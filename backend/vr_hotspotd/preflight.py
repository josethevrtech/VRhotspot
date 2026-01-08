from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd.engine import supervisor


_RFKILL_WIFI_HINTS = ("wireless", "wifi", "wlan", "wi-fi")
_HOSTAPD_SAE_RE = re.compile(r"\bsae\b", re.IGNORECASE)
_HOSTAPD_HE_RE = re.compile(r"\b(802\.11ax|ieee80211ax|he)\b", re.IGNORECASE)


def _run(cmd: List[str], timeout_s: float = 1.2) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return 124, (out + "\n" + err).strip()
    except Exception as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _parse_rfkill(text: str) -> List[Dict[str, Optional[str]]]:
    devices: List[Dict[str, Optional[str]]] = []
    cur: Dict[str, Optional[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^\d+:\s", line):
            if cur:
                devices.append(cur)
            cur = {"name": None, "type": None, "soft": None, "hard": None}
            _idx, rest = line.split(":", 1)
            parts = [p.strip() for p in rest.split(":", 1)]
            if parts:
                cur["name"] = parts[0] or None
            if len(parts) > 1:
                cur["type"] = parts[1] or None
            continue
        if "Soft blocked:" in line:
            cur["soft"] = line.split(":", 1)[1].strip().lower()
        elif "Hard blocked:" in line:
            cur["hard"] = line.split(":", 1)[1].strip().lower()
    if cur:
        devices.append(cur)
    return devices


def _check_rfkill() -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {"blocked": [], "checked": False}

    rfkill = shutil.which("rfkill")
    if not rfkill:
        warnings.append("rfkill_not_found")
        return errors, warnings, details

    rc, out = _run([rfkill, "list"])
    details["checked"] = True
    details["rc"] = rc
    details["raw"] = out[:500]
    if rc != 0:
        warnings.append("rfkill_list_failed")
        return errors, warnings, details

    blocked: List[str] = []
    for dev in _parse_rfkill(out):
        dev_type = (dev.get("type") or "").lower()
        is_wifi = any(hint in dev_type for hint in _RFKILL_WIFI_HINTS)
        if not is_wifi:
            continue
        soft = dev.get("soft") == "yes"
        hard = dev.get("hard") == "yes"
        if soft or hard:
            name = dev.get("name") or "wifi"
            blocked.append(f"{name}:soft={soft},hard={hard}")
    if blocked:
        errors.append("rfkill_blocked")
    details["blocked"] = blocked
    return errors, warnings, details


def _check_regdom(
    cfg_country: Optional[str],
    adapter: Optional[Dict[str, Any]],
    band: str,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {}

    if not cfg_country:
        return errors, warnings, details

    cc = cfg_country.strip().upper()
    if len(cc) != 2:
        return errors, warnings, details

    regdom = adapter.get("regdom") if isinstance(adapter, dict) else {}
    reg_cc = (regdom.get("country") if isinstance(regdom, dict) else None) or "unknown"
    global_cc = (regdom.get("global_country") if isinstance(regdom, dict) else None) or "unknown"
    details["cfg_country"] = cc
    details["adapter_country"] = reg_cc
    details["global_country"] = global_cc
    details["adapter_source"] = regdom.get("source") if isinstance(regdom, dict) else None

    if reg_cc in ("unknown", "00") or global_cc in ("unknown", "00"):
        msg = "regdom_unknown_or_global_00"
        if band == "6ghz":
            errors.append(msg)
        else:
            warnings.append(msg)
        return errors, warnings, details

    if reg_cc != cc:
        msg = f"regdom_mismatch(adapter={reg_cc},config={cc})"
        if band == "6ghz":
            errors.append(msg)
        else:
            warnings.append(msg)

    return errors, warnings, details


def _resolve_hostapd_path() -> Optional[str]:
    env = supervisor._build_engine_env()
    override = env.get("HOSTAPD")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    path_env = env.get("PATH")
    cand = shutil.which("hostapd", path=path_env) if path_env else None
    if cand:
        return cand
    return shutil.which("hostapd")


def _hostapd_caps() -> Dict[str, Optional[bool]]:
    caps: Dict[str, Optional[bool]] = {"sae": None, "he": None}
    hostapd = _resolve_hostapd_path()
    if not hostapd:
        caps["error"] = "hostapd_not_found"
        return caps

    rc, out = _run([hostapd, "-vv"])
    if rc != 0 and not out:
        rc, out = _run([hostapd, "-v"])
    if rc != 0 and not out:
        caps["error"] = f"hostapd_version_failed(rc={rc})"
        return caps

    caps["raw"] = out[:800]
    caps["sae"] = bool(_HOSTAPD_SAE_RE.search(out)) or ("wpa3" in out.lower())
    caps["he"] = bool(_HOSTAPD_HE_RE.search(out))
    return caps


def _check_hostapd_features(
    band: str,
    ap_security: str,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {}

    caps = _hostapd_caps()
    details.update(caps)

    need_sae = ap_security == "wpa3_sae" or band == "6ghz"
    need_he = band == "6ghz"

    sae = caps.get("sae")
    he = caps.get("he")

    if need_sae:
        if sae is False:
            errors.append("hostapd_missing_sae")
        elif sae is None:
            warnings.append("hostapd_sae_unknown")

    if need_he:
        if he is False:
            errors.append("hostapd_missing_11ax")
        elif he is None:
            warnings.append("hostapd_11ax_unknown")

    return errors, warnings, details


def _parse_ip_addrs(text: str) -> List[Tuple[str, str, int]]:
    out: List[Tuple[str, str, int]] = []
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        ifname, ip, prefix = m.group(1), m.group(2), int(m.group(3))
        out.append((ifname, ip, prefix))
    return out


def _parse_routes(text: str) -> List[Tuple[str, Optional[str]]]:
    routes: List[Tuple[str, Optional[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        dest = parts[0]
        dev = None
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                dev = parts[idx + 1]
        routes.append((dest, dev))
    return routes


def _check_subnet_conflicts(
    gateway_ip: Optional[str],
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {"conflicts": []}

    if not gateway_ip:
        return errors, warnings, details

    try:
        subnet = ipaddress.ip_network(f"{gateway_ip}/24", strict=False)
    except Exception:
        warnings.append("gateway_ip_invalid_for_preflight")
        return errors, warnings, details

    ip_bin = shutil.which("ip") or "/usr/sbin/ip"
    rc, out = _run([ip_bin, "-4", "-o", "addr", "show"])
    if rc == 0:
        for ifname, ip, _prefix in _parse_ip_addrs(out):
            try:
                if ipaddress.ip_address(ip) in subnet:
                    details["conflicts"].append(f"addr:{ifname}:{ip}")
            except Exception:
                continue
    else:
        warnings.append("ip_addr_check_failed")

    rc, out = _run([ip_bin, "-4", "route", "show"])
    if rc == 0:
        for dest, dev in _parse_routes(out):
            if dest == "default":
                continue
            try:
                net = ipaddress.ip_network(dest, strict=False)
            except Exception:
                continue
            if net == subnet:
                details["conflicts"].append(f"route:{dev or '?'}:{dest}")
    else:
        warnings.append("ip_route_check_failed")

    if details["conflicts"]:
        errors.append("subnet_conflict")

    return errors, warnings, details


def run(
    cfg: Dict[str, Any],
    *,
    adapter: Optional[Dict[str, Any]],
    band: str,
    ap_security: str,
    enable_internet: bool,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, Any] = {}

    rfkill_err, rfkill_warn, rfkill_details = _check_rfkill()
    errors += rfkill_err
    warnings += rfkill_warn
    details["rfkill"] = rfkill_details

    country = cfg.get("country") if isinstance(cfg, dict) else None
    reg_err, reg_warn, reg_details = _check_regdom(
        country if isinstance(country, str) else None,
        adapter,
        band,
    )
    errors += reg_err
    warnings += reg_warn
    details["regdom"] = reg_details

    hp_err, hp_warn, hp_details = _check_hostapd_features(band, ap_security)
    errors += hp_err
    warnings += hp_warn
    details["hostapd"] = hp_details

    bridge_mode = bool(cfg.get("bridge_mode", False))
    if not bridge_mode:
        gw_ip = cfg.get("lan_gateway_ip") if isinstance(cfg, dict) else None
        sub_err, sub_warn, sub_details = _check_subnet_conflicts(
            gw_ip if isinstance(gw_ip, str) else None
        )
        errors += sub_err
        warnings += sub_warn
        details["subnet"] = sub_details
    else:
        details["subnet"] = {"skipped": True}
        uplink = cfg.get("bridge_uplink") if isinstance(cfg, dict) else None
        if isinstance(uplink, str) and uplink.strip():
            if not os.path.exists(f"/sys/class/net/{uplink.strip()}"):
                errors.append("bridge_uplink_not_found")

    if not enable_internet and not bridge_mode:
        warnings.append("internet_disabled_no_nat")

    return {"errors": errors, "warnings": warnings, "details": details}
