import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


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

SYSCTL_LOW_LATENCY: Dict[str, str] = {
    "net.core.rmem_max": "16777216",  # Smaller buffers for lower latency
    "net.core.wmem_max": "16777216",
    "net.core.rmem_default": "131072",
    "net.core.wmem_default": "131072",
    "net.ipv4.tcp_rmem": "4096 16384 16777216",  # Reduced TCP window
    "net.ipv4.tcp_wmem": "4096 16384 16777216",
    "net.ipv4.tcp_timestamps": "1",
    "net.ipv4.tcp_sack": "1",
    "net.ipv4.tcp_slow_start_after_idle": "0",  # Disable slow start after idle
}

MEMORY_TUNING_DEFAULTS: Dict[str, str] = {
    "vm.swappiness": "1",  # Minimize swapping for lower latency
    "vm.dirty_ratio": "5",  # Reduce dirty page ratio for network buffers
    "vm.dirty_background_ratio": "2",
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


def _find_irqs_for_interface(ifname: str) -> List[int]:
    """Find IRQ numbers for a network interface."""
    irqs: List[int] = []
    try:
        # Check /proc/interrupts for interface IRQs
        with open("/proc/interrupts", "r") as f:
            for line in f:
                if ifname in line:
                    parts = line.split()
                    if parts:
                        try:
                            irq_num = int(parts[0].rstrip(":"))
                            irqs.append(irq_num)
                        except (ValueError, IndexError):
                            continue
        # Also check /sys/class/net/<ifname>/device/msi_irqs
        msi_path = Path(f"/sys/class/net/{ifname}/device/msi_irqs")
        if msi_path.exists():
            for irq_file in msi_path.iterdir():
                try:
                    irqs.append(int(irq_file.name))
                except ValueError:
                    continue
    except Exception:
        pass
    return sorted(set(irqs))


def _apply_irq_affinity(interfaces: List[str], cpus: List[int]) -> Tuple[Dict[str, Any], List[str]]:
    """Set IRQ affinity for network interfaces."""
    state: Dict[str, Any] = {}
    warnings: List[str] = []
    prev_affinity: Dict[int, str] = {}
    
    cpu_mask = sum(1 << cpu for cpu in cpus)
    cpu_mask_str = f"{cpu_mask:x}"
    
    for ifname in interfaces:
        if not ifname:
            continue
        irqs = _find_irqs_for_interface(ifname)
        if not irqs:
            warnings.append(f"irq_affinity_no_irqs_found:{ifname}")
            continue
        
        for irq in irqs:
            try:
                # Read current affinity
                affinity_path = Path(f"/proc/irq/{irq}/smp_affinity")
                if affinity_path.exists():
                    prev = affinity_path.read_text().strip()
                    prev_affinity[irq] = prev
                    # Write new affinity
                    affinity_path.write_text(cpu_mask_str)
                    state.setdefault("irqs", {})[irq] = {
                        "interface": ifname,
                        "cpus": cpus,
                        "prev_mask": prev,
                    }
            except Exception as e:
                warnings.append(f"irq_affinity_failed:irq={irq}:{e}")
    
    if prev_affinity:
        state["prev_affinity"] = prev_affinity
    
    return state, warnings


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

    # Memory tuning
    if _truthy(cfg.get("memory_tuning")):
        prev: Dict[str, str] = {}
        for key, value in MEMORY_TUNING_DEFAULTS.items():
            cur = _read_sysctl(key)
            if cur is None:
                warnings.append(f"memory_tuning_missing:{key}")
                continue
            prev[key] = cur
            if cur != value:
                if not _write_sysctl(key, value):
                    warnings.append(f"memory_tuning_set_failed:{key}")
        if prev:
            state["memory_tuning_prev"] = prev

    # TCP low-latency mode
    tcp_low_latency = _truthy(cfg.get("tcp_low_latency", False))
    sysctl_tuning = _truthy(cfg.get("sysctl_tuning", False))
    
    if sysctl_tuning:
        prev: Dict[str, str] = {}
        available_cc = _available_congestion_controls()
        # Use low-latency settings if enabled, otherwise defaults
        sysctl_settings = SYSCTL_LOW_LATENCY if tcp_low_latency else SYSCTL_TUNING_DEFAULTS
        
        for key, value in sysctl_settings.items():
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
            if tcp_low_latency:
                state["tcp_low_latency"] = True

    return state, warnings


def _find_block_devices_for_interface(ifname: str) -> List[str]:
    """Find block devices associated with a network interface (for I/O scheduler)."""
    devices: List[str] = []
    try:
        # For USB WiFi adapters, find the USB device's block device
        dev_link = Path(f"/sys/class/net/{ifname}/device")
        if dev_link.exists():
            dev_path = dev_link.resolve()
            # Look for block devices in the device tree
            for block_dir in Path("/sys/block").iterdir():
                if block_dir.is_symlink():
                    block_path = block_dir.resolve()
                    if str(dev_path) in str(block_path.parent):
                        devices.append(block_dir.name)
    except Exception:
        pass
    return devices


def _read_io_scheduler_state(device: str) -> Tuple[Optional[str], List[str]]:
    scheduler_path = Path(f"/sys/block/{device}/queue/scheduler")
    if not scheduler_path.exists():
        return None, []
    raw = scheduler_path.read_text().strip()
    tokens = [s.strip() for s in raw.split() if s.strip()]
    current = None
    available: List[str] = []
    for token in tokens:
        if token.startswith("[") and token.endswith("]"):
            cur = token.strip("[]")
            current = cur
            available.append(cur)
        else:
            available.append(token)
    return current, available


def _set_io_scheduler(device: str, scheduler: str = "none") -> Tuple[bool, str, Optional[str]]:
    """Set I/O scheduler for a block device and return previous scheduler."""
    try:
        scheduler_path = Path(f"/sys/block/{device}/queue/scheduler")
        if not scheduler_path.exists():
            return False, "scheduler_path_not_found", None

        prev, available = _read_io_scheduler_state(device)
        if not available:
            return False, "no_schedulers_available", prev

        # Try preferred scheduler, fallback to mq-deadline or first available
        if scheduler in available:
            target = scheduler
        elif "mq-deadline" in available:
            target = "mq-deadline"
        else:
            target = available[0]

        scheduler_path.write_text(target)
        return True, target, prev
    except Exception as e:
        return False, str(e), None


def _write_io_scheduler(device: str, scheduler: str) -> Tuple[bool, str]:
    try:
        scheduler_path = Path(f"/sys/block/{device}/queue/scheduler")
        if not scheduler_path.exists():
            return False, "scheduler_path_not_found"
        _prev, available = _read_io_scheduler_state(device)
        if scheduler not in available:
            return False, "scheduler_not_available"
        scheduler_path.write_text(scheduler)
        return True, scheduler
    except Exception as e:
        return False, str(e)


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

    # IRQ affinity for network interfaces
    irq_affinity_val = cfg.get("irq_affinity")
    if irq_affinity_val and isinstance(irq_affinity_val, str) and irq_affinity_val.strip():
        irq_cpus, irq_err = _parse_cpu_affinity(irq_affinity_val.strip())
        if irq_err:
            warnings.append(f"irq_affinity_parse_failed:{irq_err}")
        elif irq_cpus and (ap_ifname or adapter_ifname):
            irq_state, irq_warnings = _apply_irq_affinity(
                interfaces=[iface for iface in [ap_ifname, adapter_ifname] if iface],
                cpus=irq_cpus,
            )
            warnings.extend(irq_warnings)
            if irq_state:
                state["irq_affinity"] = irq_state

    # I/O scheduler optimization
    if _truthy(cfg.get("io_scheduler_optimize")):
        io_state: Dict[str, Dict[str, str]] = {}
        for ifname in [ap_ifname, adapter_ifname]:
            if not ifname:
                continue
            devices = _find_block_devices_for_interface(ifname)
            for device in devices:
                ok, result, prev = _set_io_scheduler(device, "none")
                if ok:
                    io_state.setdefault("current", {})[device] = result
                    if prev:
                        io_state.setdefault("prev", {})[device] = prev
                else:
                    warnings.append(f"io_scheduler_failed:{device}:{result}")
        if io_state:
            state["io_scheduler"] = io_state

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

    prev_memory = state.get("memory_tuning_prev")
    if isinstance(prev_memory, dict):
        for key, val in prev_memory.items():
            if not _write_sysctl(str(key), str(val)):
                warnings.append(f"memory_tuning_restore_failed:{key}")

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

    # Restore IRQ affinity
    irq_state = state.get("irq_affinity")
    if isinstance(irq_state, dict):
        prev_affinity = irq_state.get("prev_affinity")
        if isinstance(prev_affinity, dict):
            for irq_str, mask in prev_affinity.items():
                try:
                    irq = int(irq_str)
                    affinity_path = Path(f"/proc/irq/{irq}/smp_affinity")
                    if affinity_path.exists():
                        affinity_path.write_text(str(mask))
                except Exception as e:
                    warnings.append(f"irq_affinity_restore_failed:irq={irq_str}:{e}")

    # I/O scheduler restoration (best-effort)
    io_state = state.get("io_scheduler")
    if isinstance(io_state, dict):
        prev = io_state.get("prev") if isinstance(io_state.get("prev"), dict) else {}
        if isinstance(prev, dict):
            for device, scheduler in prev.items():
                if not scheduler:
                    continue
                ok, result = _write_io_scheduler(device, str(scheduler))
                if not ok:
                    warnings.append(f"io_scheduler_restore_failed:{device}:{result}")

    return warnings
