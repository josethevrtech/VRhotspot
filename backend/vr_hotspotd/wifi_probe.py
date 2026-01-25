from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd import os_release
from vr_hotspotd.adapters.inventory import get_adapters

_IW_WIPHY_RE = re.compile(r"^Wiphy\s+(phy\d+)")
_IW_FREQ_LINE_RE = re.compile(
    r"^\s*\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\](.*)$", re.IGNORECASE
)
_IW_VHT_WIDTH_RE = re.compile(r"Supported Channel Width:\s*(.+)$", re.IGNORECASE)
_IW_HE_80_RE = re.compile(r"HE40/HE80(?:/5GHz)?", re.IGNORECASE)
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
}


def build_error_detail(code: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "remediation": ERROR_REMEDIATIONS.get(code, "Check logs for details."),
        "context": context or {},
    }


def _run(cmd: List[str], timeout_s: float = 2.0) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return 124, (out + "\n" + err).strip()
    except Exception as exc:
        return 127, f"{type(exc).__name__}: {exc}"

    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, out.strip()


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


def _split_tokens(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.replace(",", " ").split() if item.strip()]


def detect_os_flavor(info: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    info = info or os_release.read_os_release()
    tokens: List[str] = []
    for key in ("id", "id_like", "variant_id", "variant", "name"):
        tokens.extend(_split_tokens(info.get(key)))

    flavor = "unknown"
    family = None
    if "steamos" in tokens:
        flavor = "steamos"
        family = "arch"
    elif "bazzite" in tokens:
        flavor = "bazzite"
        family = "fedora"
    elif "fedora" in tokens and any(
        t in tokens for t in ("silverblue", "kinoite", "sericea", "onyx", "atomic", "ostree")
    ):
        flavor = "fedora_atomic"
        family = "fedora"
    elif "fedora" in tokens:
        flavor = "fedora"
        family = "fedora"
    elif any(t in tokens for t in ("ubuntu", "debian", "pop", "linuxmint")):
        flavor = "ubuntu_debian"
        family = "debian"
    elif any(t in tokens for t in ("arch", "cachyos")):
        flavor = "arch"
        family = "arch"

    return {
        "id": info.get("id"),
        "id_like": info.get("id_like"),
        "variant_id": info.get("variant_id"),
        "version_id": info.get("version_id"),
        "name": info.get("name"),
        "flavor": flavor,
        "family": family,
    }


def _firewalld_active() -> bool:
    if not shutil.which("firewall-cmd"):
        return False
    rc, out = _run(["firewall-cmd", "--state"], timeout_s=1.0)
    return rc == 0 and out.strip() == "running"


def _ufw_active() -> bool:
    if not shutil.which("ufw"):
        return False
    rc, out = _run(["ufw", "status"], timeout_s=1.5)
    if rc != 0:
        return False
    for line in out.splitlines():
        if "Status:" in line:
            return "active" in line.lower()
    return False


def _iptables_variant() -> Optional[str]:
    ipt = shutil.which("iptables")
    if not ipt:
        return None
    rc, out = _run([ipt, "--version"], timeout_s=1.0)
    if rc != 0:
        return "iptables-unknown"
    low = out.lower()
    if "nf_tables" in low or "nft" in low:
        return "iptables-nft"
    if "legacy" in low:
        return "iptables-legacy"
    return "iptables-unknown"


def detect_firewall_backends() -> Dict[str, Any]:
    firewalld_active = _firewalld_active()
    ufw_active = _ufw_active()
    nft_present = bool(shutil.which("nft"))
    ipt_variant = _iptables_variant()

    selected = "unknown"
    rationale = "no_firewall_detected"
    if firewalld_active:
        selected = "firewalld"
        rationale = "firewalld_running"
    elif ufw_active:
        selected = "ufw"
        rationale = "ufw_active"
    elif nft_present:
        selected = "nftables"
        rationale = "nft_present"
    elif ipt_variant:
        selected = "iptables"
        rationale = "iptables_present"

    return {
        "firewalld": {"available": bool(shutil.which("firewall-cmd")), "active": firewalld_active},
        "ufw": {"available": bool(shutil.which("ufw")), "active": ufw_active},
        "nftables": {"available": nft_present},
        "iptables": {"available": ipt_variant is not None, "variant": ipt_variant},
        "selected_backend": selected,
        "rationale": rationale,
    }


def detect_network_manager() -> Dict[str, Any]:
    nmcli = shutil.which("nmcli")
    running = False
    if nmcli:
        rc, out = _run([nmcli, "-t", "-f", "RUNNING", "g"], timeout_s=1.0)
        running = rc == 0 and out.strip() == "running"
    return {"nmcli": bool(nmcli), "running": running}


def _split_wiphy_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        m = _IW_WIPHY_RE.match(line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, []).append(line)
            continue
        if current is not None:
            sections[current].append(line)
    return {phy: "\n".join(lines) for phy, lines in sections.items()}


def _parse_supported_interface_modes(text: str) -> Optional[bool]:
    if not text or "Supported interface modes" not in text:
        return None
    in_modes = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Supported interface modes"):
            in_modes = True
            continue
        if in_modes:
            if line.startswith("*"):
                mode = line.lstrip("*").strip()
                if mode in ("AP", "AP/VLAN"):
                    return True
            elif line and not line.startswith("*"):
                break
    return False


def _parse_5ghz_channels(text: str) -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    in_freqs = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Frequencies:"):
            in_freqs = True
            continue
        if in_freqs and line and not line.startswith("*"):
            in_freqs = False
        if not in_freqs:
            continue

        m = _IW_FREQ_LINE_RE.match(raw)
        if not m:
            continue
        try:
            mhz = int(float(m.group(1)))
            channel = int(m.group(2))
        except Exception:
            continue
        if not (4900 <= mhz <= 5900):
            continue
        flags = (m.group(3) or "").lower()
        disabled = "disabled" in flags
        no_ir = "no ir" in flags or "no-ir" in flags or "no_ir" in flags
        dfs = "radar detection" in flags or "dfs" in flags
        channels.append(
            {
                "channel": channel,
                "freq_mhz": mhz,
                "disabled": disabled,
                "no_ir": no_ir,
                "dfs": dfs,
                "flags": flags.strip(),
            }
        )
    return channels


def _parse_vht_supports_80(text: str) -> Optional[bool]:
    if "VHT Capabilities" not in text:
        return None
    for line in text.splitlines():
        m = _IW_VHT_WIDTH_RE.search(line)
        if not m:
            continue
        value = m.group(1).strip().lower()
        if "20/40" in value and "80" not in value and "160" not in value:
            return False
        return True
    return True


def _parse_he_supports_80(text: str) -> Optional[bool]:
    if _IW_HE_80_RE.search(text):
        return True
    if "HE Capabilities" in text or "HE Iftypes" in text:
        return None
    return None


def _parse_iw_reg_get(text: str) -> Dict[str, Any]:
    global_country: Optional[str] = None
    global_header: Optional[str] = None
    phys: Dict[str, Dict[str, Any]] = {}

    current_section = "global"
    current_phy: Optional[str] = None
    current_phy_source = "unknown"

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("phy#"):
            current_section = "phy"
            phy_num = s.split()[0].split("#", 1)[1]
            current_phy = f"phy{phy_num}"
            current_phy_source = "self-managed" if "self-managed" in s else "kernel-managed"
            phys.setdefault(current_phy, {"country": None, "source": current_phy_source, "raw_header": None})
            phys[current_phy]["source"] = current_phy_source
            continue

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
        "phys": phys,
    }


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
