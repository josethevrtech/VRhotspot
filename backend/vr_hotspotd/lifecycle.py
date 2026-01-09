import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Dict, Any, List, Tuple

from vr_hotspotd.state import load_state, update_state
from vr_hotspotd.adapters.inventory import get_adapters
from vr_hotspotd.adapters.profiles import apply_adapter_profile
from vr_hotspotd.config import load_config, ensure_config_file
from vr_hotspotd.engine.lnxrouter_cmd import build_cmd
from vr_hotspotd.engine import lnxrouter_conf
from vr_hotspotd.engine.hostapd6_cmd import build_cmd_6ghz
from vr_hotspotd.engine.hostapd_bridge_cmd import build_cmd_bridge
from vr_hotspotd.engine.supervisor import start_engine, stop_engine, is_running
from vr_hotspotd.engine.channel_scan import select_best_channel
from vr_hotspotd.engine.tx_power import auto_adjust_tx_power, set_tx_power, get_tx_power
from vr_hotspotd import system_tuning, preflight, network_tuning

log = logging.getLogger("vr_hotspotd.lifecycle")

_OP_LOCK = threading.Lock()
_WATCHDOG_THREAD: Optional[threading.Thread] = None
_WATCHDOG_STOP = threading.Event()
_WATCHDOG_BACKOFF_MAX_S = 30.0


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
    "wifi6",         # "auto" | true | false
    "channel_width",  # "auto" | "20" | "40" | "80" | "160"
    "beacon_interval",  # int
    "dtim_period",  # int
    "short_guard_interval",  # bool
    "tx_power",  # int or None
    "channel_auto_select",  # bool
    # Network
    "lan_gateway_ip",
    "dhcp_start_ip",
    "dhcp_end_ip",
    "dhcp_dns",
    "enable_internet",
    # System tuning
    "wifi_power_save_disable",
    "usb_autosuspend_disable",
    "cpu_governor_performance",
    "cpu_affinity",
    "sysctl_tuning",
    "irq_affinity",
    "interrupt_coalescing",
    "tcp_low_latency",
    "memory_tuning",
    "io_scheduler_optimize",
    # Watchdog / telemetry / QoS / NAT
    "watchdog_enable",
    "watchdog_interval_s",
    "telemetry_enable",
    "telemetry_interval_s",
    "qos_preset",
    "nat_accel",
    "connection_quality_monitoring",
    "auto_channel_switch",
    # Bridge mode
    "bridge_mode",
    "bridge_name",
    "bridge_uplink",
}

# Broaden virtual AP detection: still safe because we only delete if type == AP.
_VIRT_AP_RE = re.compile(r"^x\d+.+$")

_LNXROUTER_PATH = "/var/lib/vr-hotspot/app/backend/vendor/bin/lnxrouter"
_LNXROUTER_TMP = Path("/dev/shm/lnxrouter_tmp")
_HOSTAPD_CTRL_CANDIDATES = (Path("/run/hostapd"), Path("/var/run/hostapd"))

_IW_PHY_RE = re.compile(r"^phy#(\d+)$")
_IW_CHANNEL_RE = re.compile(r"^channel\s+(\d+)(?:\s+\((\d+(?:\.\d+)?)\s+MHz\))?")
_IW_FREQ_RE = re.compile(r"^(?:freq|frequency)(?:[:\s]+)(\d+(?:\.\d+)?)\b")


@dataclass(frozen=True)
class APReadyInfo:
    ifname: str
    phy: Optional[str]
    ssid: Optional[str]
    freq_mhz: Optional[int]
    channel: Optional[int]


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


def _parse_iw_dev_ap_info(iw_text: str) -> List[APReadyInfo]:
    aps: List[APReadyInfo] = []
    cur_phy: Optional[str] = None
    cur: Optional[Dict[str, Optional[object]]] = None

    def _finalize_current():
        nonlocal cur
        if not cur:
            return
        ifname = cur.get("ifname")
        iface_type = (cur.get("type") or "").upper()
        if ifname and iface_type.startswith("AP"):
            aps.append(
                APReadyInfo(
                    ifname=str(ifname),
                    phy=cur.get("phy"),
                    ssid=cur.get("ssid"),
                    freq_mhz=cur.get("freq_mhz"),
                    channel=cur.get("channel"),
                )
            )
        cur = None

    for raw in iw_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m_phy = _IW_PHY_RE.match(line)
        if m_phy:
            _finalize_current()
            cur_phy = f"phy{m_phy.group(1)}"
            continue

        if line.startswith("Interface "):
            _finalize_current()
            parts = line.split()
            cur = {
                "ifname": parts[1] if len(parts) > 1 else None,
                "phy": cur_phy,
                "type": None,
                "ssid": None,
                "freq_mhz": None,
                "channel": None,
            }
            continue

        if not cur:
            continue

        if line.startswith("type "):
            cur["type"] = line.split(" ", 1)[1].strip()
            continue
        if line.startswith("ssid "):
            cur["ssid"] = line.split(" ", 1)[1].strip()
            continue

        m_channel = _IW_CHANNEL_RE.match(line)
        if m_channel:
            try:
                cur["channel"] = int(m_channel.group(1))
            except Exception:
                cur["channel"] = None
            if cur.get("freq_mhz") is None and m_channel.group(2):
                try:
                    cur["freq_mhz"] = int(float(m_channel.group(2)))
                except Exception:
                    pass
            continue

        m_freq = _IW_FREQ_RE.match(line)
        if m_freq and cur.get("freq_mhz") is None:
            try:
                cur["freq_mhz"] = int(float(m_freq.group(1)))
            except Exception:
                pass

    _finalize_current()
    return aps


def _parse_iw_dev_ap_ifaces(iw_text: str) -> Set[str]:
    return {ap.ifname for ap in _parse_iw_dev_ap_info(iw_text) if ap.ifname}


def _band_from_freq_mhz(freq_mhz: Optional[int]) -> Optional[str]:
    if freq_mhz is None:
        return None
    if 2400 <= freq_mhz <= 2500:
        return "2.4ghz"
    if 4900 <= freq_mhz <= 5900:
        return "5ghz"
    if 5925 <= freq_mhz <= 7125:
        return "6ghz"
    return None


def _vendor_bin() -> Path:
    here = Path(__file__).resolve()
    backend_dir = here.parents[1]
    return backend_dir / "vendor" / "bin"


def _hostapd_cli_path() -> Optional[str]:
    vendor = _vendor_bin() / "hostapd_cli"
    if vendor.exists() and os.access(vendor, os.X_OK):
        return str(vendor)
    bundled = _vendor_bin() / "hostapd"
    if bundled.exists() and os.access(bundled, os.X_OK):
        cand = bundled.parent / "hostapd_cli"
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("hostapd_cli")


def _select_ap_from_iw(
    iw_text: str,
    *,
    target_phy: Optional[str],
    ssid: Optional[str],
) -> Optional[APReadyInfo]:
    aps = _parse_iw_dev_ap_info(iw_text)
    want_ssid = ssid.strip() if isinstance(ssid, str) and ssid.strip() else None

    def _filter(items: List[APReadyInfo], match_ssid: bool, match_phy: bool) -> List[APReadyInfo]:
        out: List[APReadyInfo] = []
        for ap in items:
            if ap.freq_mhz is None:
                continue
            if match_ssid and want_ssid and ap.ssid != want_ssid:
                continue
            if match_phy and target_phy and ap.phy != target_phy:
                continue
            out.append(ap)
        return out

    candidates: List[APReadyInfo] = []
    if want_ssid and target_phy:
        candidates = _filter(aps, match_ssid=True, match_phy=True)
    if not candidates and want_ssid:
        candidates = _filter(aps, match_ssid=True, match_phy=False)
    if not candidates and target_phy:
        candidates = _filter(aps, match_ssid=False, match_phy=True)
    if not candidates and not (want_ssid or target_phy):
        candidates = _filter(aps, match_ssid=False, match_phy=False)

    if not candidates:
        return None
    candidates.sort(key=lambda ap: ap.ifname)
    return candidates[0]


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
    ssid: Optional[str] = None,
    adapter_ifname: Optional[str] = None,
) -> Optional[APReadyInfo]:
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        dump = _iw_dev_dump()
        ap = _select_ap_from_iw(dump, target_phy=target_phy, ssid=ssid)
        if ap and _hostapd_ready(ap.ifname, adapter_ifname=adapter_ifname):
            return ap

        time.sleep(poll_s)

    return None


def _pid_cmdline(pid: int) -> str:
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
        return raw.decode("utf-8", "ignore").replace("\x00", " ").strip()
    except Exception:
        return ""


def _safe_revert_tuning(tuning_state: Optional[Dict[str, object]]) -> List[str]:
    try:
        return system_tuning.revert(tuning_state)
    except Exception as e:
        return [f"system_tuning_revert_failed:{e}"]


def _safe_revert_network_tuning(net_state: Optional[Dict[str, object]]) -> List[str]:
    try:
        return network_tuning.revert(net_state)
    except Exception as e:
        return [f"network_tuning_revert_failed:{e}"]


def _child_pids(pid: Optional[int]) -> List[int]:
    if not pid or pid <= 0:
        return []
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text().strip()
    except Exception:
        return []
    if not raw:
        return []
    out: List[int] = []
    for tok in raw.split():
        if tok.isdigit():
            out.append(int(tok))
    return out


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


def _pid_is_hostapd(pid: int) -> bool:
    cmdline = _pid_cmdline(pid)
    return "hostapd" in cmdline.lower()


def _pid_is_dnsmasq(pid: int) -> bool:
    cmdline = _pid_cmdline(pid)
    return "dnsmasq" in cmdline.lower()


def _pid_running(pid: int) -> bool:
    return lnxrouter_conf.pid_running(pid)


def _candidate_conf_dirs(adapter_ifname: Optional[str]) -> List[Path]:
    return lnxrouter_conf.candidate_conf_dirs(adapter_ifname, tmp_dir=_LNXROUTER_TMP)


def _find_latest_conf_dir(adapter_ifname: Optional[str], ap_interface: Optional[str]) -> Optional[Path]:
    return lnxrouter_conf.find_latest_conf_dir(
        adapter_ifname,
        ap_interface,
        tmp_dir=_LNXROUTER_TMP,
    )


def _find_ctrl_dir(conf_dir: Optional[Path], ap_interface: str) -> Optional[Path]:
    return lnxrouter_conf.find_ctrl_dir(
        conf_dir,
        ap_interface,
        extra_candidates=list(_HOSTAPD_CTRL_CANDIDATES),
    )


def _hostapd_cli_ping(ctrl_dir: Path, ap_interface: str) -> bool:
    binpath = _hostapd_cli_path()
    if not binpath:
        return False
    try:
        p = subprocess.run(
            [binpath, "-p", str(ctrl_dir), "-i", ap_interface, "ping"],
            capture_output=True,
            text=True,
            timeout=0.8,
        )
    except Exception:
        return False
    if p.returncode != 0:
        return False
    return "PONG" in (p.stdout or "")


def _hostapd_pid_running(conf_dir: Path) -> bool:
    pid = lnxrouter_conf.read_pid_file(conf_dir / "hostapd.pid")
    if pid is None or not _pid_running(pid):
        return False
    return _pid_is_hostapd(pid)


def _dnsmasq_pid_running(conf_dir: Path) -> bool:
    pid = lnxrouter_conf.read_pid_file(conf_dir / "dnsmasq.pid")
    if pid is None or not _pid_running(pid):
        return False
    return _pid_is_dnsmasq(pid)


def _hostapd_ready(ap_interface: str, *, adapter_ifname: Optional[str]) -> bool:
    conf_dir = _find_latest_conf_dir(adapter_ifname, ap_interface)
    if conf_dir and _hostapd_pid_running(conf_dir):
        return True
    ctrl_dir = _find_ctrl_dir(conf_dir, ap_interface)
    if ctrl_dir and _hostapd_cli_ping(ctrl_dir, ap_interface):
        return True
    return False


def _read_log_tail(path: Path, max_lines: int = 200) -> List[str]:
    if not path.exists():
        return []
    try:
        mode = path.stat().st_mode
    except Exception:
        return []
    data = ""
    try:
        if stat.S_ISFIFO(mode):
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                raw = os.read(fd, 65536)
            finally:
                os.close(fd)
            data = raw.decode("utf-8", "ignore") if raw else ""
        else:
            data = path.read_text(errors="ignore")
    except Exception:
        return []
    if not data:
        return []
    lines = data.splitlines()
    return lines[-max_lines:]


def _collect_ap_logs(adapter_ifname: Optional[str], ap_interface: Optional[str]) -> List[str]:
    conf_dir = _find_latest_conf_dir(adapter_ifname, ap_interface)
    if not conf_dir:
        return []
    logs: List[str] = []
    log_paths = [
        ("hostapd", conf_dir / "hostapd.log"),
        ("dnsmasq", conf_dir / "dnsmasq.log"),
    ]
    for label, path in log_paths:
        for line in _read_log_tail(path, max_lines=200):
            logs.append(f"[{label}] {line}")
    return logs[-200:]


def _find_hostapd_pids(adapter_ifname: Optional[str]) -> List[int]:
    pids: List[int] = []
    for conf_dir in _candidate_conf_dirs(adapter_ifname):
        pid = lnxrouter_conf.read_pid_file(conf_dir / "hostapd.pid")
        if pid and _pid_running(pid) and _pid_is_hostapd(pid):
            pids.append(pid)
    return sorted(set(pids))


def _find_dnsmasq_pids(adapter_ifname: Optional[str]) -> List[int]:
    pids: List[int] = []
    for conf_dir in _candidate_conf_dirs(adapter_ifname):
        pid = lnxrouter_conf.read_pid_file(conf_dir / "dnsmasq.pid")
        if pid and _pid_running(pid) and _pid_is_dnsmasq(pid):
            pids.append(pid)
    return sorted(set(pids))


def _collect_affinity_pids(
    *,
    adapter_ifname: Optional[str],
    ap_interface: Optional[str],
    engine_pid: Optional[int],
) -> List[int]:
    pids: List[int] = []

    conf_dir = _find_latest_conf_dir(adapter_ifname, ap_interface)
    if conf_dir:
        for name, matcher in (("hostapd.pid", _pid_is_hostapd), ("dnsmasq.pid", _pid_is_dnsmasq)):
            pid = lnxrouter_conf.read_pid_file(conf_dir / name)
            if pid and _pid_running(pid) and matcher(pid):
                pids.append(pid)

    if engine_pid and not pids:
        for child in _child_pids(engine_pid):
            cmd = _pid_cmdline(child).lower()
            if "hostapd" in cmd or "dnsmasq" in cmd:
                pids.append(child)

    if engine_pid:
        pids.append(engine_pid)

    return sorted(set(pids))


def _watchdog_enabled(cfg: Optional[Dict[str, object]]) -> bool:
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get("watchdog_enable", True))


def _watchdog_interval(cfg: Optional[Dict[str, object]]) -> float:
    if not isinstance(cfg, dict):
        return 2.0
    try:
        val = float(cfg.get("watchdog_interval_s", 2.0))
        return max(0.5, min(10.0, val))
    except Exception:
        return 2.0


def _watchdog_reason(state: Dict[str, Any], cfg: Dict[str, object]) -> Optional[str]:
    adapter_ifname = state.get("adapter") if isinstance(state, dict) else None
    ap_interface = state.get("ap_interface") if isinstance(state, dict) else None
    engine_pid = state.get("engine", {}).get("pid") if isinstance(state, dict) else None
    expect_dns = not bool(cfg.get("bridge_mode", False))

    conf_dir = _find_latest_conf_dir(adapter_ifname, ap_interface)
    if conf_dir:
        if not _hostapd_pid_running(conf_dir):
            return "hostapd_exited"
        if expect_dns and not _dnsmasq_pid_running(conf_dir):
            return "dnsmasq_exited"
        # Check connection quality if monitoring is enabled
        if bool(cfg.get("connection_quality_monitoring", True)):
            quality_reason = _check_connection_quality(state, cfg)
            if quality_reason:
                return quality_reason
        return None

    if engine_pid and _pid_running(engine_pid):
        children = _child_pids(engine_pid)
        has_hostapd = any(_pid_is_hostapd(pid) for pid in children)
        has_dnsmasq = any(_pid_is_dnsmasq(pid) for pid in children)
        if not has_hostapd:
            return "hostapd_missing"
        if expect_dns and not has_dnsmasq:
            return "dnsmasq_missing"
        # Check connection quality
        if bool(cfg.get("connection_quality_monitoring", True)):
            quality_reason = _check_connection_quality(state, cfg)
            if quality_reason:
                return quality_reason
        return None

    if not _find_hostapd_pids(adapter_ifname):
        return "hostapd_missing"
    if expect_dns and not _find_dnsmasq_pids(adapter_ifname):
        return "dnsmasq_missing"
    return None


def _check_connection_quality(state: Dict[str, Any], cfg: Dict[str, object]) -> Optional[str]:
    """Check connection quality and return reason if quality is degraded."""
    try:
        from vr_hotspotd import telemetry
        
        adapter_ifname = state.get("adapter")
        telemetry_enabled = bool(cfg.get("telemetry_enable", True))
        if not telemetry_enabled:
            return None
        
        interval = float(cfg.get("telemetry_interval_s", 2.0))
        telemetry_data = telemetry.get_snapshot(
            adapter_ifname=adapter_ifname,
            enabled=True,
            interval_s=interval,
        )
        
        if not telemetry_data.get("enabled"):
            return None
        
        summary = telemetry_data.get("summary", {})
        quality_score = summary.get("quality_score_avg")
        
        # If quality score is below threshold, trigger restart
        if quality_score is not None and quality_score < 50.0:  # Threshold: 50/100
            loss_pct = summary.get("loss_pct_avg")
            rssi_min = summary.get("rssi_min_dbm")
            
            if loss_pct is not None and loss_pct > 5.0:
                return f"connection_quality_degraded:loss={loss_pct:.1f}%"
            if rssi_min is not None and rssi_min < -85:
                return f"connection_quality_degraded:rssi={rssi_min}dBm"
            return f"connection_quality_degraded:score={quality_score:.1f}"
    except Exception:
        pass  # Best-effort, don't fail watchdog on telemetry errors
    
    return None


def _restart_from_watchdog(reason: str) -> None:
    cid = f"watchdog-{int(time.time())}"
    
    # Check if auto channel switch is enabled and reason is quality-related
    cfg = load_config()
    auto_switch = bool(cfg.get("auto_channel_switch", False))
    
    if auto_switch and "connection_quality" in reason:
        # Try to switch to a better channel
        st = load_state()
        adapter_ifname = st.get("adapter")
        band = st.get("band", "5ghz")
        
        if adapter_ifname:
            try:
                best_channel = select_best_channel(adapter_ifname, band)
                if best_channel:
                    # Update config with new channel
                    if band == "6ghz":
                        cfg["channel_6g"] = best_channel
                    elif band == "2.4ghz":
                        cfg["fallback_channel_2g"] = best_channel
                    # Note: For 5GHz, channel selection is handled by lnxrouter
                    from vr_hotspotd.config import write_config_file
                    write_config_file({"channel_6g" if band == "6ghz" else "fallback_channel_2g": best_channel})
            except Exception:
                pass  # Best-effort
    
    with _OP_LOCK:
        _stop_hotspot_impl(correlation_id=cid + ":stop")
        _start_hotspot_impl(correlation_id=cid + ":start")

    try:
        st = load_state()
        warnings = list(st.get("warnings") if isinstance(st, dict) and st.get("warnings") else [])
        warnings.append(f"watchdog_restart:{reason}")
        update_state(warnings=warnings)
    except Exception:
        pass


def _watchdog_loop() -> None:
    backoff_s = 2.0
    next_restart = 0.0
    while not _WATCHDOG_STOP.is_set():
        cfg = load_config()
        interval = _watchdog_interval(cfg)
        if _WATCHDOG_STOP.wait(interval):
            break

        if not _watchdog_enabled(cfg):
            backoff_s = max(2.0, interval)
            continue

        st = load_state()
        if not st.get("running") or st.get("phase") != "running":
            backoff_s = max(2.0, interval)
            continue

        if not is_running():
            reason = "engine_not_running"
        else:
            reason = _watchdog_reason(st, cfg)

        if not reason:
            backoff_s = max(2.0, interval)
            next_restart = 0.0
            
            # Auto-adjust TX power based on telemetry (if enabled and tx_power is None/auto)
            tx_power_cfg = cfg.get("tx_power")
            if tx_power_cfg is None:  # Auto mode
                try:
                    from vr_hotspotd import telemetry
                    adapter_ifname = st.get("adapter")
                    if adapter_ifname:
                        telemetry_data = telemetry.get_snapshot(
                            adapter_ifname=adapter_ifname,
                            enabled=True,
                            interval_s=interval,
                        )
                        summary = telemetry_data.get("summary", {})
                        rssi_avg = summary.get("rssi_avg_dbm")
                        if rssi_avg is not None:
                            current_power = get_tx_power(adapter_ifname)
                            new_power = auto_adjust_tx_power(adapter_ifname, rssi_avg, current_power)
                            if new_power is not None:
                                ok, msg = set_tx_power(adapter_ifname, new_power)
                                if ok:
                                    # Update config
                                    from vr_hotspotd.config import write_config_file
                                    write_config_file({"tx_power": new_power})
                except Exception:
                    pass  # Best-effort
            
            continue

        now = time.time()
        if next_restart and now < next_restart:
            continue

        delay = min(_WATCHDOG_BACKOFF_MAX_S, max(backoff_s, interval))
        next_restart = now + delay
        backoff_s = min(_WATCHDOG_BACKOFF_MAX_S, delay * 2)
        _restart_from_watchdog(reason)


def _ensure_watchdog_started() -> None:
    global _WATCHDOG_THREAD
    if _WATCHDOG_THREAD and _WATCHDOG_THREAD.is_alive():
        return
    _WATCHDOG_STOP.clear()
    _WATCHDOG_THREAD = threading.Thread(target=_watchdog_loop, daemon=True)
    _WATCHDOG_THREAD.start()


def _remove_conf_dirs(adapter_ifname: Optional[str]) -> List[str]:
    removed: List[str] = []
    for conf_dir in _candidate_conf_dirs(adapter_ifname):
        try:
            shutil.rmtree(conf_dir, ignore_errors=True)
            removed.append(conf_dir.name)
        except Exception:
            pass
    return removed


def _kill_runtime_processes(
    adapter_ifname: Optional[str],
    *,
    firewalld_cfg: Optional[Dict[str, object]] = None,
    stop_engine_first: bool = True,
) -> None:
    if stop_engine_first:
        try:
            stop_engine(firewalld_cfg=firewalld_cfg)
        except Exception:
            pass

    for pid in _find_our_lnxrouter_pids():
        _kill_pid(pid)

    for pid in _find_hostapd_pids(adapter_ifname):
        _kill_pid(pid)

    for pid in _find_dnsmasq_pids(adapter_ifname):
        _kill_pid(pid)


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
    enable_internet = bool(cfg.get("enable_internet", True))
    if bool(cfg.get("bridge_mode", False)):
        enable_internet = False
    return {
        "firewalld_enabled": bool(cfg.get("firewalld_enabled", True)),
        "firewalld_zone": str(cfg.get("firewalld_zone", "trusted")),
        "firewalld_enable_masquerade": enable_internet and bool(cfg.get("firewalld_enable_masquerade", True)),
        "firewalld_enable_forward": enable_internet and bool(cfg.get("firewalld_enable_forward", False)),
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
    tuning_warnings = _safe_revert_tuning(st.get("tuning") if isinstance(st, dict) else None)
    net_warnings = _safe_revert_network_tuning(
        st.get("network_tuning") if isinstance(st, dict) else None
    )
    removed_ifaces: List[str] = []
    removed_conf_dirs: List[str] = []

    try:
        inv = get_adapters()
        preferred = cfg.get("ap_adapter")
        if preferred and isinstance(preferred, str) and preferred.strip():
            ap_ifname = preferred.strip()
        else:
            ap_ifname = inv.get("recommended") or _select_ap_adapter(inv, cfg.get("band_preference", "5ghz"))
        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception:
        ap_ifname = None
        target_phy = None

    _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
    removed_conf_dirs = _remove_conf_dirs(ap_ifname)

    try:
        removed_ifaces = _cleanup_virtual_ap_ifaces(target_phy=target_phy)
    except Exception:
        removed_ifaces = []

    warnings: List[str] = []
    warnings.extend(tuning_warnings)
    warnings.extend(net_warnings)
    if removed_ifaces:
        warnings.append("repair_removed_virtual_ap_ifaces:" + ",".join(removed_ifaces))
    if removed_conf_dirs:
        warnings.append("repair_removed_lnxrouter_conf_dirs:" + ",".join(removed_conf_dirs))

    st = update_state(
        running=False,
        phase="stopped",
        ap_interface=None,
        last_error=None,
        last_op="repair",
        last_correlation_id=correlation_id,
        warnings=warnings,
        tuning={},
        network_tuning={},
        engine={
            "pid": None,
            "cmd": None,
            "started_ts": None,
            "last_exit_code": None,
            "last_error": None,
            "stdout_tail": [],
            "stderr_tail": [],
            "ap_logs_tail": [],
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
        ap_interface=None,
        engine={"ap_logs_tail": []},
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
    enable_internet = bool(cfg.get("enable_internet", True))
    bridge_mode = bool(cfg.get("bridge_mode", False))
    bridge_name = cfg.get("bridge_name")
    bridge_uplink = cfg.get("bridge_uplink")

    def _norm_str(v: object) -> Optional[str]:
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None

    gateway_ip = _norm_str(cfg.get("lan_gateway_ip"))
    dhcp_start_ip = _norm_str(cfg.get("dhcp_start_ip"))
    dhcp_end_ip = _norm_str(cfg.get("dhcp_end_ip"))
    dhcp_dns = _norm_str(cfg.get("dhcp_dns"))

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
            ap_interface=None,
            last_error=err,
            last_correlation_id=correlation_id,
            engine={"last_error": err, "ap_logs_tail": []},
        )
        return LifecycleResult("start_failed", state)

    try:
        inv = get_adapters()
        inv_error = inv.get("error")
        if inv_error and not inv.get("adapters"):
            raise RuntimeError(inv_error)

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

        # Apply adapter-specific profile optimizations
        try:
            cfg = apply_adapter_profile(cfg, a)
        except Exception:
            pass  # Best-effort, continue if profile application fails

        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception as e:
        state = update_state(
            phase="error",
            running=False,
            ap_interface=None,
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
                "ap_logs_tail": [],
            },
        )
        return LifecycleResult("start_failed", state)

    wifi6_setting = cfg.get("wifi6", "auto")
    if isinstance(wifi6_setting, str):
        s = wifi6_setting.strip().lower()
        if s == "auto":
            wifi6_setting = "auto"
        elif s in ("1", "true", "yes", "on", "y"):
            wifi6_setting = True
        elif s in ("0", "false", "no", "off", "n"):
            wifi6_setting = False
        else:
            wifi6_setting = "auto"

    supports_wifi6 = bool(a.get("supports_wifi6"))
    effective_wifi6 = False
    start_warnings: List[str] = []

    if wifi6_setting == "auto":
        effective_wifi6 = supports_wifi6
    elif wifi6_setting is True:
        effective_wifi6 = supports_wifi6
        if not supports_wifi6:
            start_warnings.append("wifi6_not_supported_on_adapter")
    elif wifi6_setting is False:
        effective_wifi6 = False
    else:
        effective_wifi6 = supports_wifi6

    if start_warnings:
        update_state(warnings=start_warnings)

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
            ap_interface=None,
            last_error=err,
            last_correlation_id=correlation_id,
            engine={"last_error": err, "ap_logs_tail": []},
        )
        return LifecycleResult("start_failed", state)

    preflight_result = preflight.run(
        cfg,
        adapter=a if isinstance(a, dict) else None,
        band=bp,
        ap_security=ap_security,
        enable_internet=enable_internet,
    )
    if preflight_result.get("warnings"):
        start_warnings.extend([str(w) for w in preflight_result.get("warnings")])
    preflight_errors = [str(e) for e in preflight_result.get("errors") or []]
    if preflight_errors:
        start_warnings.extend([f"preflight_error:{e}" for e in preflight_errors])
    update_state(preflight=preflight_result)

    try:
        tuning_state, tuning_warnings = system_tuning.apply_pre(cfg)
    except Exception as e:
        tuning_state = {}
        tuning_warnings = [f"system_tuning_pre_failed:{e}"]
    if tuning_warnings:
        start_warnings.extend(tuning_warnings)

    # Attempt 1: requested band
    if bridge_mode:
        bridge_channel: Optional[int] = None
        if bp == "6ghz":
            bridge_channel = cfg.get("channel_6g", None)
            if bridge_channel is not None:
                try:
                    bridge_channel = int(bridge_channel)
                except Exception:
                    bridge_channel = None
        elif bp == "2.4ghz":
            try:
                bridge_channel = int(cfg.get("fallback_channel_2g", 6))
            except Exception:
                bridge_channel = 6

        channel_width = str(cfg.get("channel_width", "auto")).lower()
        beacon_interval = int(cfg.get("beacon_interval", 50))
        dtim_period = int(cfg.get("dtim_period", 1))
        short_guard_interval = bool(cfg.get("short_guard_interval", True))
        tx_power = cfg.get("tx_power")
        if tx_power is not None:
            try:
                tx_power = int(tx_power)
            except Exception:
                tx_power = None

        cmd1 = build_cmd_bridge(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            band=bp,
            ap_security=ap_security,
            country=country if isinstance(country, str) else None,
            channel=bridge_channel,
            no_virt=optimized_no_virt,
            debug=debug,
            wifi6=effective_wifi6,
            bridge_name=str(bridge_name).strip() if isinstance(bridge_name, str) else None,
            bridge_uplink=str(bridge_uplink).strip() if isinstance(bridge_uplink, str) else None,
            channel_width=channel_width,
            beacon_interval=beacon_interval,
            dtim_period=dtim_period,
            short_guard_interval=short_guard_interval,
            tx_power=tx_power,
        )
    elif bp == "6ghz":
        channel_6g = cfg.get("channel_6g", None)
        
        # Auto-select channel if enabled
        channel_auto_select = bool(cfg.get("channel_auto_select", False))
        if channel_auto_select and (channel_6g is None or channel_6g == 0):
            try:
                best_channel = select_best_channel(ap_ifname, "6ghz", channel_6g)
                if best_channel:
                    channel_6g = best_channel
                    # Update config with selected channel
                    from vr_hotspotd.config import write_config_file
                    write_config_file({"channel_6g": best_channel})
            except Exception:
                pass  # Best-effort, continue with default
        
        if channel_6g is not None:
            try:
                channel_6g = int(channel_6g)
            except Exception:
                channel_6g = None

        channel_width = str(cfg.get("channel_width", "auto")).lower()
        beacon_interval = int(cfg.get("beacon_interval", 50))
        dtim_period = int(cfg.get("dtim_period", 1))
        short_guard_interval = bool(cfg.get("short_guard_interval", True))
        tx_power = cfg.get("tx_power")
        if tx_power is not None:
            try:
                tx_power = int(tx_power)
            except Exception:
                tx_power = None

        cmd1 = build_cmd_6ghz(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            country=country if isinstance(country, str) else None,
            channel=channel_6g,
            no_virt=optimized_no_virt,
            debug=debug,
            gateway_ip=gateway_ip,
            dhcp_start_ip=dhcp_start_ip,
            dhcp_end_ip=dhcp_end_ip,
            dhcp_dns=dhcp_dns,
            enable_internet=enable_internet,
            channel_width=channel_width,
            beacon_interval=beacon_interval,
            dtim_period=dtim_period,
            short_guard_interval=short_guard_interval,
            tx_power=tx_power,
        )
    else:
        # Auto-select channel for 5GHz/2.4GHz if enabled
        selected_channel = None
        channel_auto_select = bool(cfg.get("channel_auto_select", False))
        if channel_auto_select:
            try:
                best_channel = select_best_channel(ap_ifname, bp, None)
                if best_channel:
                    selected_channel = best_channel
            except Exception:
                pass  # Best-effort
        
        cmd1 = build_cmd(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            band_preference=bp,
            country=country if isinstance(country, str) else None,
            channel=selected_channel,
            no_virt=optimized_no_virt,
            wifi6=effective_wifi6,
            gateway_ip=gateway_ip,
            dhcp_dns=dhcp_dns,
            enable_internet=enable_internet,
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
            "ap_logs_tail": [],
        },
    )

    ap_info = None
    start_failure_reason = None
    if not res.ok:
        start_failure_reason = res.error or "engine_start_failed"
    else:
        ap_info = _wait_for_ap_ready(target_phy, ap_ready_timeout_s, ssid=ssid, adapter_ifname=ap_ifname)
        if not ap_info:
            start_failure_reason = "ap_ready_timeout"

    if ap_info:
        detected_band = _band_from_freq_mhz(ap_info.freq_mhz)
        affinity_pids = _collect_affinity_pids(
            adapter_ifname=ap_ifname,
            ap_interface=ap_info.ifname,
            engine_pid=res.pid,
        )
        try:
            tuning_state, runtime_warnings = system_tuning.apply_runtime(
                tuning_state,
                cfg,
                ap_ifname=ap_info.ifname,
                adapter_ifname=ap_ifname,
                cpu_affinity_pids=affinity_pids,
            )
        except Exception as e:
            runtime_warnings = [f"system_tuning_runtime_failed:{e}"]
        if runtime_warnings:
            start_warnings.extend(runtime_warnings)
        try:
            net_state, net_warnings = network_tuning.apply(
                cfg,
                ap_ifname=ap_info.ifname,
                enable_internet=enable_internet,
                firewalld_cfg=fw_cfg,
            )
        except Exception as e:
            net_state = {}
            net_warnings = [f"network_tuning_apply_failed:{e}"]
        if net_warnings:
            start_warnings.extend(net_warnings)
        state = update_state(
            phase="running",
            running=True,
            ap_interface=ap_info.ifname,
            band=detected_band,
            mode="optimized",
            fallback_reason=None,
            warnings=start_warnings,
            last_error=None,
            last_correlation_id=correlation_id,
            tuning=tuning_state,
            network_tuning=net_state,
            engine={"last_error": None, "last_exit_code": None, "ap_logs_tail": []},
        )
        if _watchdog_enabled(cfg) and is_running():
            _ensure_watchdog_started()
        return LifecycleResult("started", state)

    # If requested band failed to become ready, fallback (6 -> 5 -> 2.4).
    ap_candidate = None
    try:
        ap_candidate = _select_ap_from_iw(_iw_dev_dump(), target_phy=target_phy, ssid=ssid)
    except Exception:
        ap_candidate = None
    ap_logs = _collect_ap_logs(ap_ifname, ap_candidate.ifname if ap_candidate else None)
    if ap_logs:
        update_state(engine={"ap_logs_tail": ap_logs})
    _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
    _remove_conf_dirs(ap_ifname)

    warnings: List[str] = list(start_warnings)
    if start_failure_reason == "ap_ready_timeout":
        warnings.append("optimized_ap_start_timed_out")
    else:
        warnings.append(f"optimized_start_failed:{start_failure_reason or 'engine_start_failed'}")
    fallback_chain: List[Tuple[str, Optional[int], bool, str]] = []

    if bridge_mode:
        revert_warnings = _safe_revert_tuning(tuning_state)
        warnings.extend(revert_warnings)
        last_error = "ap_ready_timeout_bridge_mode"
        if start_failure_reason and start_failure_reason != "ap_ready_timeout":
            last_error = start_failure_reason
        state = update_state(
            phase="error",
            running=False,
            ap_interface=None,
            last_error=last_error,
            last_correlation_id=correlation_id,
            fallback_reason=None,
            warnings=warnings,
            tuning={},
            network_tuning={},
        )
        return LifecycleResult("start_failed", state)

    if bp == "6ghz":
        fallback_chain = [
            ("5ghz", None, optimized_no_virt, "fallback_to_5ghz"),
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), optimized_no_virt, "fallback_to_2_4ghz"),
        ]
    elif bp == "5ghz":
        fallback_chain = [
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), optimized_no_virt, "fallback_to_2_4ghz"),
        ]
    else:
        revert_warnings = _safe_revert_tuning(tuning_state)
        warnings.extend(revert_warnings)
        last_error = "ap_ready_timeout"
        if start_failure_reason and start_failure_reason != "ap_ready_timeout":
            last_error = start_failure_reason
        state = update_state(
            phase="error",
            running=False,
            ap_interface=None,
            last_error=last_error,
            last_correlation_id=correlation_id,
            fallback_reason=None,
            warnings=warnings,
            tuning={},
            network_tuning={},
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
            wifi6=effective_wifi6,
            gateway_ip=gateway_ip,
            dhcp_dns=dhcp_dns,
            enable_internet=enable_internet,
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
                "ap_logs_tail": [],
            },
        )

        if not res_fallback.ok:
            warnings.append(
                f"fallback_start_failed:{res_fallback.error or 'engine_start_failed_fallback'}"
            )
            ap_info_fallback = None
        else:
            ap_info_fallback = _wait_for_ap_ready(
                target_phy, ap_ready_timeout_s, ssid=ssid, adapter_ifname=ap_ifname
            )

        if ap_info_fallback:
            detected_band = _band_from_freq_mhz(ap_info_fallback.freq_mhz)
            affinity_pids = _collect_affinity_pids(
                adapter_ifname=ap_ifname,
                ap_interface=ap_info_fallback.ifname,
                engine_pid=res_fallback.pid,
            )
            try:
                tuning_state, runtime_warnings = system_tuning.apply_runtime(
                    tuning_state,
                    cfg,
                    ap_ifname=ap_info_fallback.ifname,
                    adapter_ifname=ap_ifname,
                    cpu_affinity_pids=affinity_pids,
                )
            except Exception as e:
                runtime_warnings = [f"system_tuning_runtime_failed:{e}"]
            if runtime_warnings:
                warnings.extend(runtime_warnings)
            try:
                net_state, net_warnings = network_tuning.apply(
                    cfg,
                    ap_ifname=ap_info_fallback.ifname,
                    enable_internet=enable_internet,
                    firewalld_cfg=fw_cfg,
                )
            except Exception as e:
                net_state = {}
                net_warnings = [f"network_tuning_apply_failed:{e}"]
            if net_warnings:
                warnings.extend(net_warnings)
            state = update_state(
                phase="running",
                running=True,
                ap_interface=ap_info_fallback.ifname,
                band=detected_band,
                mode="fallback",
                fallback_reason="ap_ready_timeout",
                warnings=warnings,
                last_error=None,
                last_correlation_id=correlation_id,
                tuning=tuning_state,
                network_tuning=net_state,
                engine={"last_error": None, "last_exit_code": None, "ap_logs_tail": []},
            )
            if _watchdog_enabled(cfg) and is_running():
                _ensure_watchdog_started()
            return LifecycleResult("started_with_fallback", state)

        ap_candidate = None
        try:
            ap_candidate = _select_ap_from_iw(_iw_dev_dump(), target_phy=target_phy, ssid=ssid)
        except Exception:
            ap_candidate = None
        ap_logs = _collect_ap_logs(ap_ifname, ap_candidate.ifname if ap_candidate else None)
        if ap_logs:
            update_state(engine={"ap_logs_tail": ap_logs})
        _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
        _remove_conf_dirs(ap_ifname)

    revert_warnings = _safe_revert_tuning(tuning_state)
    warnings.extend(revert_warnings)
    state = update_state(
        phase="error",
        running=False,
        ap_interface=None,
        last_error="ap_ready_timeout_after_fallback",
        last_correlation_id=correlation_id,
        fallback_reason="ap_ready_timeout",
        warnings=warnings,
        tuning={},
        network_tuning={},
    )
    return LifecycleResult("start_failed", state)


def stop_hotspot(correlation_id: str = "stop"):
    with _OP_LOCK:
        return _stop_hotspot_impl(correlation_id=correlation_id)


def _stop_hotspot_impl(correlation_id: str = "stop"):
    state = load_state()
    tuning_warnings = _safe_revert_tuning(state.get("tuning") if isinstance(state, dict) else None)
    net_warnings = _safe_revert_network_tuning(
        state.get("network_tuning") if isinstance(state, dict) else None
    )

    if state["phase"] == "stopped":
        return LifecycleResult("already_stopped", state)

    cfg = load_config()
    fw_cfg = _build_firewalld_cfg(cfg)
    adapter_ifname = state.get("adapter") if isinstance(state, dict) else None

    state = update_state(
        phase="stopping",
        last_op="stop",
        last_correlation_id=correlation_id,
        last_error=None,
    )

    ok, rc, out_tail, err_tail, err = stop_engine(firewalld_cfg=fw_cfg)

    _kill_runtime_processes(adapter_ifname, firewalld_cfg=fw_cfg, stop_engine_first=False)
    removed_conf_dirs = _remove_conf_dirs(adapter_ifname)

    removed_ifaces: List[str] = []
    try:
        removed_ifaces = _cleanup_virtual_ap_ifaces(target_phy=None)
    except Exception:
        removed_ifaces = []

    warnings: List[str] = []
    warnings.extend(tuning_warnings)
    warnings.extend(net_warnings)
    if removed_ifaces:
        warnings.append("stop_removed_virtual_ap_ifaces:" + ",".join(removed_ifaces))
    if removed_conf_dirs:
        warnings.append("stop_removed_lnxrouter_conf_dirs:" + ",".join(removed_conf_dirs))

    state = update_state(
        engine={
            "pid": None,
            "cmd": None,
            "started_ts": None,
            "last_exit_code": rc,
            "last_error": err,
            "stdout_tail": out_tail,
            "stderr_tail": err_tail,
            "ap_logs_tail": [],
        }
    )

    state = update_state(
        phase="stopped",
        running=False,
        adapter=None,
        ap_interface=None,
        band=None,
        mode=None,
        fallback_reason=None,
        warnings=warnings,
        last_error=(err if not ok else None),
        last_correlation_id=correlation_id,
        tuning={},
        network_tuning={},
    )

    return LifecycleResult("stopped" if ok else "stop_failed", state)
