from __future__ import annotations

import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd import host_probes, os_release
from vr_hotspotd.adapters.inventory import get_adapters

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

ERROR_REMEDIATIONS: Dict[str, str] = {
    "regdom_unknown_or_global_00": (
        "Set a valid 2-letter country code (config `country`) and ensure `iw reg get` "
        "no longer reports 00; replug the adapter or reboot if needed."
    ),
    "driver_no_ap_mode_5ghz": (
        "Use an adapter/driver that supports AP mode on 5GHz; update driver/firmware if available."
    ),
    "driver_no_vht80_or_he80": (
        "Adapter/driver lacks VHT80/HE80 support. Try a different adapter or enable "
        "Pro Mode `allow_fallback_40mhz`."
    ),
    "non_dfs_80mhz_channels_unavailable": (
        "No non-DFS 80MHz channel blocks are allowed. Set a valid country/regdom or enable DFS use."
    ),
    "dfs_required_but_disabled": (
        "Only DFS 80MHz blocks are available. Enable DFS channels explicitly or choose a different "
        "regulatory domain."
    ),
    "hostapd_failed": (
        "Hostapd/lnxrouter failed to start. Check logs and verify adapter/hostapd compatibility."
    ),
    "hostapd_started_but_width_not_80": (
        "AP started but channel width is not 80MHz. Ensure 80MHz support, update driver, "
        "or enable Pro Mode `allow_fallback_40mhz`."
    ),
    "ap_start_timed_out": (
        "AP did not become ready in time. Check logs, regdom, and reduce interference."
    ),
    "nm_interference_detected": (
        "NetworkManager is managing the AP interface. Set it unmanaged or stop NM."
    ),
    "basic_mode_requires_5ghz": (
        "Basic Mode requires the 5 GHz band for VR streaming. "
        "Switch to Advanced Mode to use other bands."
    ),
    "basic_mode_requires_80mhz_adapter": (
        "Basic Mode requires an adapter with 80MHz (VHT80/HE80) support for VR. "
        "Use a compatible USB Wi-Fi adapter or switch to Advanced Mode."
    ),
    "nm_interface_managed": (
        "NetworkManager is controlling this interface. "
        "Run: nmcli dev set <interface> managed no"
    ),
    "ap_adapter_still_associated_iwd_autoconnect": (
        "Disable iwd autoconnect for the AP adapter, or use Ethernet/internal Wi-Fi as upstream "
        "and reserve USB Wi-Fi for AP."
    ),
}


def build_error_detail(code: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "remediation": ERROR_REMEDIATIONS.get(code, "Check logs for details."),
        "context": context or {},
    }


def _run(cmd: List[str], timeout_s: float = 2.0) -> Tuple[int, str]:
    result = host_probes.run_command(cmd, timeout_s=timeout_s)
    return result.exit_status, result.combined_output()


def _iw_bin() -> str:
    iw = shutil.which("iw")
    if iw:
        return iw
    for candidate in ("/usr/sbin/iw", "/usr/bin/iw"):
        if candidate and shutil.which(candidate):
            return candidate
    return "/usr/sbin/iw"


def _run_iw_list() -> Tuple[int, str]:
    return _run([_iw_bin(), "list"], timeout_s=3.0)


def _run_iw_reg_get() -> Tuple[int, str]:
    return _run([_iw_bin(), "reg", "get"], timeout_s=2.0)


def detect_os_flavor(info: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    info = info or os_release.read_os_release()
    return host_probes.classify_os_flavor(info)


def detect_firewall_backends() -> Dict[str, Any]:
    return host_probes.probe_firewall_backends()


def detect_network_manager() -> Dict[str, Any]:
    return host_probes.probe_network_manager()


def _split_wiphy_sections(text: str) -> Dict[str, str]:
    return host_probes.split_wiphy_sections(text)


def _parse_supported_interface_modes(text: str) -> Optional[bool]:
    return host_probes.supports_ap_mode(text)


def _parse_5ghz_channels(text: str) -> List[Dict[str, Any]]:
    return host_probes.parse_5ghz_channels(text)


def _parse_vht_supports_80(text: str) -> Optional[bool]:
    return host_probes.parse_vht_supports_80(text)


def _parse_he_supports_80(text: str) -> Optional[bool]:
    return host_probes.parse_he_supports_80(text)


def _parse_iw_reg_get(text: str) -> Dict[str, Any]:
    return host_probes.parse_regulatory_domains(text)


def _effective_country(config_country: Optional[str], reg_country: Optional[str]) -> Optional[str]:
    if isinstance(config_country, str):
        cc = config_country.strip().upper()
        if _COUNTRY_RE.match(cc) and cc != "00":
            return cc
    if isinstance(reg_country, str):
        cc = reg_country.strip().upper()
        if _COUNTRY_RE.match(cc) and cc != "00":
            return cc
    return None


_BLOCKS_80 = (
    (36, 48, 42),
    (52, 64, 58),
    (100, 112, 106),
    (116, 128, 122),
    (132, 144, 138),
    (149, 161, 155),
)


def _build_80mhz_candidates(
    channels: List[Dict[str, Any]],
    *,
    allow_dfs: bool,
    preferred_primary_channel: Optional[int],
    country: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    available = {
        c["channel"]: c
        for c in channels
        if not c.get("disabled") and not c.get("no_ir")
    }
    candidates: List[Dict[str, Any]] = []
    for start, end, center in _BLOCKS_80:
        block_channels = [c for c in range(start, end + 1, 4)]
        if any(ch not in available for ch in block_channels):
            continue
        dfs = any(available[ch].get("dfs") for ch in block_channels)
        flags = ["dfs"] if dfs else ["non_dfs"]
        primary = block_channels[0]
        if preferred_primary_channel in block_channels:
            primary = int(preferred_primary_channel)
        candidates.append(
            {
                "band": 5,
                "width": 80,
                "primary_channel": primary,
                "center_channel": center,
                "country": country,
                "flags": flags,
                "rationale": f"{'dfs' if dfs else 'non_dfs'}_block_{start}_{end}",
            }
        )

    non_dfs = [c for c in candidates if "dfs" not in c.get("flags", [])]
    dfs = [c for c in candidates if "dfs" in c.get("flags", [])]

    if preferred_primary_channel:
        def _pref_key(cand: Dict[str, Any]) -> Tuple[int, int]:
            if cand["primary_channel"] == preferred_primary_channel:
                return (0, cand["primary_channel"])
            return (1, cand["primary_channel"])

        non_dfs.sort(key=_pref_key)
        dfs.sort(key=_pref_key)
    else:
        non_dfs.sort(key=lambda c: c["primary_channel"])
        dfs.sort(key=lambda c: c["primary_channel"])

    if allow_dfs:
        return non_dfs + dfs, {"non_dfs": len(non_dfs), "dfs": len(dfs)}
    return non_dfs, {"non_dfs": len(non_dfs), "dfs": len(dfs)}


def _build_40mhz_candidates(
    channels: List[Dict[str, Any]],
    *,
    allow_dfs: bool,
    preferred_primary_channel: Optional[int],
    country: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    available = {
        c["channel"]: c
        for c in channels
        if not c.get("disabled") and not c.get("no_ir")
    }
    candidates: List[Dict[str, Any]] = []
    for primary in sorted(available.keys()):
        secondary = primary + 4
        if secondary not in available:
            continue
        dfs = bool(available[primary].get("dfs") or available[secondary].get("dfs"))
        flags = ["dfs"] if dfs else ["non_dfs"]
        center = primary + 2
        candidates.append(
            {
                "band": 5,
                "width": 40,
                "primary_channel": primary,
                "center_channel": center,
                "country": country,
                "flags": flags,
                "rationale": f"{'dfs' if dfs else 'non_dfs'}_pair_{primary}_{secondary}",
            }
        )

    non_dfs = [c for c in candidates if "dfs" not in c.get("flags", [])]
    dfs = [c for c in candidates if "dfs" in c.get("flags", [])]

    if preferred_primary_channel:
        def _pref_key(cand: Dict[str, Any]) -> Tuple[int, int]:
            if cand["primary_channel"] == preferred_primary_channel:
                return (0, cand["primary_channel"])
            return (1, cand["primary_channel"])

        non_dfs.sort(key=_pref_key)
        dfs.sort(key=_pref_key)
    else:
        non_dfs.sort(key=lambda c: c["primary_channel"])
        dfs.sort(key=lambda c: c["primary_channel"])

    if allow_dfs:
        return non_dfs + dfs, {"non_dfs": len(non_dfs), "dfs": len(dfs)}
    return non_dfs, {"non_dfs": len(non_dfs), "dfs": len(dfs)}


def probe_5ghz_80(
    ap_ifname: str,
    *,
    inventory: Optional[Dict[str, Any]] = None,
    country: Optional[str] = None,
    allow_dfs: bool = False,
    preferred_primary_channel: Optional[int] = None,
) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []

    inv = inventory if isinstance(inventory, dict) else get_adapters()
    adapter = None
    for item in inv.get("adapters", []):
        if item.get("ifname") == ap_ifname:
            adapter = item
            break
    phy = adapter.get("phy") if isinstance(adapter, dict) else None

    rc, iw_list = _run_iw_list()
    if rc != 0 or not iw_list:
        warnings.append("iw_list_failed")
        iw_list = ""
    sections = _split_wiphy_sections(iw_list)
    section = sections.get(phy) if phy else None

    ap_supported = _parse_supported_interface_modes(section or "")
    channels_5g = _parse_5ghz_channels(section or "")
    vht80 = _parse_vht_supports_80(section or "")
    he80 = _parse_he_supports_80(section or "")
    supports_80 = bool(he80 is True or vht80 is True)

    rc_reg, reg_text = _run_iw_reg_get()
    reg = _parse_iw_reg_get(reg_text) if rc_reg == 0 else {"global": {}, "phys": {}}
    global_cc = (reg.get("global", {}) or {}).get("country") or "unknown"
    phy_reg = (reg.get("phys", {}) or {}).get(phy, {}) if phy else {}
    reg_cc = phy_reg.get("country") or global_cc or "unknown"
    reg_source = phy_reg.get("source") or "global"

    if not ap_supported:
        errors.append(build_error_detail("driver_no_ap_mode_5ghz", {"phy": phy}))
    if not channels_5g:
        errors.append(build_error_detail("driver_no_ap_mode_5ghz", {"phy": phy, "reason": "no_5ghz_channels"}))
    if not supports_80:
        errors.append(build_error_detail("driver_no_vht80_or_he80", {"phy": phy}))

    if reg_cc in ("00", "unknown") or global_cc in ("00", "unknown"):
        errors.append(build_error_detail("regdom_unknown_or_global_00", {"country": reg_cc, "global": global_cc}))

    effective_cc = _effective_country(country, reg_cc)

    candidates, counts = _build_80mhz_candidates(
        channels_5g,
        allow_dfs=allow_dfs,
        preferred_primary_channel=preferred_primary_channel,
        country=effective_cc,
    )
    if not candidates and channels_5g:
        if counts.get("dfs", 0) > 0 and not allow_dfs:
            errors.append(build_error_detail("non_dfs_80mhz_channels_unavailable", {"phy": phy}))
            errors.append(build_error_detail("dfs_required_but_disabled", {"phy": phy}))
        else:
            errors.append(build_error_detail("non_dfs_80mhz_channels_unavailable", {"phy": phy}))

    return {
        "errors": errors,
        "warnings": warnings,
        "adapter": {
            "ifname": ap_ifname,
            "phy": phy,
            "supports_ap": ap_supported,
            "supports_vht80": vht80,
            "supports_he80": he80,
        },
        "regdom": {
            "country": reg_cc,
            "global_country": global_cc,
            "source": reg_source,
            "effective_country": effective_cc,
        },
        "channels_5ghz": channels_5g,
        "dfs_policy": "allow" if allow_dfs else "disallow",
        "counts": counts,
        "candidates": candidates,
    }


def probe_5ghz_40(
    ap_ifname: str,
    *,
    inventory: Optional[Dict[str, Any]] = None,
    country: Optional[str] = None,
    allow_dfs: bool = False,
    preferred_primary_channel: Optional[int] = None,
) -> Dict[str, Any]:
    inv = inventory if isinstance(inventory, dict) else get_adapters()
    adapter = None
    for item in inv.get("adapters", []):
        if item.get("ifname") == ap_ifname:
            adapter = item
            break
    phy = adapter.get("phy") if isinstance(adapter, dict) else None

    rc, iw_list = _run_iw_list()
    section = _split_wiphy_sections(iw_list).get(phy, "") if rc == 0 else ""
    channels_5g = _parse_5ghz_channels(section)

    rc_reg, reg_text = _run_iw_reg_get()
    reg = _parse_iw_reg_get(reg_text) if rc_reg == 0 else {"global": {}, "phys": {}}
    global_cc = (reg.get("global", {}) or {}).get("country") or "unknown"
    phy_reg = (reg.get("phys", {}) or {}).get(phy, {}) if phy else {}
    reg_cc = phy_reg.get("country") or global_cc or "unknown"
    effective_cc = _effective_country(country, reg_cc)

    candidates, counts = _build_40mhz_candidates(
        channels_5g,
        allow_dfs=allow_dfs,
        preferred_primary_channel=preferred_primary_channel,
        country=effective_cc,
    )
    return {"candidates": candidates, "counts": counts}


def probe(
    ap_ifname: str,
    *,
    inventory: Optional[Dict[str, Any]] = None,
    country: Optional[str] = None,
    allow_dfs: bool = False,
    preferred_primary_channel: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "os": detect_os_flavor(),
        "firewall": detect_firewall_backends(),
        "network_manager": detect_network_manager(),
        "wifi": probe_5ghz_80(
            ap_ifname,
            inventory=inventory,
            country=country,
            allow_dfs=allow_dfs,
            preferred_primary_channel=preferred_primary_channel,
        ),
    }
