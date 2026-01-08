import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SYSCTL_TUNING_DEFAULTS: Dict[str, str] = {
    "net.core.rmem_max": "134217728",
    "net.core.wmem_max": "134217728",
    "net.core.rmem_default": "262144",
    "net.core.wmem_default": "262144",
    "net.core.netdev_max_backlog": "50000",
    "net.ipv4.tcp_rmem": "4096 87380 134217728",
    "net.ipv4.tcp_wmem": "4096 65536 134217728",
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
}


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
    return False


def _sysctl_path(key: str) -> Path:
    return Path("/proc/sys") / Path(key.replace(".", "/"))


def _read_sysctl(key: str) -> Optional[str]:
    path = _sysctl_path(key)
    if not path.exists():
        return None
    try:
        return path.read_text(errors="ignore").strip()
    except Exception:
        return None


def _write_sysctl(key: str, value: str) -> bool:
    path = _sysctl_path(key)
    if not path.exists():
        return False
    try:
        path.write_text(str(value).strip() + "\n")
        return True
    except Exception:
        return False


def _available_congestion_controls() -> List[str]:
    raw = _read_sysctl("net.ipv4.tcp_available_congestion_control") or ""
    return [s.strip() for s in raw.split() if s.strip()]


def _cpu_governor_paths() -> List[Path]:
    roots = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*"))
    out: List[Path] = []
    for cpu in roots:
        path = cpu / "cpufreq" / "scaling_governor"
        if path.exists():
            out.append(path)
    return out


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="ignore").strip()
    except Exception:
        return None


def _write_text(path: Path, value: str) -> bool:
    try:
        path.write_text(str(value).strip() + "\n")
        return True
    except Exception:
        return False


def _find_usb_power_control_paths(ifname: Optional[str]) -> List[Path]:
    if not ifname:
        return []
    dev_link = Path("/sys/class/net") / ifname / "device"
    if not dev_link.exists():
        return []
    try:
        dev_path = dev_link.resolve()
    except Exception:
        return []
    if "/usb" not in str(dev_path):
        return []
    paths: List[Path] = []
    cur = dev_path
    while cur and cur != cur.parent:
        control = cur / "power" / "control"
        if control.exists():
            paths.append(control)
        cur = cur.parent
    return list(dict.fromkeys(paths))


def _iw_bin() -> Optional[str]:
    return shutil.which("iw") or ("/usr/sbin/iw" if os.path.exists("/usr/sbin/iw") else None)


def _get_power_save(ifname: str) -> Optional[str]:
    iw = _iw_bin()
    if not iw:
        return None
    try:
        p = subprocess.run([iw, "dev", ifname, "get", "power_save"], capture_output=True, text=True)
    except Exception:
        return None
    out = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"Power save:\s*(on|off)", out, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()


def _set_power_save(ifname: str, enabled: bool) -> bool:
    iw = _iw_bin()
    if not iw:
        return False
    state = "on" if enabled else "off"
    try:
        p = subprocess.run([iw, "dev", ifname, "set", "power_save", state], capture_output=True, text=True)
    except Exception:
        return False
    return p.returncode == 0


def _parse_cpu_affinity(value: Optional[str]) -> Tuple[Optional[List[int]], Optional[str]]:
    if not value:
        return None, None
    raw = str(value).strip().lower()
    if not raw or raw == "auto":
        return None, None

    cpus: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                return None, "cpu_affinity_invalid_format"
            start = int(a)
            end = int(b)
            if end < start:
                return None, "cpu_affinity_invalid_range"
            cpus.extend(list(range(start, end + 1)))
        else:
            if not part.isdigit():
                return None, "cpu_affinity_invalid_format"
            cpus.append(int(part))

    if not cpus:
        return None, "cpu_affinity_empty"

    max_cpu = (os.cpu_count() or 1) - 1
    for cpu in cpus:
        if cpu < 0 or cpu > max_cpu:
            return None, "cpu_affinity_out_of_range"
    return sorted(set(cpus)), None


def apply_pre(cfg: Dict[str, object]) -> Tuple[Dict[str, object], List[str]]:
    """
    Apply system-wide tuning before engine start.
    Returns (state, warnings).
    """
    state: Dict[str, object] = {}
    warnings: List[str] = []

    if _truthy(cfg.get("cpu_governor_performance")):
        prev: Dict[str, str] = {}
        paths = _cpu_governor_paths()
        if not paths:
            warnings.append("cpu_governor_not_available")
        for path in paths:
            cur = _read_text(path)
            if cur:
                prev[str(path)] = cur
            if cur != "performance":
                if not _write_text(path, "performance"):
                    warnings.append(f"cpu_governor_set_failed:{path.name}")
        if prev:
            state["cpu_governor_prev"] = prev

    if _truthy(cfg.get("sysctl_tuning")):
        prev: Dict[str, str] = {}
        available_cc = _available_congestion_controls()
        for key, value in SYSCTL_TUNING_DEFAULTS.items():
            if key == "net.ipv4.tcp_congestion_control" and "bbr" not in available_cc:
                warnings.append("bbr_not_available")
                continue
            cur = _read_sysctl(key)
            if cur is None:
                warnings.append(f"sysctl_missing:{key}")
                continue
            prev[key] = cur
            if cur != value:
                if not _write_sysctl(key, value):
                    warnings.append(f"sysctl_set_failed:{key}")
        if prev:
            state["sysctl_prev"] = prev

    return state, warnings


def apply_runtime(
    state: Dict[str, object],
    cfg: Dict[str, object],
    *,
    ap_ifname: Optional[str],
    adapter_ifname: Optional[str],
    cpu_affinity_pids: Iterable[int],
) -> Tuple[Dict[str, object], List[str]]:
    warnings: List[str] = []

    if _truthy(cfg.get("wifi_power_save_disable")) and ap_ifname:
        prev: Dict[str, str] = {}
        for iface in {ap_ifname, adapter_ifname}:
            if not iface:
                continue
            cur = _get_power_save(iface)
            if cur:
                prev[iface] = cur
            if not _set_power_save(iface, False):
                warnings.append(f"wifi_power_save_disable_failed:{iface}")
        if prev:
            state["wifi_power_save_prev"] = prev

    if _truthy(cfg.get("usb_autosuspend_disable")) and adapter_ifname:
        prev: Dict[str, str] = {}
        for path in _find_usb_power_control_paths(adapter_ifname):
            cur = _read_text(path)
            if cur:
                prev[str(path)] = cur
            if cur != "on":
                if not _write_text(path, "on"):
                    warnings.append(f"usb_autosuspend_disable_failed:{path.parent.name}")
        if prev:
            state["usb_power_control_prev"] = prev

    affinity_val = cfg.get("cpu_affinity")
    cpus, err = _parse_cpu_affinity(str(affinity_val).strip() if affinity_val is not None else "")
    if err:
        warnings.append(err)
    if cpus:
        pinned: List[int] = []
        for pid in cpu_affinity_pids:
            try:
                os.sched_setaffinity(int(pid), set(cpus))
                pinned.append(int(pid))
            except Exception:
                warnings.append(f"cpu_affinity_failed:pid={pid}")
        if pinned:
            state["cpu_affinity"] = {"cpus": cpus, "pids": pinned}
        else:
            warnings.append("cpu_affinity_no_pids")

    return state, warnings


def revert(state: Optional[Dict[str, object]]) -> List[str]:
    warnings: List[str] = []
    if not isinstance(state, dict):
        return warnings

    prev_governor = state.get("cpu_governor_prev")
    if isinstance(prev_governor, dict):
        for path_str, val in prev_governor.items():
            path = Path(path_str)
            if path.exists() and not _write_text(path, str(val)):
                warnings.append(f"cpu_governor_restore_failed:{path.name}")

    prev_sysctl = state.get("sysctl_prev")
    if isinstance(prev_sysctl, dict):
        for key, val in prev_sysctl.items():
            if not _write_sysctl(str(key), str(val)):
                warnings.append(f"sysctl_restore_failed:{key}")

    prev_usb = state.get("usb_power_control_prev")
    if isinstance(prev_usb, dict):
        for path_str, val in prev_usb.items():
            path = Path(path_str)
            if path.exists() and not _write_text(path, str(val)):
                warnings.append(f"usb_autosuspend_restore_failed:{path.parent.name}")

    prev_wifi = state.get("wifi_power_save_prev")
    if isinstance(prev_wifi, dict):
        for iface, val in prev_wifi.items():
            if not _set_power_save(str(iface), val == "on"):
                warnings.append(f"wifi_power_save_restore_failed:{iface}")

    return warnings
