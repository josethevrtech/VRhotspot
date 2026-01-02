import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Optional, Set, Dict, Any, List, Tuple

from vr_hotspotd.state import load_state, update_state
from vr_hotspotd.adapters.inventory import get_adapters
from vr_hotspotd.config import load_config, ensure_config_file
from vr_hotspotd.engine.lnxrouter_cmd import build_cmd
from vr_hotspotd.engine.hostapd6_cmd import build_cmd_6ghz
from vr_hotspotd.engine.supervisor import start_engine, stop_engine, is_running

log = logging.getLogger("vr_hotspotd.lifecycle")

_OP_LOCK = threading.Lock()


class LifecycleResult:
    def __init__(self, code, state):
        self.code = code
        self.state = state


_START_OVERRIDE_KEYS = {
    "ssid",
    "wpa2_passphrase",
    "band_preference",
    "country",
    "optimized_no_virt",
    "ap_adapter",
    "ap_ready_timeout_s",
    "fallback_channel_2g",
    "debug",
    # NEW:
    "ap_security",   # "wpa2" (default) or "wpa3_sae"
    "channel_6g",    # int
}

# Broaden virtual AP detection: still safe because we only delete if type == AP.
_VIRT_AP_RE = re.compile(r"^x\d+.+$")

_LNXROUTER_PATH = "/var/lib/vr-hotspot/app/backend/vendor/bin/lnxrouter"


def _iw_bin() -> str:
    iw = shutil.which("iw")
    if iw:
        return iw
    if os.path.exists("/usr/sbin/iw"):
        return "/usr/sbin/iw"
    raise RuntimeError("iw_not_found")


def _run(cmd: List[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")


def _iw_dev_dump() -> str:
    return _run([_iw_bin(), "dev"])


def _parse_iw_dev_ap_ifaces(iw_text: str) -> Set[str]:
    ap_ifaces: Set[str] = set()
    cur_iface: Optional[str] = None
    cur_type: Optional[str] = None

    for raw in iw_text.splitlines():
        line = raw.strip()
        if line.startswith("Interface "):
            if cur_iface and cur_type == "AP":
                ap_ifaces.add(cur_iface)
            cur_iface = line.split(" ", 1)[1].strip()
            cur_type = None
        elif line.startswith("type "):
            cur_type = line.split(" ", 1)[1].strip()

    if cur_iface and cur_type == "AP":
        ap_ifaces.add(cur_iface)

    return ap_ifaces


def _iface_phy(ifname: str) -> Optional[str]:
    p = subprocess.run(
        [_iw_bin(), "dev", ifname, "info"],
        capture_output=True,
        text=True,
    )
    for raw in (p.stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("wiphy "):
            idx = line.split(" ", 1)[1].strip()
            if idx.isdigit():
                return f"phy{idx}"
    return None


def _wait_for_ap_ready(
    target_phy: Optional[str],
    timeout_s: float = 6.0,
    poll_s: float = 0.25,
) -> Optional[str]:
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        dump = _iw_dev_dump()
        ap_ifaces = _parse_iw_dev_ap_ifaces(dump)

        if ap_ifaces:
            if target_phy is None:
                return sorted(ap_ifaces)[0]

            for ap_if in sorted(ap_ifaces):
                if _iface_phy(ap_if) == target_phy:
                    return ap_if

        time.sleep(poll_s)

    return None


def _pid_cmdline(pid: int) -> str:
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
        return raw.decode("utf-8", "ignore").replace("\x00", " ").strip()
    except Exception:
        return ""


def _pid_is_our_lnxrouter(pid: int) -> bool:
    cmdline = _pid_cmdline(pid)
    return bool(cmdline) and (_LNXROUTER_PATH in cmdline or "lnxrouter" in cmdline)


def _find_our_lnxrouter_pids() -> List[int]:
    pids: List[int] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        cmdline = _pid_cmdline(pid)
        if not cmdline:
            continue
        if _LNXROUTER_PATH in cmdline:
            pids.append(pid)
    return sorted(set(pids))


def _kill_pid(pid: int, timeout_s: float = 3.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not os.path.exists(f"/proc/{pid}"):
            return
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _cleanup_virtual_ap_ifaces(target_phy: Optional[str] = None) -> List[str]:
    removed: List[str] = []

    try:
        dump = _iw_dev_dump()
        ap_ifaces = _parse_iw_dev_ap_ifaces(dump)
    except Exception:
        return removed

    for ifname in sorted(ap_ifaces):
        if not _VIRT_AP_RE.match(ifname):
            continue

        if target_phy is not None:
            try:
                phy = _iface_phy(ifname)
            except Exception:
                phy = None
            if phy != target_phy:
                continue

        try:
            subprocess.run([_iw_bin(), "dev", ifname, "del"], check=False, capture_output=True, text=True)
        except Exception:
            pass

        removed.append(ifname)

    return removed


def _select_ap_adapter(inv: dict, band_pref: str) -> str:
    """
    Select an AP adapter for the requested band.
    For 6ghz: requires supports_6ghz True.
    """
    bp = (band_pref or "").lower().strip()
    if bp in ("6", "6g", "6ghz", "6e"):
        for a in inv.get("adapters", []):
            if a.get("supports_ap") and a.get("supports_6ghz"):
                return a.get("ifname")
        raise RuntimeError("no_6ghz_ap_capable_adapter_found")

    # Default behavior: use existing recommended
    rec = inv.get("recommended")
    if rec:
        return rec

    # Fallback: any AP-capable adapter
    for a in inv.get("adapters", []):
        if a.get("supports_ap"):
            return a.get("ifname")

    raise RuntimeError("no_ap_capable_adapter_found")


def _get_adapter(inv: dict, ifname: str) -> Optional[dict]:
    for a in inv.get("adapters", []):
        if a.get("ifname") == ifname:
            return a
    return None


def _get_adapter_phy(inv: dict, ifname: str) -> Optional[str]:
    a = _get_adapter(inv, ifname)
    return a.get("phy") if a else None


def _build_firewalld_cfg(cfg: dict) -> dict:
    return {
        "firewalld_enabled": bool(cfg.get("firewalld_enabled", True)),
        "firewalld_zone": str(cfg.get("firewalld_zone", "trusted")),
        "firewalld_enable_masquerade": bool(cfg.get("firewalld_enable_masquerade", True)),
        "firewalld_enable_forward": bool(cfg.get("firewalld_enable_forward", False)),
        "firewalld_cleanup_on_stop": bool(cfg.get("firewalld_cleanup_on_stop", True)),
    }


def _apply_start_overrides(cfg: Dict[str, Any], overrides: Optional[dict]) -> Dict[str, Any]:
    if not overrides or not isinstance(overrides, dict):
        return cfg
    for k, v in overrides.items():
        if k in _START_OVERRIDE_KEYS:
            cfg[k] = v
    return cfg


def _maybe_set_regdom(country: Optional[str]) -> None:
    if not country or not isinstance(country, str):
        return
    cc = country.strip().upper()
    if len(cc) != 2:
        return
    try:
        subprocess.run([_iw_bin(), "reg", "set", cc], check=False, capture_output=True, text=True)
    except Exception:
        pass


def reconcile_state_with_engine() -> Dict[str, Any]:
    st = load_state()
    if st.get("running") and not is_running():
        return update_state(
            running=False,
            phase="error",
            last_error="engine_not_running_state_reconciled",
            engine={"pid": None, "last_error": "engine_not_running_state_reconciled"},
        )
    return st


def repair(correlation_id: str = "repair"):
    with _OP_LOCK:
        return _repair_impl(correlation_id=correlation_id)


def _repair_impl(correlation_id: str = "repair"):
    cfg = load_config()
    fw_cfg = _build_firewalld_cfg(cfg)

    st = load_state()
    removed_ifaces: List[str] = []

    try:
        stop_engine(firewalld_cfg=fw_cfg)
    except Exception:
        pass

    pid = st.get("engine", {}).get("pid")
    if pid and isinstance(pid, int) and pid > 1 and _pid_is_our_lnxrouter(pid):
        _kill_pid(pid)

    for p in _find_our_lnxrouter_pids():
        if p != pid:
            _kill_pid(p)

    try:
        inv = get_adapters()
        preferred = cfg.get("ap_adapter")
        if preferred and isinstance(preferred, str) and preferred.strip():
            ap_ifname = preferred.strip()
        else:
            ap_ifname = inv.get("recommended") or _select_ap_adapter(inv, cfg.get("band_preference", "5ghz"))
        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception:
        target_phy = None

    try:
        removed_ifaces = _cleanup_virtual_ap_ifaces(target_phy=target_phy)
    except Exception:
        removed_ifaces = []

    warnings: List[str] = []
    if removed_ifaces:
        warnings.append("repair_removed_virtual_ap_ifaces:" + ",".join(removed_ifaces))

    st = update_state(
        running=False,
        phase="stopped",
        last_error=None,
        last_op="repair",
        last_correlation_id=correlation_id,
        warnings=warnings,
        engine={
            "pid": None,
            "cmd": None,
            "started_ts": None,
            "last_exit_code": None,
            "last_error": None,
            "stdout_tail": [],
            "stderr_tail": [],
        },
    )
    return st


def start_hotspot(correlation_id: str = "start", overrides: Optional[dict] = None):
    with _OP_LOCK:
        return _start_hotspot_impl(correlation_id=correlation_id, overrides=overrides)


def _start_hotspot_impl(correlation_id: str = "start", overrides: Optional[dict] = None):
    ensure_config_file()
    _repair_impl(correlation_id=correlation_id)

    state = load_state()
    if state["phase"] in ("starting", "running"):
        return LifecycleResult("already_running", state)

    state = update_state(
        phase="starting",
        last_op="start",
        last_correlation_id=correlation_id,
        last_error=None,
        mode=None,
        fallback_reason=None,
        warnings=[],
    )

    cfg = load_config()
    cfg = _apply_start_overrides(cfg, overrides)
    fw_cfg = _build_firewalld_cfg(cfg)

    ssid = cfg.get("ssid", "VR-Hotspot")
    passphrase = cfg.get("wpa2_passphrase", "")
    country = cfg.get("country")
    band_pref = cfg.get("band_preference", "5ghz")
    ap_ready_timeout_s = float(cfg.get("ap_ready_timeout_s", 6.0))
    optimized_no_virt = bool(cfg.get("optimized_no_virt", False))
    debug = bool(cfg.get("debug", False))

    # Normalize band
    bp = str(band_pref or "").lower().strip()
    if bp in ("2ghz", "2.4", "2.4ghz"):
        bp = "2.4ghz"
    elif bp in ("5", "5g", "5ghz"):
        bp = "5ghz"
    elif bp in ("6", "6g", "6ghz", "6e"):
        bp = "6ghz"
    else:
        bp = "5ghz"

    if not isinstance(passphrase, str) or len(passphrase) < 8:
        err = "invalid_passphrase_min_length_8"
        state = update_state(
            phase="error",
            running=False,
            last_error=err,
            last_correlation_id=correlation_id,
            engine={"last_error": err},
        )
        return LifecycleResult("start_failed", state)

    try:
        inv = get_adapters()

        preferred = cfg.get("ap_adapter")
        if preferred and isinstance(preferred, str) and preferred.strip():
            ap_ifname = preferred.strip()
        else:
            ap_ifname = _select_ap_adapter(inv, bp)

        # Validate band capability if explicitly requested
        a = _get_adapter(inv, ap_ifname)
        if not a or not a.get("supports_ap"):
            raise RuntimeError("no_ap_capable_adapter_found")

        if bp == "6ghz" and not a.get("supports_6ghz"):
            raise RuntimeError("selected_adapter_not_6ghz_capable")

        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception as e:
        state = update_state(
            phase="error",
            running=False,
            last_error=str(e),
            last_correlation_id=correlation_id,
            engine={
                "pid": None,
                "cmd": None,
                "started_ts": None,
                "last_exit_code": None,
                "last_error": str(e),
                "stdout_tail": [],
                "stderr_tail": [],
            },
        )
        return LifecycleResult("start_failed", state)

    # Best-effort regdom set before starting (helps 5/6 GHz bringup on many systems)
    _maybe_set_regdom(country if isinstance(country, str) else None)

    # Enforce WPA3-SAE for 6 GHz
    ap_security = str(cfg.get("ap_security", "wpa2")).lower().strip()
    if bp == "6ghz" and ap_security != "wpa3_sae":
        err = "wpa3_sae_required_for_6ghz_set_ap_security_to_wpa3_sae"
        state = update_state(
            phase="error",
            running=False,
            adapter=ap_ifname,
            last_error=err,
            last_correlation_id=correlation_id,
            engine={"last_error": err},
        )
        return LifecycleResult("start_failed", state)

    # Attempt 1: requested band
    if bp == "6ghz":
        channel_6g = cfg.get("channel_6g", None)
        if channel_6g is not None:
            try:
                channel_6g = int(channel_6g)
            except Exception:
                channel_6g = None

        cmd1 = build_cmd_6ghz(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            country=country if isinstance(country, str) else None,
            channel=channel_6g,
            no_virt=optimized_no_virt,
            debug=debug,
        )
    else:
        cmd1 = build_cmd(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            band_preference=bp,
            country=country if isinstance(country, str) else None,
            channel=None,
            no_virt=optimized_no_virt,
        )

    res = start_engine(cmd1, firewalld_cfg=fw_cfg)

    state = update_state(
        adapter=ap_ifname,
        engine={
            "pid": res.pid,
            "cmd": res.cmd,
            "started_ts": res.started_ts,
            "last_exit_code": res.exit_code,
            "last_error": res.error,
            "stdout_tail": res.stdout_tail,
            "stderr_tail": res.stderr_tail,
        },
    )

    if not res.ok:
        state = update_state(
            phase="error",
            running=False,
            last_error=res.error or "engine_start_failed",
            last_correlation_id=correlation_id,
        )
        return LifecycleResult("start_failed", state)

    ap_iface = _wait_for_ap_ready(target_phy, ap_ready_timeout_s)
    if ap_iface:
        state = update_state(
            phase="running",
            running=True,
            band=bp,
            mode="optimized",
            fallback_reason=None,
            warnings=[],
            last_error=None,
            last_correlation_id=correlation_id,
            engine={"last_error": None, "last_exit_code": None},
        )
        return LifecycleResult("started", state)

    # If requested band failed to become ready, fallback (6 -> 5 -> 2.4).
    stop_engine(firewalld_cfg=fw_cfg)

    warnings: List[str] = ["optimized_ap_start_timed_out"]
    fallback_chain: List[Tuple[str, Optional[int], bool, str]] = []

    if bp == "6ghz":
        fallback_chain = [
            ("5ghz", None, optimized_no_virt, "fallback_to_5ghz"),
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), True, "fallback_to_2_4ghz"),
        ]
    elif bp == "5ghz":
        fallback_chain = [
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), True, "fallback_to_2_4ghz"),
        ]
    else:
        state = update_state(
            phase="error",
            running=False,
            last_error="ap_ready_timeout",
            last_correlation_id=correlation_id,
            fallback_reason=None,
            warnings=warnings,
        )
        return LifecycleResult("start_failed", state)

    for band, channel, no_virt, warning_tag in fallback_chain:
        warnings.append(warning_tag)

        cmd_fallback = build_cmd(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            band_preference=band,
            country=country if isinstance(country, str) else None,
            channel=channel,
            no_virt=no_virt,
        )

        res_fallback = start_engine(cmd_fallback, firewalld_cfg=fw_cfg)
        state = update_state(
            adapter=ap_ifname,
            engine={
                "pid": res_fallback.pid,
                "cmd": res_fallback.cmd,
                "started_ts": res_fallback.started_ts,
                "last_exit_code": res_fallback.exit_code,
                "last_error": res_fallback.error,
                "stdout_tail": res_fallback.stdout_tail,
                "stderr_tail": res_fallback.stderr_tail,
            },
        )

        if not res_fallback.ok:
            state = update_state(
                phase="error",
                running=False,
                last_error=res_fallback.error or "engine_start_failed_fallback",
                last_correlation_id=correlation_id,
                fallback_reason="ap_ready_timeout",
                warnings=warnings,
            )
            return LifecycleResult("start_failed", state)

        ap_iface_fallback = _wait_for_ap_ready(target_phy, ap_ready_timeout_s)
        if ap_iface_fallback:
            state = update_state(
                phase="running",
                running=True,
                band=band,
                mode="fallback",
                fallback_reason="ap_ready_timeout",
                warnings=warnings,
                last_error=None,
                last_correlation_id=correlation_id,
                engine={"last_error": None, "last_exit_code": None},
            )
            return LifecycleResult("started_with_fallback", state)

        stop_engine(firewalld_cfg=fw_cfg)

    state = update_state(
        phase="error",
        running=False,
        last_error="ap_ready_timeout_after_fallback",
        last_correlation_id=correlation_id,
        fallback_reason="ap_ready_timeout",
        warnings=warnings,
    )
    return LifecycleResult("start_failed", state)


def stop_hotspot(correlation_id: str = "stop"):
    with _OP_LOCK:
        return _stop_hotspot_impl(correlation_id=correlation_id)


def _stop_hotspot_impl(correlation_id: str = "stop"):
    state = load_state()

    if state["phase"] == "stopped":
        return LifecycleResult("already_stopped", state)

    cfg = load_config()
    fw_cfg = _build_firewalld_cfg(cfg)

    state = update_state(
        phase="stopping",
        last_op="stop",
        last_correlation_id=correlation_id,
        last_error=None,
    )

    ok, rc, out_tail, err_tail, err = stop_engine(firewalld_cfg=fw_cfg)

    removed_ifaces: List[str] = []
    try:
        removed_ifaces = _cleanup_virtual_ap_ifaces(target_phy=None)
    except Exception:
        removed_ifaces = []

    warnings: List[str] = []
    if removed_ifaces:
        warnings.append("stop_removed_virtual_ap_ifaces:" + ",".join(removed_ifaces))

    state = update_state(
        engine={
            "pid": None,
            "cmd": None,
            "started_ts": None,
            "last_exit_code": rc,
            "last_error": err,
            "stdout_tail": out_tail,
            "stderr_tail": err_tail,
        }
    )

    state = update_state(
        phase="stopped",
        running=False,
        adapter=None,
        band=None,
        mode=None,
        fallback_reason=None,
        warnings=warnings,
        last_error=(err if not ok else None),
        last_correlation_id=correlation_id,
    )

    return LifecycleResult("stopped" if ok else "stop_failed", state)
