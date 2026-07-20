import os
import shutil
import subprocess
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from vr_hotspotd import host_probes


def _iw_bin() -> str:
    iw = shutil.which("iw")
    if iw:
        return iw
    for candidate in ("/usr/sbin/iw", "/usr/bin/iw"):
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("iw_not_found")


def _run(cmd: List[str]) -> str:
    result = host_probes.run_command(
        cmd,
        timeout_s=None,
        merge_stderr=True,
    )
    out = result.stdout or ""
    if result.exception is not None:
        raise result.exception
    if result.exit_status != 0:
        raise subprocess.CalledProcessError(
            result.exit_status,
            cmd,
            output=out,
        )
    return out


def _run_iw(args: List[str]) -> str:
    return _run([_iw_bin(), *args])


def _parse_iw_dev() -> List[Dict]:
    """
    Parse `iw dev` into a list of interfaces with their phy (phy0, phy1, ...).
    """
    return host_probes.parse_iw_dev_interfaces(_run_iw(["dev"]))


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
                # Preserve inventory's legacy behavior: leave this block but
                # continue scanning in case a later modes block is present.
                in_modes = False
    return False


_VIRT_IFACE_RE = re.compile(r"^x\d+.+$")


def _he_iftypes_has_ap(iw_text: str) -> Optional[bool]:
    """
    Return True if HE Iftypes explicitly includes AP/AP-VLAN.
    Return False if HE Iftypes exists but does not include AP.
    Return None if HE Iftypes is not present in the output.
    """
    return host_probes.he_iftypes_has_ap(iw_text)


def _supports_wifi6_from_iw(iw_text: str) -> bool:
    return host_probes.supports_wifi6(iw_text)


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
    
    return host_probes.supports_80mhz(out)


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
    try:
        out = _run_iw(["phy", phy, "info"])
    except Exception:
        return {
            "supports_2ghz": False,
            "supports_5ghz": False,
            "supports_6ghz": False,
        }
    return host_probes.parse_band_support(out)


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
    return host_probes.parse_regulatory_domains(_run_iw(["reg", "get"]))


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
        if ifname and _VIRT_IFACE_RE.match(ifname):
            # Skip virtual AP interfaces like x0wlan1; inventory should be physical adapters.
            continue

        supports_ap = _phy_supports_ap(phy) if phy else False
        supports_wifi6 = _phy_supports_wifi6(phy) if phy else False
        supports_80mhz = _phy_supports_80mhz(phy) if phy else False
        band_caps = _phy_band_support(phy) if phy else {"supports_2ghz": False, "supports_5ghz": False, "supports_6ghz": False}
        bus_type = _detect_bus_type(ifname) if ifname else "unknown"
        if bus_type == "virtual":
            continue

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
