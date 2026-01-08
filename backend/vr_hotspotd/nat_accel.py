from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from vr_hotspotd.engine import firewalld


_TABLE_NAME = "vrhotspot"


def _run(cmd: List[str]) -> Tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode == 0, out.strip()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _nft_path() -> Optional[str]:
    return shutil.which("nft")


def apply(
    cfg: Dict[str, object],
    *,
    ap_ifname: Optional[str],
    uplink_ifname: Optional[str],
    enable_internet: bool,
    firewalld_cfg: Optional[Dict[str, object]] = None,
) -> Tuple[Dict[str, object], List[str]]:
    state: Dict[str, object] = {}
    warnings: List[str] = []

    if not bool(cfg.get("nat_accel", False)):
        return state, warnings

    if bool(cfg.get("bridge_mode", False)):
        warnings.append("nat_accel_skipped_bridge_mode")
        return state, warnings

    if not enable_internet:
        warnings.append("nat_accel_skipped_no_internet")
        return state, warnings

    if not ap_ifname or not uplink_ifname:
        warnings.append("nat_accel_missing_interface")
        return state, warnings

    fw_enabled = bool(firewalld_cfg.get("firewalld_enabled", True)) if firewalld_cfg else True
    if fw_enabled and firewalld.is_running():
        warnings.append("nat_accel_skipped_firewalld_active")
        return state, warnings

    nft = _nft_path()
    if not nft:
        warnings.append("nft_not_found")
        return state, warnings

    _run([nft, "delete", "table", "inet", _TABLE_NAME])

    cmds = [
        [nft, "add", "table", "inet", _TABLE_NAME],
        [
            nft,
            "add",
            "flowtable",
            "inet",
            _TABLE_NAME,
            "ft",
            "{",
            "hook",
            "ingress",
            "priority",
            "0",
            ";",
            "devices",
            "=",
            "{",
            ap_ifname,
            ",",
            uplink_ifname,
            "}",
            ";",
            "}",
        ],
        [
            nft,
            "add",
            "chain",
            "inet",
            _TABLE_NAME,
            "forward",
            "{",
            "type",
            "filter",
            "hook",
            "forward",
            "priority",
            "10",
            ";",
            "policy",
            "accept",
            ";",
            "}",
        ],
        [
            nft,
            "add",
            "rule",
            "inet",
            _TABLE_NAME,
            "forward",
            "ct",
            "state",
            "established,related",
            "flow",
            "add",
            "@ft",
        ],
        [
            nft,
            "add",
            "rule",
            "inet",
            _TABLE_NAME,
            "forward",
            "ip",
            "protocol",
            "{",
            "tcp",
            ",",
            "udp",
            "}",
            "ct",
            "state",
            "new",
            "flow",
            "add",
            "@ft",
        ],
    ]

    for cmd in cmds:
        ok, out = _run(cmd)
        if not ok:
            warnings.append(f"nft_cmd_failed:{out[:120]}")
            _run([nft, "delete", "table", "inet", _TABLE_NAME])
            return state, warnings

    state["table"] = _TABLE_NAME
    state["ap_ifname"] = ap_ifname
    state["uplink_ifname"] = uplink_ifname
    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    nft = _nft_path()
    if not nft:
        return warnings

    table = state.get("table") or _TABLE_NAME
    ok, out = _run([nft, "delete", "table", "inet", str(table)])
    if not ok and out:
        warnings.append(f"nft_delete_failed:{out[:120]}")
    return warnings
