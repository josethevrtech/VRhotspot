from typing import Any, Dict, Iterable, List, Optional, Tuple


READINESS_EXCELLENT = "excellent_for_vr"
READINESS_GOOD = "good_for_vr"
READINESS_USABLE = "usable_with_limitations"
READINESS_NOT_RECOMMENDED = "not_recommended"
READINESS_UNSUPPORTED = "unsupported"

SIX_GHZ_SUPPORTED = "supported"
SIX_GHZ_BLOCKED_REGDOMAIN = "blocked_by_regdomain"
SIX_GHZ_BLOCKED_DRIVER = "blocked_by_driver"
SIX_GHZ_BLOCKED_HOSTAPD = "blocked_by_hostapd"
SIX_GHZ_UNKNOWN = "unknown"
SIX_GHZ_NOT_SUPPORTED = "not_supported"


def build_readiness_model(inventory: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build Adapter Intelligence v2 readiness data from an existing inventory dict.

    This function is intentionally pure. It only consumes dictionaries that callers
    provide and does not probe sysfs, run iw, or inspect host state.
    """
    adapters_in = inventory.get("adapters") if isinstance(inventory, dict) else []
    adapters = list(adapters_in) if isinstance(adapters_in, list) else []
    global_regdom = _normalize_global_regdom(inventory.get("global_regdom") if isinstance(inventory, dict) else None)

    if not adapters:
        return {
            "recommended": None,
            "basic_mode_recommended": None,
            "adapters": [],
            "global_regulatory_domain": global_regdom,
            "summary": {
                "readiness_state": READINESS_UNSUPPORTED,
                "six_ghz_state": SIX_GHZ_UNKNOWN,
                "recommendation_score": 0,
                "reason_codes": ["no_adapter_found"],
                "explanation": (
                    "No physical Wi-Fi adapter was found. Connect a USB Wi-Fi adapter "
                    "that supports AP mode, 5 GHz, and 80 MHz channels."
                ),
            },
        }

    has_external_ap = any(
        _boolish(a.get("supports_ap", a.get("supports_ap_mode"))) and _adapter_bus(a) == "usb"
        for a in adapters
        if isinstance(a, dict)
    )

    scored = [_score_adapter(adapter, global_regdom, has_external_ap) for adapter in adapters if isinstance(adapter, dict)]
    ranked = sorted(
        scored,
        key=lambda a: (
            a["readiness_state"] == READINESS_UNSUPPORTED,
            -a["recommendation_score"],
            a["interface"],
        ),
    )
    basic_ranked = [a for a in ranked if a["basic_mode_visibility"]["selectable"]]
    for idx, adapter in enumerate(basic_ranked, start=1):
        adapter["basic_mode_visibility"]["rank"] = idx

    recommended = _recommended_interface(ranked)
    basic_recommended = basic_ranked[0]["interface"] if basic_ranked else None

    return {
        "recommended": recommended,
        "basic_mode_recommended": basic_recommended,
        "adapters": ranked,
        "global_regulatory_domain": global_regdom,
        "notes": [
            "Adapter readiness is derived from supplied inventory facts only.",
            "This model is pure and does not call system commands.",
        ],
    }


def _score_adapter(adapter: Dict[str, Any], global_regdom: Dict[str, Any], has_external_ap: bool) -> Dict[str, Any]:
    ifname = str(adapter.get("ifname") or adapter.get("interface") or adapter.get("id") or "unknown")
    driver = str(adapter.get("driver") or "unknown")
    bus_type = _adapter_bus(adapter)
    supports_ap = _maybe_bool(adapter.get("supports_ap", adapter.get("supports_ap_mode")))
    supports_2ghz = _boolish(adapter.get("supports_2ghz"))
    supports_5ghz = _boolish(adapter.get("supports_5ghz"))
    supports_6ghz = _boolish(adapter.get("supports_6ghz"))
    supports_wifi6 = _boolish(adapter.get("supports_wifi6"))
    supports_80mhz = _boolish(adapter.get("supports_80mhz"))
    supports_160mhz = _boolish(adapter.get("supports_160mhz"))
    regdom = _normalize_regdom(adapter.get("regdom") or adapter.get("regulatory_domain"), global_regdom)
    six_ghz_state = _six_ghz_state(adapter, supports_ap, supports_6ghz, supports_wifi6, regdom)

    score, reason_codes = _base_score(
        ifname=ifname,
        bus_type=bus_type,
        supports_ap=supports_ap,
        supports_2ghz=supports_2ghz,
        supports_5ghz=supports_5ghz,
        supports_6ghz=supports_6ghz,
        supports_wifi6=supports_wifi6,
        supports_80mhz=supports_80mhz,
        supports_160mhz=supports_160mhz,
        regdom=regdom,
        six_ghz_state=six_ghz_state,
        has_external_ap=has_external_ap,
    )
    basic_mode_visibility = _basic_mode_visibility(
        ifname=ifname,
        supports_ap=supports_ap,
        supports_5ghz=supports_5ghz,
        supports_80mhz=supports_80mhz,
        regdom=regdom,
        has_external_ap=has_external_ap,
    )
    reason_codes.append("basic_mode_visible" if basic_mode_visibility["visible"] else "basic_mode_hidden")
    readiness_state = _readiness_state(
        ifname=ifname,
        bus_type=bus_type,
        supports_ap=supports_ap,
        supports_2ghz=supports_2ghz,
        supports_5ghz=supports_5ghz,
        supports_80mhz=supports_80mhz,
        regdom=regdom,
        six_ghz_state=six_ghz_state,
        has_external_ap=has_external_ap,
    )

    return {
        "interface": ifname,
        "driver": driver,
        "bus_type": bus_type,
        "chipset_vendor_guess": _chipset_vendor_guess(driver),
        "supports_ap_mode": supports_ap,
        "supports_2ghz": supports_2ghz,
        "supports_5ghz": supports_5ghz,
        "supports_6ghz": supports_6ghz,
        "regulatory_domain": regdom,
        "channel_width_hints": {
            "supports_20mhz": supports_2ghz or supports_5ghz or supports_6ghz,
            "supports_40mhz": supports_2ghz or supports_5ghz,
            "supports_80mhz": supports_80mhz,
            "supports_160mhz": supports_160mhz,
            "best_vr_width_mhz": 160 if supports_160mhz else (80 if supports_80mhz else (40 if supports_5ghz else 20)),
            "evidence": _width_evidence(adapter, supports_80mhz, supports_160mhz, supports_6ghz),
        },
        "basic_mode_visibility": basic_mode_visibility,
        "readiness_state": readiness_state,
        "six_ghz_state": six_ghz_state,
        "recommendation_score": _clamp(score, 0, 100),
        "reason_codes": _unique(reason_codes),
        "explanation": _explanation(ifname, readiness_state, six_ghz_state, basic_mode_visibility),
    }


def _base_score(
    *,
    ifname: str,
    bus_type: str,
    supports_ap: Optional[bool],
    supports_2ghz: bool,
    supports_5ghz: bool,
    supports_6ghz: bool,
    supports_wifi6: bool,
    supports_80mhz: bool,
    supports_160mhz: bool,
    regdom: Dict[str, Any],
    six_ghz_state: str,
    has_external_ap: bool,
) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    if supports_ap is True:
        score += 35
        reasons.append("supports_ap_mode")
    elif supports_ap is False:
        score -= 40
        reasons.append("missing_ap_mode")
    else:
        reasons.append("ap_mode_unknown")

    if bus_type == "usb":
        score += 8
        reasons.append("usb_adapter")
    else:
        reasons.append("pci_or_internal_adapter")

    if ifname == "wlan0" and has_external_ap:
        score -= 20
        reasons.append("wlan0_deprioritized")

    if supports_2ghz:
        reasons.append("supports_2ghz")
    if supports_5ghz:
        score += 20
        reasons.append("supports_5ghz")
    if supports_6ghz:
        reasons.append("supports_6ghz")
    if supports_wifi6:
        reasons.append("wifi6_supported")
    else:
        reasons.append("wifi6_not_supported_on_adapter")
    if six_ghz_state == SIX_GHZ_SUPPORTED:
        score += 12

    if supports_80mhz:
        score += 18
        reasons.append("supports_80mhz")
    elif supports_5ghz:
        score -= 25
        reasons.append("missing_80mhz")

    if supports_160mhz:
        score += 5

    if regdom["status"] in ("valid", "self_managed"):
        score += 6
        reasons.append("regdom_valid")
    else:
        score -= 30
        reasons.append("regdom_global_or_unknown")

    if regdom.get("self_managed"):
        score += 4

    if six_ghz_state == SIX_GHZ_BLOCKED_REGDOMAIN:
        reasons.append("regdom_no_ir_blocks_6ghz")
    elif six_ghz_state == SIX_GHZ_BLOCKED_HOSTAPD:
        reasons.append("hostapd_6ghz_not_available")

    if supports_ap is not True or not (supports_2ghz or supports_5ghz or supports_6ghz):
        score = min(score, 20)

    return score, reasons


def _readiness_state(
    *,
    ifname: str,
    bus_type: str,
    supports_ap: Optional[bool],
    supports_2ghz: bool,
    supports_5ghz: bool,
    supports_80mhz: bool,
    regdom: Dict[str, Any],
    six_ghz_state: str,
    has_external_ap: bool,
) -> str:
    if supports_ap is not True or not (supports_2ghz or supports_5ghz):
        return READINESS_UNSUPPORTED
    if ifname == "wlan0" and has_external_ap:
        return READINESS_NOT_RECOMMENDED
    if supports_5ghz and not supports_80mhz:
        return READINESS_NOT_RECOMMENDED
    if regdom["status"] == "global_or_unknown" and not supports_5ghz:
        return READINESS_NOT_RECOMMENDED
    if supports_5ghz and supports_80mhz and bus_type == "usb" and six_ghz_state == SIX_GHZ_SUPPORTED:
        return READINESS_EXCELLENT
    if supports_5ghz and supports_80mhz and six_ghz_state == SIX_GHZ_BLOCKED_REGDOMAIN:
        return READINESS_GOOD
    if supports_5ghz and supports_80mhz and regdom["status"] in ("valid", "self_managed", "no_ir_blocked"):
        return READINESS_GOOD
    return READINESS_USABLE


def _six_ghz_state(
    adapter: Dict[str, Any],
    supports_ap: Optional[bool],
    supports_6ghz: bool,
    supports_wifi6: bool,
    regdom: Dict[str, Any],
) -> str:
    explicit = adapter.get("six_ghz_state")
    if explicit in {
        SIX_GHZ_SUPPORTED,
        SIX_GHZ_BLOCKED_REGDOMAIN,
        SIX_GHZ_BLOCKED_DRIVER,
        SIX_GHZ_BLOCKED_HOSTAPD,
        SIX_GHZ_UNKNOWN,
        SIX_GHZ_NOT_SUPPORTED,
    }:
        return explicit

    if not supports_6ghz and not _boolish(adapter.get("has_6ghz_frequencies")):
        return SIX_GHZ_NOT_SUPPORTED
    if supports_ap is not True or not supports_wifi6:
        return SIX_GHZ_BLOCKED_DRIVER
    if regdom["status"] in ("global_or_unknown", "no_ir_blocked"):
        return SIX_GHZ_BLOCKED_REGDOMAIN
    hostapd_6ghz = adapter.get("hostapd_6ghz_capable")
    if hostapd_6ghz is False:
        return SIX_GHZ_BLOCKED_HOSTAPD
    if regdom["status"] in ("valid", "self_managed"):
        return SIX_GHZ_SUPPORTED
    return SIX_GHZ_UNKNOWN


def _basic_mode_visibility(
    *,
    ifname: str,
    supports_ap: Optional[bool],
    supports_5ghz: bool,
    supports_80mhz: bool,
    regdom: Dict[str, Any],
    has_external_ap: bool,
) -> Dict[str, Any]:
    if supports_ap is not True:
        return {"visible": False, "selectable": False, "rank": 99, "reason": "missing_ap_mode"}
    if ifname == "wlan0" and has_external_ap:
        return {"visible": False, "selectable": False, "rank": 99, "reason": "internal_deprioritized"}
    if not supports_5ghz:
        return {"visible": False, "selectable": False, "rank": 99, "reason": "missing_5ghz"}
    if not supports_80mhz:
        return {"visible": False, "selectable": False, "rank": 99, "reason": "missing_80mhz"}
    if regdom["status"] == "global_or_unknown":
        return {"visible": False, "selectable": False, "rank": 99, "reason": "regdomain_blocks_required_band"}
    return {"visible": True, "selectable": True, "rank": 99, "reason": "usb_5ghz_80mhz_ap"}


def _normalize_regdom(regdom: Any, global_regdom: Dict[str, Any]) -> Dict[str, Any]:
    reg = regdom if isinstance(regdom, dict) else {}
    country = str(reg.get("country") or global_regdom.get("country") or "unknown")
    source = str(reg.get("source") or "global")
    global_country = str(reg.get("global_country") or global_regdom.get("country") or "unknown")
    self_managed = source == "self-managed" or _boolish(reg.get("self_managed"))
    raw = " ".join(str(reg.get(k) or "") for k in ("raw_phy", "raw_global", "raw")).lower()

    if self_managed and country not in ("00", "unknown", ""):
        status = "self_managed"
    elif country in ("00", "unknown", ""):
        status = "global_or_unknown"
    elif "no ir" in raw or "no-ir" in raw or reg.get("status") == "no_ir_blocked":
        status = "no_ir_blocked"
    else:
        status = str(reg.get("status") or "valid")

    return {
        "status": status,
        "country": country,
        "source": source,
        "global_country": global_country,
        "self_managed": self_managed,
    }


def _normalize_global_regdom(regdom: Any) -> Dict[str, Any]:
    reg = regdom if isinstance(regdom, dict) else {}
    country = str(reg.get("country") or "unknown")
    status = "valid" if country not in ("00", "unknown", "") else "global_or_unknown"
    return {
        "status": status,
        "country": country,
        "source": str(reg.get("source") or "global"),
        "raw": reg.get("raw"),
    }


def _chipset_vendor_guess(driver: str) -> Dict[str, str]:
    d = driver.lower()
    if d.startswith("mt"):
        return {"vendor": "MediaTek", "chipset": driver, "source": "driver"}
    if d == "iwlwifi":
        return {"vendor": "Intel", "chipset": "Intel Wi-Fi family", "source": "driver"}
    if d.startswith("rtl"):
        return {"vendor": "Realtek", "chipset": driver, "source": "driver"}
    return {"vendor": "unknown", "chipset": "unknown", "source": "unavailable"}


def _width_evidence(adapter: Dict[str, Any], supports_80mhz: bool, supports_160mhz: bool, supports_6ghz: bool) -> List[str]:
    evidence = list(adapter.get("channel_width_evidence") or [])
    if supports_80mhz and "vht80" not in evidence:
        evidence.append("vht80")
    if supports_160mhz and "he160" not in evidence:
        evidence.append("he160")
    if supports_6ghz and "6ghz_frequencies_present" not in evidence:
        evidence.append("6ghz_frequencies_present")
    return evidence


def _explanation(ifname: str, readiness_state: str, six_ghz_state: str, visibility: Dict[str, Any]) -> str:
    if readiness_state == READINESS_UNSUPPORTED:
        return f"{ifname} is unsupported for VR hotspot use because required AP or channel support is missing."
    if readiness_state == READINESS_NOT_RECOMMENDED and visibility["reason"] == "internal_deprioritized":
        return f"{ifname} is AP-capable, but it is deprioritized while an external AP-capable adapter is available."
    if readiness_state == READINESS_NOT_RECOMMENDED:
        return f"{ifname} is not recommended for VR hotspot use because it is missing a required readiness signal."
    if six_ghz_state == SIX_GHZ_BLOCKED_REGDOMAIN:
        return f"{ifname} is a strong 5 GHz VR adapter, but 6 GHz is blocked by the current regulatory domain."
    if readiness_state == READINESS_EXCELLENT:
        return f"{ifname} is excellent for VR hotspot use with AP mode, wide channels, and usable 6 GHz readiness."
    if readiness_state == READINESS_GOOD:
        return f"{ifname} supports AP mode, 5 GHz, and 80 MHz channels for VR hotspot use."
    return f"{ifname} can be used for hotspot mode, but it has limitations for VR."


def _recommended_interface(adapters: Iterable[Dict[str, Any]]) -> Optional[str]:
    for adapter in adapters:
        if adapter["readiness_state"] != READINESS_UNSUPPORTED:
            return adapter["interface"]
    return None


def _adapter_bus(adapter: Dict[str, Any]) -> str:
    bus = adapter.get("bus_type", adapter.get("bus"))
    if bus in ("usb", "pci", "sdio", "platform", "virtual"):
        return str(bus)
    return "unknown"


def _maybe_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return _boolish(value)


def _boolish(value: Any) -> bool:
    return bool(value)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
