from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from vr_hotspotd.engine import firewalld


QOS_PRESETS: Dict[str, Dict[str, Optional[str]]] = {
    "off": {"dscp": None, "qdisc": None, "priority": None},
    "vr": {"dscp": "CS5", "qdisc": "cake", "priority": "normal"},
    "balanced": {"dscp": "AF41", "qdisc": "fq_codel", "priority": "normal"},
    "ultra_low_latency": {"dscp": "CS6", "qdisc": "prio", "priority": "strict"},
    "high_throughput": {"dscp": "AF42", "qdisc": "cake", "priority": "normal"},
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


def _iptables_cmd(ipt: str, action: str, rule: List[str]) -> List[str]:
    cmd: List[str] = [ipt]
    if len(rule) >= 2 and rule[0] == "-t":
        # iptables syntax requires table selection before action:
        #   iptables -t mangle -A POSTROUTING ...
        cmd.extend(rule[:2])
        cmd.append(action)
        cmd.extend(rule[2:])
        return cmd
    cmd.append(action)
    cmd.extend(rule)
    return cmd


def _apply_qdisc(ap_ifname: str, kind: str, priority: Optional[str] = None) -> Tuple[Optional[Dict[str, str]], List[str]]:
    warnings: List[str] = []
    tc = _tc_path()
    if not tc:
        warnings.append("tc_not_found")
        return None, warnings

    # Strict priority qdisc for ultra low latency
    if kind == "prio" and priority == "strict":
        # Create prio qdisc with 3 bands, then add filters for UDP prioritization
        ok, out = _run([tc, "qdisc", "replace", "dev", ap_ifname, "root", "handle", "1:", "prio", "bands", "3"])
        if ok:
            # Add fq_codel to each band for fairness within priority
            _run([tc, "qdisc", "add", "dev", ap_ifname, "parent", "1:1", "handle", "11:", "fq_codel"])
            _run([tc, "qdisc", "add", "dev", ap_ifname, "parent", "1:2", "handle", "12:", "fq_codel"])
            _run([tc, "qdisc", "add", "dev", ap_ifname, "parent", "1:3", "handle", "13:", "fq_codel"])
            
            # Prioritize UDP traffic (VR streaming) to band 1 (highest priority)
            _run([tc, "filter", "add", "dev", ap_ifname, "protocol", "ip", "parent", "1:0", "prio", "1", "u32", "match", "ip", "protocol", "17", "0xff", "flowid", "1:1"])
            
            return {"dev": ap_ifname, "kind": "prio", "priority": "strict"}, warnings
        warnings.append(f"prio_qdisc_failed:{out[:120]}")
        kind = "fq_codel"

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
    ok, _out = _run(_iptables_cmd(ipt, "-C", rule))
    if ok:
        return True, "exists"
    return _run(_iptables_cmd(ipt, "-A", rule))


def _iptables_del(rule: List[str]) -> None:
    ipt = _iptables_path()
    if not ipt:
        return
    _run(_iptables_cmd(ipt, "-D", rule))


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
    priority = QOS_PRESETS[preset].get("priority")
    if qdisc_kind:
        qdisc_state, qdisc_warn = _apply_qdisc(ap_ifname, qdisc_kind, priority)
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
            # For prio qdisc, delete filters first, then qdisc
            if qdisc.get("kind") == "prio":
                _run([tc, "filter", "del", "dev", str(dev), "parent", "1:0"])
            ok, out = _run([tc, "qdisc", "del", "dev", str(dev), "root"])
            if not ok and out:
                warnings.append(f"qdisc_delete_failed:{out[:120]}")
    return warnings
