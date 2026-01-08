from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from vr_hotspotd.engine import firewalld


QOS_PRESETS: Dict[str, Dict[str, Optional[str]]] = {
    "off": {"dscp": None, "qdisc": None},
    "vr": {"dscp": "CS5", "qdisc": "cake"},
    "balanced": {"dscp": "AF41", "qdisc": "fq_codel"},
}

_RULE_COMMENT = "vrhotspot-qos"


def _run(cmd: List[str]) -> Tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode == 0, out.strip()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _tc_path() -> Optional[str]:
    return shutil.which("tc")


def _iptables_path() -> Optional[str]:
    return shutil.which("iptables")


def _apply_qdisc(ap_ifname: str, kind: str) -> Tuple[Optional[Dict[str, str]], List[str]]:
    warnings: List[str] = []
    tc = _tc_path()
    if not tc:
        warnings.append("tc_not_found")
        return None, warnings

    if kind == "cake":
        ok, out = _run([tc, "qdisc", "replace", "dev", ap_ifname, "root", "cake", "diffserv4"])
        if ok:
            return {"dev": ap_ifname, "kind": "cake"}, warnings
        warnings.append(f"cake_qdisc_failed:{out[:120]}")
        kind = "fq_codel"

    ok, out = _run([tc, "qdisc", "replace", "dev", ap_ifname, "root", "fq_codel"])
    if ok:
        return {"dev": ap_ifname, "kind": "fq_codel"}, warnings
    warnings.append(f"fq_codel_failed:{out[:120]}")
    return None, warnings


def _iptables_add_unique(rule: List[str]) -> Tuple[bool, str]:
    ipt = _iptables_path()
    if not ipt:
        return False, "iptables_not_found"
    check_rule = rule[:]
    check_rule.insert(1, "-C")
    ok, _out = _run([ipt] + check_rule)
    if ok:
        return True, "exists"
    add_rule = rule[:]
    add_rule.insert(1, "-A")
    return _run([ipt] + add_rule)


def _iptables_del(rule: List[str]) -> None:
    ipt = _iptables_path()
    if not ipt:
        return
    del_rule = rule[:]
    del_rule.insert(1, "-D")
    _run([ipt] + del_rule)


def _dscp_rule(ap_ifname: str, dscp: str) -> List[str]:
    return [
        "-t",
        "mangle",
        "POSTROUTING",
        "-o",
        ap_ifname,
        "-m",
        "comment",
        "--comment",
        _RULE_COMMENT,
        "-j",
        "DSCP",
        "--set-dscp-class",
        dscp,
    ]


def apply(
    cfg: Dict[str, object],
    *,
    ap_ifname: Optional[str],
    firewalld_cfg: Optional[Dict[str, object]] = None,
) -> Tuple[Dict[str, object], List[str]]:
    state: Dict[str, object] = {}
    warnings: List[str] = []

    preset = str(cfg.get("qos_preset", "off")).strip().lower()
    if preset in ("0", "false", "none", ""):
        preset = "off"
    if preset not in QOS_PRESETS:
        warnings.append(f"qos_unknown_preset:{preset}")
        return state, warnings
    if preset == "off":
        return state, warnings

    if not ap_ifname:
        warnings.append("qos_missing_ap_interface")
        return state, warnings

    state["preset"] = preset

    qdisc_kind = QOS_PRESETS[preset].get("qdisc")
    if qdisc_kind:
        qdisc_state, qdisc_warn = _apply_qdisc(ap_ifname, qdisc_kind)
        warnings.extend(qdisc_warn)
        if qdisc_state:
            state["qdisc"] = qdisc_state

    dscp = QOS_PRESETS[preset].get("dscp")
    if dscp:
        fw_enabled = bool(firewalld_cfg.get("firewalld_enabled", True)) if firewalld_cfg else True
        if fw_enabled and firewalld.is_running():
            warnings.append("qos_dscp_skipped_firewalld_active")
        else:
            rule = _dscp_rule(ap_ifname, dscp)
            ok, out = _iptables_add_unique(rule)
            if ok:
                state["dscp_rule"] = rule
            else:
                warnings.append(f"qos_dscp_failed:{out[:120]}")

    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    dscp_rule = state.get("dscp_rule")
    if isinstance(dscp_rule, list):
        _iptables_del([str(x) for x in dscp_rule])

    qdisc = state.get("qdisc")
    if isinstance(qdisc, dict):
        dev = qdisc.get("dev")
        tc = _tc_path()
        if tc and dev:
            ok, out = _run([tc, "qdisc", "del", "dev", str(dev), "root"])
            if not ok and out:
                warnings.append(f"qdisc_delete_failed:{out[:120]}")
    return warnings
