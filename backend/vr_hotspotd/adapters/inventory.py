import os
import shutil
import subprocess
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple


def _iw_bin() -> str:
    iw = shutil.which("iw")
    if iw:
        return iw
    for candidate in ("/usr/sbin/iw", "/usr/bin/iw"):
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("iw_not_found")


def _run(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def _run_iw(args: List[str]) -> str:
    return _run([_iw_bin(), *args])


def _parse_iw_dev() -> List[Dict]:
    """
    Parse `iw dev` into a list of interfaces with their phy (phy0, phy1, ...).
    """
    out = _run_iw(["dev"])
    items: List[Dict] = []
    current_phy: Optional[str] = None

    for line in out.splitlines():
        s = line.strip()
        if s.startswith("phy#"):
            n = s.split("#", 1)[1].strip()
            current_phy = f"phy{n}"
        elif s.startswith("Interface") and current_phy:
            ifname = s.split()[1].strip()
            items.append({"ifname": ifname, "phy": current_phy})

    return items


def _phy_supports_ap(phy: str) -> bool:
    """
    Parse `iw phy <phy> info` and look for 'Supported interface modes' containing '* AP'.
    """
    try:
        out = _run_iw(["phy", phy, "info"])
    except Exception:
        return False

    in_modes = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Supported interface modes"):
            in_modes = True
            continue
        if in_modes:
            if s.startswith("*"):
                mode = s.lstrip("*").strip().upper()
                if mode == "AP" or mode.startswith("AP/") or mode.startswith("AP-"):
                    return True
            elif s and not s.startswith("*"):
                # Heuristic exit: we've likely left the modes section
                in_modes = False
    return False


_HE_IFTYPES_RE = re.compile(r"^\s*HE Iftypes:\s*(.+)$", re.IGNORECASE)


def _he_iftypes_has_ap(iw_text: str) -> Optional[bool]:
    """
    Return True if HE Iftypes explicitly includes AP/AP-VLAN.
    Return False if HE Iftypes exists but does not include AP.
    Return None if HE Iftypes is not present in the output.
    """
    seen = False
    for raw in iw_text.splitlines():
        m = _HE_IFTYPES_RE.search(raw)
        if not m:
            continue
        seen = True
        for token in m.group(1).split(","):
            t = token.strip().upper()
            if t in ("AP", "AP/VLAN", "AP-VLAN"):
                return True
    return False if seen else None


def _supports_wifi6_from_iw(iw_text: str) -> bool:
    he_ap = _he_iftypes_has_ap(iw_text)
    if he_ap is True:
        return True
    if he_ap is False:
        return False

    s = iw_text.lower()
    return ("802.11ax" in s) or ("he capabilities" in s)


@lru_cache(maxsize=64)
def _phy_supports_wifi6(phy: str) -> bool:
    """
    Parse `iw phy <phy> info` and look for HE (802.11ax) AP support markers.
    """
    try:
        out = _run_iw(["phy", phy, "info"])
    except Exception:
        return False

    return _supports_wifi6_from_iw(out)


@lru_cache(maxsize=64)
def _phy_supports_80mhz(phy: str) -> bool:
    """
    Parse `iw phy <phy> info` and look for VHT or HE capabilities implying 80MHz support.
    """
    try:
        out = _run_iw(["phy", phy, "info"])
    except Exception:
        return False
    
    # 1. Check HE (Wi-Fi 6) - HE80
    if re.search(r"HE40/HE80/5GHz", out, re.IGNORECASE):
        return True
    
    # 2. Check VHT (Wi-Fi 5)
    vht_section = re.search(r"VHT Capabilities \(.*?\):(.*?)(?:\n\s*[A-Za-z]|\Z)", out, re.DOTALL | re.IGNORECASE)
    if vht_section:
        content = vht_section.group(1)
        width_line = re.search(r"Supported Channel Width:(.*)", content, re.IGNORECASE)
        if width_line:
            val = width_line.group(1).strip().lower()
            if "160" in val or "neither 160 nor 80+80" in val:
                return True
            if "20/40" in val:
                return False
        # Default VHT usually implies 80MHz support if not explicitly restricted
        return True
        
    return False


_FREQ_LINE_RE = re.compile(r"^\s*\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\].*$")


def _phy_band_support(phy: str) -> Dict[str, bool]:
    """
    Parse `iw phy <phy> info` and infer whether the phy supports 2.4/5/6 GHz.

    Heuristic:
      - Look at frequency lines inside each "Band X:" -> "Frequencies:" list.
      - If we find at least one *enabled* freq in the range, we mark support True.

    2.4 GHz: 2400-2500 MHz
    5 GHz:   4900-5900 MHz
    6 GHz:   5925-7125 MHz (covers 6E ranges; some outputs show 5955+)
    """
    supports = {"supports_2ghz": False, "supports_5ghz": False, "supports_6ghz": False}

    try:
        out = _run_iw(["phy", phy, "info"])
    except Exception:
        return supports

    in_freqs = False
    for raw in out.splitlines():
        s = raw.strip()

        if s.startswith("Frequencies:"):
            in_freqs = True
            continue

        # Exiting frequencies list (next section header)
        if in_freqs and s and not s.startswith("*"):
            in_freqs = False

        if not in_freqs:
            continue

        m = _FREQ_LINE_RE.match(s)
        if not m:
            continue

        try:
            mhz = int(float(m.group(1)))
        except Exception:
            continue
        line_lower = s.lower()
        disabled = ("disabled" in line_lower) or ("no ir" in line_lower) or ("no-ir" in line_lower)

        if disabled:
            continue

        if 2400 <= mhz <= 2500:
            supports["supports_2ghz"] = True
        elif 4900 <= mhz <= 5900:
            supports["supports_5ghz"] = True
        elif 5925 <= mhz <= 7125:
            supports["supports_6ghz"] = True

    return supports


def _parse_iw_reg_get() -> Dict:
    """
    Parse `iw reg get` to extract:
      - global country code
      - per-phy overrides (including 'self-managed' marker)
    Returns:
      {
        "global": {"country": "00", "raw_header": "country 00: ..."},
        "phys": {
          "phy0": {"country": "US", "source": "self-managed", "raw_header": "country US: ..."},
          "phy1": {"country": "00", "source": "global", "raw_header": None}
        }
      }
    """
    out = _run_iw(["reg", "get"])

    global_country: Optional[str] = None
    global_header: Optional[str] = None
    phys: Dict[str, Dict] = {}

    current_section = "global"
    current_phy: Optional[str] = None
    current_phy_source: str = "unknown"

    for line in out.splitlines():
        s = line.strip()

        # start of a phy section: "phy#0 (self-managed)"
        if s.startswith("phy#"):
            current_section = "phy"
            # phy#0 -> phy0
            phy_num = s.split()[0].split("#", 1)[1]
            current_phy = f"phy{phy_num}"
            current_phy_source = "self-managed" if "self-managed" in s else "kernel-managed"
            if current_phy not in phys:
                phys[current_phy] = {"country": None, "source": current_phy_source, "raw_header": None}
            else:
                phys[current_phy]["source"] = current_phy_source
            continue

        # country line applies to whichever section we're in
        if s.startswith("country "):
            parts = s.split()
            cc = parts[1].rstrip(":") if len(parts) >= 2 else None

            if current_section == "global":
                global_country = cc
                global_header = s
            elif current_section == "phy" and current_phy:
                phys.setdefault(current_phy, {})
                phys[current_phy]["country"] = cc
                phys[current_phy]["raw_header"] = s
                phys[current_phy].setdefault("source", current_phy_source)

    return {
        "global": {"country": global_country or "unknown", "raw_header": global_header},
        "phys": phys
    }


def _score_adapter(
    *,
    ifname: str,
    supports_ap: bool,
    reg_country: str,
    reg_source: str,
    supports_5ghz: bool,
    supports_6ghz: bool,
    supports_80mhz: bool,
) -> Tuple[int, List[Dict], List[str]]:
    """
    Capability-based scoring (chipset-agnostic).
    Returns: (score, breakdown[], warnings[])
    """
    score = 0
    breakdown: List[Dict] = []
    warnings: List[str] = []

    if supports_ap:
        score += 50
        breakdown.append({"points": 50, "reason": "supports_ap_mode"})
    else:
        breakdown.append({"points": 0, "reason": "no_ap_mode"})
        warnings.append("no_ap_mode")

    # Prefer broader spectrum support (helps recommendation UX)
    if supports_6ghz:
        score += 15
        breakdown.append({"points": 15, "reason": "supports_6ghz"})
    if supports_5ghz:
        score += 10
        breakdown.append({"points": 10, "reason": "supports_5ghz"})
    
    if supports_80mhz:
        score += 20
        breakdown.append({"points": 20, "reason": "supports_80mhz_bandwidth"})
    elif supports_5ghz:
         # Penalize 5GHz adapters that don't support 80MHz (production readiness)
         warnings.append("adapter_lacks_80mhz_bandwidth_support")

    # Regulatory domain considerations
    if reg_source == "self-managed":
        score += 20
        breakdown.append({"points": 20, "reason": "phy_self_managed_regdom"})
    elif reg_source in ("kernel-managed", "unknown"):
        breakdown.append({"points": 0, "reason": "phy_not_self_managed"})

    if reg_country and reg_country != "00" and reg_country != "unknown":
        score += 10
        breakdown.append({"points": 10, "reason": "regdom_not_global_00"})
    else:
        breakdown.append({"points": 0, "reason": "regdom_global_or_unknown"})
        warnings.append("regdom_global_or_unknown_may_limit_ap_or_5ghz_or_6ghz")

    # Deprioritize wlan0 - known issues with AP mode on some systems (Intel AX200, NetworkManager conflicts)
    # Prefer wlan1+ which are typically USB adapters with better AP mode support
    if ifname == "wlan0":
        score -= 30
        breakdown.append({"points": -30, "reason": "wlan0_deprioritized_known_ap_mode_issues"})
        warnings.append("wlan0_has_known_ap_mode_issues_on_some_systems")

    return score, breakdown, warnings


def _detect_bus_type(ifname: str) -> str:
    """
    Detect if adapter is 'usb', 'pci', or 'unknown' via sysfs.
    """
    try:
        path = f"/sys/class/net/{ifname}/device"
        if not os.path.exists(path):
            return "unknown"
        real = os.path.realpath(path)
        if "/usb" in real:
            return "usb"
        if "/pci" in real:
            return "pci"
        if "/virtual" in real:
            return "virtual"
    except Exception:
        pass
    return "unknown"



def get_adapters():
    """
    Returns:
      - adapters: inventory with AP support + band support + regdom + score + explanation
      - recommended: best AP-capable adapter by score
      - global_regdom: global country
    """
    try:
        devs = _parse_iw_dev()
    except Exception as e:
        return {"error": str(e), "adapters": [], "recommended": None, "global_regdom": None}

    try:
        reg = _parse_iw_reg_get()
    except Exception:
        reg = {"global": {"country": "unknown", "raw_header": None}, "phys": {}}

    global_country = reg.get("global", {}).get("country", "unknown")
    per_phy = reg.get("phys", {})

    enriched: List[Dict] = []
    recommended: Optional[str] = None
    best_score: Optional[int] = None

    for d in devs:
        phy = d.get("phy")
        ifname = d.get("ifname")

        supports_ap = _phy_supports_ap(phy) if phy else False
        supports_wifi6 = _phy_supports_wifi6(phy) if phy else False
        supports_80mhz = _phy_supports_80mhz(phy) if phy else False
        band_caps = _phy_band_support(phy) if phy else {"supports_2ghz": False, "supports_5ghz": False, "supports_6ghz": False}
        bus_type = _detect_bus_type(ifname) if ifname else "unknown"

        phy_reg = per_phy.get(phy, {})
        reg_country = phy_reg.get("country") or global_country or "unknown"
        reg_source = phy_reg.get("source") or "global"

        score, breakdown, warnings = _score_adapter(
            ifname=ifname,
            supports_ap=supports_ap,
            reg_country=reg_country,
            reg_source=reg_source,
            supports_5ghz=bool(band_caps.get("supports_5ghz")),
            supports_6ghz=bool(band_caps.get("supports_6ghz")),
            supports_80mhz=supports_80mhz,
        )

        item = {
            "id": ifname,
            "ifname": ifname,
            "phy": phy,
            "bus": bus_type,
            "supports_ap": supports_ap,
            "supports_wifi6": supports_wifi6,
            "supports_2ghz": bool(band_caps.get("supports_2ghz")),
            "supports_5ghz": bool(band_caps.get("supports_5ghz")),
            "supports_6ghz": bool(band_caps.get("supports_6ghz")),
            "supports_80mhz": supports_80mhz,
            "regdom": {
                "country": reg_country,
                "source": reg_source,
                "global_country": global_country,
                "raw_phy": phy_reg.get("raw_header"),
                "raw_global": reg.get("global", {}).get("raw_header"),
            },
            "score": score,
            "score_breakdown": breakdown,
            "warnings": warnings,
        }
        enriched.append(item)

        if supports_ap:
            if best_score is None or score > best_score:
                best_score = score
                recommended = ifname

    return {
        "global_regdom": {"country": global_country, "raw": reg.get("global", {}).get("raw_header")},
        "recommended": recommended,
        "adapters": enriched,
        "notes": [
            "Selection is capability-based (AP mode + band support + regulatory context), not chipset-based.",
            "supports_6ghz is inferred from enabled 6 GHz frequencies in `iw phy <phy> info`.",
        ],
    }
