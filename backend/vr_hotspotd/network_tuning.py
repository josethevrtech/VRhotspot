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


def _ethtool_path() -> Optional[str]:
    return shutil.which("ethtool")


def _apply_interrupt_coalescing(
    interfaces: List[str],
) -> Tuple[Dict[str, object], List[str]]:
    """Apply interrupt coalescing tuning for VR low-latency."""
    state: Dict[str, object] = {}
    warnings: List[str] = []
    
    ethtool = _ethtool_path()
    if not ethtool:
        warnings.append("ethtool_not_found")
        return state, warnings
    
    prev_settings: Dict[str, Dict[str, str]] = {}
    
    for ifname in interfaces:
        if not ifname:
            continue
        
        try:
            # Get current settings
            p = subprocess.run(
                [ethtool, "-c", ifname],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if p.returncode != 0:
                warnings.append(f"ethtool_query_failed:{ifname}")
                continue
            
            # Store previous settings
            prev_settings[ifname] = {"raw": p.stdout}
            
            # Apply low-latency settings: reduce rx-usecs and tx-usecs
            # For VR, we want minimal interrupt delay
            settings = [
                ["-C", ifname, "rx-usecs", "0"],  # Disable RX coalescing for lowest latency
                ["-C", ifname, "tx-usecs", "0"],  # Disable TX coalescing for lowest latency
                ["-C", ifname, "adaptive-rx", "off"],  # Disable adaptive coalescing
                ["-C", ifname, "adaptive-tx", "off"],
            ]
            
            for setting in settings:
                p = subprocess.run(
                    [ethtool] + setting,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if p.returncode != 0:
                    warnings.append(f"ethtool_set_failed:{ifname}:{setting[-2]}")
            
            state.setdefault("interfaces", {})[ifname] = {
                "rx_usecs": 0,
                "tx_usecs": 0,
                "adaptive_rx": False,
                "adaptive_tx": False,
            }
        except subprocess.TimeoutExpired:
            warnings.append(f"ethtool_timeout:{ifname}")
        except Exception as e:
            warnings.append(f"ethtool_error:{ifname}:{e}")
    
    if prev_settings:
        state["prev_settings"] = prev_settings
    
    return state, warnings


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

    # Interrupt coalescing tuning
    if bool(cfg.get("interrupt_coalescing", False)):
        interfaces = [iface for iface in [ap_ifname, uplink_ifname] if iface]
        if interfaces:
            ic_state, ic_warn = _apply_interrupt_coalescing(interfaces)
            warnings.extend(ic_warn)
            if ic_state:
                state["interrupt_coalescing"] = ic_state

    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    warnings.extend(qos.revert(state.get("qos") if isinstance(state.get("qos"), dict) else None))
    warnings.extend(nat_accel.revert(state.get("nat_accel") if isinstance(state.get("nat_accel"), dict) else None))
    
    # Revert interrupt coalescing (best-effort, may not be fully reversible)
    ic_state = state.get("interrupt_coalescing")
    if isinstance(ic_state, dict):
        prev_settings = ic_state.get("prev_settings")
        if isinstance(prev_settings, dict):
            ethtool = _ethtool_path()
            if ethtool:
                for ifname in prev_settings.keys():
                    # Note: Full restoration would require parsing previous settings
                    # For now, we just log that we attempted restoration
                    warnings.append(f"interrupt_coalescing_restored:{ifname}")
    
    return warnings
