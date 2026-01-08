from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from vr_hotspotd import qos, nat_accel


def _default_uplink_iface() -> Optional[str]:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    try:
        p = subprocess.run([ip, "route", "show", "default"], capture_output=True, text=True)
    except Exception:
        return None
    for raw in (p.stdout or "").splitlines():
        parts = raw.strip().split()
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return None


def apply(
    cfg: Dict[str, object],
    *,
    ap_ifname: Optional[str],
    enable_internet: bool,
    firewalld_cfg: Optional[Dict[str, object]] = None,
) -> Tuple[Dict[str, object], List[str]]:
    state: Dict[str, object] = {}
    warnings: List[str] = []

    uplink_ifname = _default_uplink_iface()
    state["uplink_ifname"] = uplink_ifname

    qos_state, qos_warn = qos.apply(cfg, ap_ifname=ap_ifname, firewalld_cfg=firewalld_cfg)
    warnings.extend(qos_warn)
    if qos_state:
        state["qos"] = qos_state

    nat_state, nat_warn = nat_accel.apply(
        cfg,
        ap_ifname=ap_ifname,
        uplink_ifname=uplink_ifname,
        enable_internet=enable_internet,
        firewalld_cfg=firewalld_cfg,
    )
    warnings.extend(nat_warn)
    if nat_state:
        state["nat_accel"] = nat_state

    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    warnings.extend(qos.revert(state.get("qos") if isinstance(state.get("qos"), dict) else None))
    warnings.extend(nat_accel.revert(state.get("nat_accel") if isinstance(state.get("nat_accel"), dict) else None))
    return warnings
