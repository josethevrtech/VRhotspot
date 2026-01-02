import subprocess
import re
from typing import Dict, List, Optional, Tuple


def _run(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def _parse_iw_dev() -> List[Dict]:
    """
    Parse `iw dev` into a list of interfaces with their phy (phy0, phy1, ...).
    """
    out = _run(["iw", "dev"])
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
        out = _run(["iw", "phy", phy, "info"])
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
                mode = s.lstrip("*").strip()
                if mode == "AP":
                    return True
            elif s and not s.startswith("*"):
                # Heuristic exit: we've likely left the modes section
                in_modes = False
    return False


_FREQ_LINE_RE = re.compile(r"^\*\s+(\d+)\s+MHz\s+\[(\d+)\].*$")


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
        out = _run(["iw", "phy", phy, "info"])
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

        mhz = int(m.group(1))
        disabled = "disabled" in s.lower()

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
    out = _run(["iw", "reg", "get"])

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
    supports_ap: bool,
    reg_country: str,
    reg_source: str,
    supports_5ghz: bool,
    supports_6ghz: bool,
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

    return score, breakdown, warnings


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
        band_caps = _phy_band_support(phy) if phy else {"supports_2ghz": False, "supports_5ghz": False, "supports_6ghz": False}

        phy_reg = per_phy.get(phy, {})
        reg_country = phy_reg.get("country") or global_country or "unknown"
        reg_source = phy_reg.get("source") or "global"

        score, breakdown, warnings = _score_adapter(
            supports_ap=supports_ap,
            reg_country=reg_country,
            reg_source=reg_source,
            supports_5ghz=bool(band_caps.get("supports_5ghz")),
            supports_6ghz=bool(band_caps.get("supports_6ghz")),
        )

        item = {
            "id": ifname,
            "ifname": ifname,
            "phy": phy,
            "supports_ap": supports_ap,
            "supports_2ghz": bool(band_caps.get("supports_2ghz")),
            "supports_5ghz": bool(band_caps.get("supports_5ghz")),
            "supports_6ghz": bool(band_caps.get("supports_6ghz")),
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
