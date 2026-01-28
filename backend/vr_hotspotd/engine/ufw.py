import shutil
import subprocess
from typing import Dict, List, Optional, Tuple


def _run(cmd: List[str]) -> Tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode == 0, out.strip()


def is_active() -> bool:
    if not shutil.which("ufw"):
        return False
    ok, out = _run(["ufw", "status"])
    if not ok:
        return False
    for line in out.splitlines():
        if "Status:" in line:
            return "active" in line.lower()
    return False


def apply(
    *,
    ap_ifname: Optional[str],
    uplink_ifname: Optional[str],
    enable_internet: bool,
) -> Tuple[Dict[str, object], List[str]]:
    state: Dict[str, object] = {}
    warnings: List[str] = []

    if not enable_internet:
        warnings.append("ufw_skip_no_internet")
        return state, warnings
    if not ap_ifname:
        warnings.append("ufw_missing_ap_interface")
        return state, warnings
    if not is_active():
        warnings.append("ufw_not_active")
        return state, warnings

    rules: List[str] = []
    ok, out = _run(["ufw", "allow", "in", "on", ap_ifname])
    if ok:
        rules.append(f"allow_in:{ap_ifname}")
    else:
        warnings.append(f"ufw_allow_in_failed:{out[:120]}")

    if uplink_ifname:
        ok, out = _run(
            ["ufw", "route", "allow", "in", "on", ap_ifname, "out", "on", uplink_ifname]
        )
        if ok:
            rules.append(f"route_allow:{ap_ifname}:{uplink_ifname}")
        else:
            warnings.append(f"ufw_route_allow_failed:{out[:120]}")
    else:
        warnings.append("ufw_missing_uplink_interface")

    if rules:
        state["ap_ifname"] = ap_ifname
        state["uplink_ifname"] = uplink_ifname
        state["rules"] = rules
    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    if not shutil.which("ufw"):
        return warnings

    ap_ifname = state.get("ap_ifname")
    uplink_ifname = state.get("uplink_ifname")
    rules = state.get("rules") if isinstance(state.get("rules"), list) else []

    def _is_missing_rule_error(output: str) -> bool:
        low = (output or "").lower()
        return (
            "skipping" in low
            or "could not find" in low
            or "could not find a matching rule" in low
            or "no matching" in low
            or "not found" in low
        )

    if ap_ifname and "allow_in" in " ".join(rules):
        ok, out = _run(["ufw", "delete", "allow", "in", "on", str(ap_ifname)])
        if not ok and not _is_missing_rule_error(out):
            warnings.append(f"ufw_delete_allow_in_failed:{out[:120]}")

    if ap_ifname and uplink_ifname and "route_allow" in " ".join(rules):
        ok, out = _run(
            [
                "ufw",
                "route",
                "delete",
                "allow",
                "in",
                "on",
                str(ap_ifname),
                "out",
                "on",
                str(uplink_ifname),
            ]
        )
        if not ok and not _is_missing_rule_error(out):
            warnings.append(f"ufw_delete_route_allow_failed:{out[:120]}")

    return warnings
