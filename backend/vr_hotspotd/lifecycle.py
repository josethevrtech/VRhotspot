import logging
import os
import re
import secrets
import shutil
import signal
import stat
import string
import subprocess
import threading
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Dict, Any, List, Tuple

from vr_hotspotd.state import load_state, update_state
from vr_hotspotd.adapters.inventory import get_adapters
from vr_hotspotd.adapters.profiles import apply_adapter_profile
from vr_hotspotd.config import load_config, ensure_config_file, write_config_file
from vr_hotspotd.engine.lnxrouter_cmd import build_cmd
from vr_hotspotd.engine import lnxrouter_conf
from vr_hotspotd.engine.hostapd6_cmd import build_cmd_6ghz
from vr_hotspotd.engine.hostapd_nat_cmd import build_cmd_nat
from vr_hotspotd.engine.hostapd_bridge_cmd import build_cmd_bridge
from vr_hotspotd.engine.supervisor import start_engine, stop_engine, is_running, get_tails
from vr_hotspotd.engine.channel_scan import select_best_channel
from vr_hotspotd.engine.tx_power import auto_adjust_tx_power, set_tx_power, get_tx_power
from vr_hotspotd import system_tuning, preflight, network_tuning, os_release, wifi_probe
from vr_hotspotd.policy import (
    BASIC_MODE_REQUIRED_BAND,
    ERROR_BASIC_MODE_REQUIRES_5GHZ,
    ERROR_BASIC_MODE_REQUIRES_80MHZ_ADAPTER,
    ERROR_NM_INTERFACE_MANAGED,
)

log = logging.getLogger("vr_hotspotd.lifecycle")

def _precreated_ap_ifname(parent_ifname: str, prefix: str = "vrhs_ap_") -> str:
    """
    Creates a valid network interface name for a pre-created AP interface.
    Ensures the name is no longer than 15 characters, which is a common
    kernel limit.
    """
    if not parent_ifname:
        raise ValueError("parent_ifname must not be empty")

    ifname = f"{prefix}{parent_ifname}"
    if len(ifname) <= 15:
        return ifname

    # Name is too long, so we need to truncate and add a hash to keep it unique.
    # The suffix is a 4-char hex representation of the SHA1 hash of the parent.
    suffix = "_" + hashlib.sha1(parent_ifname.encode()).hexdigest()[:4]

    # Calculate the maximum length of the parent_ifname part we can keep.
    # 15 (max) - len(prefix) - len(suffix)
    max_parent_len = 15 - len(prefix) - len(suffix)

    # Truncate the parent ifname and assemble the new name.
    truncated_parent = parent_ifname[:max_parent_len]
    return f"{prefix}{truncated_parent}{suffix}"

_OP_LOCK = threading.Lock()
_WATCHDOG_THREAD: Optional[threading.Thread] = None
_WATCHDOG_STOP = threading.Event()
_WATCHDOG_BACKOFF_MAX_S = 30.0
_AUTOGEN_PASSPHRASE_CACHE: Optional[str] = None
_AUTOGEN_PASSPHRASE_TS: float = 0.0


def _virt_ap_ifname(base: str) -> str:
    cand = f"x0{base}"
    return cand[:15]


def _lnxrouter_expected_ifname(ap_ifname: str, *, no_virt: bool) -> Optional[str]:
    """
    Best-effort expected AP interface naming for linux-router mode.

    linux-router auto-picks virtual interface names unless --virt-name is set.
    We only predict deterministic names where we control it:
    - no_virt=True => AP uses the adapter ifname
    - long ifnames (>13) => build_cmd injects --virt-name x0<ifname>[:15]
    """
    if not ap_ifname:
        return None
    if no_virt:
        return ap_ifname
    if len(ap_ifname) > 13:
        return _virt_ap_ifname(ap_ifname)
    return None


class LifecycleResult:
    def __init__(self, code, state):
        self.code = code
        self.state = state


def _generate_bootstrap_passphrase(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    n = max(8, min(63, int(length)))
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _get_or_create_bootstrap_passphrase(*, cache_ttl_s: float = 300.0) -> str:
    global _AUTOGEN_PASSPHRASE_CACHE, _AUTOGEN_PASSPHRASE_TS
    now = time.time()
    cached = _AUTOGEN_PASSPHRASE_CACHE
    if (
        isinstance(cached, str)
        and len(cached) >= 8
        and (now - float(_AUTOGEN_PASSPHRASE_TS)) <= float(cache_ttl_s)
    ):
        return cached
    generated = _generate_bootstrap_passphrase()
    _AUTOGEN_PASSPHRASE_CACHE = generated
    _AUTOGEN_PASSPHRASE_TS = now
    return generated


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
    "channel_5g",    # int (NEW)
    "wifi6",         # "auto" | true | false
    "channel_width",  # "auto" | "20" | "40" | "80" | "160"
    "beacon_interval",  # int
    "dtim_period",  # int
    "short_guard_interval",  # bool
    "tx_power",  # int or None
    "channel_auto_select",  # bool
    "allow_fallback_40mhz",  # bool
    "allow_dfs_channels",  # bool
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
_IW_WIDTH_RE = re.compile(r"width:\s*(\d+)\s*mhz", re.IGNORECASE)
_HOSTAPD_CTRL_DIR_RE = re.compile(r"DIR=(.+)")
_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
_CMD_TIMEOUT_S = 2.5


def ensure_hostapd_ctrl_interface_dir(conf_path: str) -> None:
    """
    Parse ctrl_interface from hostapd.conf and ensure the directory exists with proper permissions.
    Handles both plain path and DIR=/path formats.
    """
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        log.warning("hostapd_ctrl_interface_parse_failed", extra={"conf_path": conf_path, "error": str(e)})
        return

    ctrl_dir: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("ctrl_interface="):
            value = stripped.split("=", 1)[1].strip()
            # Check for DIR=/path format
            m = _HOSTAPD_CTRL_DIR_RE.match(value)
            if m:
                ctrl_dir = m.group(1)
            else:
                # Plain path or first token
                ctrl_dir = value.split()[0] if value else None
            break

    if not ctrl_dir:
        log.debug("hostapd_ctrl_interface_not_found", extra={"conf_path": conf_path})
        return

    try:
        Path(ctrl_dir).mkdir(parents=True, exist_ok=True)
        os.chmod(ctrl_dir, 0o755)
        log.info("hostapd_ctrl_interface_dir_ensured", extra={"conf_path": conf_path, "ctrl_dir": ctrl_dir})
    except Exception as e:
        log.warning("hostapd_ctrl_interface_dir_failed", extra={"conf_path": conf_path, "ctrl_dir": ctrl_dir, "error": str(e)})


def validate_hostapd_country(conf_path: str) -> Optional[str]:
    """
    Validate that if ieee80211d=1, country_code is properly set.
    Returns error code string if invalid, None if valid.
    """
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return None

    ieee80211d: Optional[int] = None
    country_code: Optional[str] = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        
        if stripped.startswith("ieee80211d="):
            val = stripped.split("=", 1)[1].strip()
            try:
                ieee80211d = int(val)
            except Exception:
                pass
        
        if stripped.startswith("country_code="):
            val = stripped.split("=", 1)[1].strip()
            country_code = val if val else None

    if ieee80211d == 1:
        if not country_code:
            return "hostapd_invalid_country_code_for_80211d"
        if country_code == "00":
            return "hostapd_invalid_country_code_for_80211d"
        if not _COUNTRY_CODE_RE.match(country_code):
            return "hostapd_invalid_country_code_for_80211d"
    
    return None


def enforce_hostapd_country(conf_path: str, resolved_country: str) -> bool:
    """
    Enforce country_code in hostapd.conf if resolved_country is valid.
    Returns True if file was modified, False otherwise.
    """
    if not _COUNTRY_CODE_RE.match(resolved_country):
        return False
    if resolved_country == "00":
        return False

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        log.warning("enforce_hostapd_country_read_failed", extra={"conf_path": conf_path, "error": str(e)})
        return False

    modified = False
    country_line_idx: Optional[int] = None
    
    # Find existing country_code line
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("country_code="):
            country_line_idx = i
            current_val = stripped.split("=", 1)[1].strip()
            if current_val != resolved_country:
                lines[i] = f"country_code={resolved_country}\n"
                modified = True
            break

    # If no country_code line exists, append it
    if country_line_idx is None:
        lines.append(f"country_code={resolved_country}\n")
        modified = True

    if modified:
        try:
            with open(conf_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            log.info("enforce_hostapd_country_updated", extra={"conf_path": conf_path, "country": resolved_country})
        except Exception as e:
            log.error("enforce_hostapd_country_write_failed", extra={"conf_path": conf_path, "error": str(e)})
            return False

    return modified


@dataclass(frozen=True)
class APReadyInfo:
    ifname: str
    phy: Optional[str]
    ssid: Optional[str]
    freq_mhz: Optional[int]
    channel: Optional[int]
    channel_width_mhz: Optional[int]


def _iw_bin() -> str:
    iw = shutil.which("iw")
    if iw:
        return iw
    if os.path.exists("/usr/sbin/iw"):
        return "/usr/sbin/iw"
    raise RuntimeError("iw_not_found")


def _run(cmd: List[str], timeout_s: float = _CMD_TIMEOUT_S) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        return (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        cmd_s = " ".join(cmd)
        return (out + ("\n" if out else "")) + f"cmd_timed_out:{cmd_s}"
    except Exception as exc:
        return f"cmd_failed:{type(exc).__name__}:{exc}"


def _iw_dev_dump() -> str:
    return _run([_iw_bin(), "dev"])


def _iw_dev_info(ifname: str) -> str:
    if not ifname:
        return ""
    return _run([_iw_bin(), "dev", ifname, "info"])


def _parse_iw_dev_info(iw_text: str) -> Dict[str, Optional[int]]:
    info: Dict[str, Optional[int]] = {
        "channel": None,
        "freq_mhz": None,
        "channel_width_mhz": None,
    }
    for raw in iw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m_channel = _IW_CHANNEL_RE.match(line)
        if m_channel:
            try:
                info["channel"] = int(m_channel.group(1))
            except Exception:
                info["channel"] = None
            if m_channel.group(2):
                try:
                    info["freq_mhz"] = int(float(m_channel.group(2)))
                except Exception:
                    pass
            m_width = _IW_WIDTH_RE.search(line)
            if m_width:
                try:
                    info["channel_width_mhz"] = int(m_width.group(1))
                except Exception:
                    pass
            continue
        m_freq = _IW_FREQ_RE.match(line)
        if m_freq and info.get("freq_mhz") is None:
            try:
                info["freq_mhz"] = int(float(m_freq.group(1)))
            except Exception:
                pass
            continue
        m_width = _IW_WIDTH_RE.search(line)
        if m_width:
            try:
                info["channel_width_mhz"] = int(m_width.group(1))
            except Exception:
                pass
    return info


def _iface_is_up(ifname: str) -> bool:
    if not ifname:
        return False
    ip = shutil.which("ip") or "/usr/sbin/ip"
    try:
        p = subprocess.run([ip, "link", "show", "dev", ifname], capture_output=True, text=True)
    except Exception:
        return False
    if p.returncode != 0:
        return False
    text = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if "state UP" in text:
        return True
    # ip link flags are typically like "<BROADCAST,MULTICAST,UP,LOWER_UP>"
    # so checking only for "<UP" is too strict and misses valid UP states.
    m = re.search(r"<([^>]+)>", text)
    if m:
        flags = [f.strip().upper() for f in m.group(1).split(",") if f.strip()]
        if "UP" in flags:
            return True

    # Fallback: kernel netdev flags bitmask (IFF_UP = 0x1).
    try:
        flags_raw = Path(f"/sys/class/net/{ifname}/flags").read_text(encoding="utf-8").strip()
        flags = int(flags_raw, 0)
        if flags & 0x1:
            return True
    except Exception:
        pass
    return False


def _iface_exists(ifname: str) -> bool:
    if not ifname:
        return False
    return os.path.exists(f"/sys/class/net/{ifname}")


def _ensure_iface_up(ifname: str) -> bool:
    if not ifname:
        return False
    if _iface_is_up(ifname):
        return True
    ip = shutil.which("ip") or "/usr/sbin/ip"
    try:
        subprocess.run([ip, "link", "set", "dev", ifname, "up"], capture_output=True, text=True, check=False)
    except Exception:
        return False
    time.sleep(0.2)
    return _iface_is_up(ifname)


def _ensure_iface_up_with_grace(
    ifname: str,
    *,
    grace_s: float = 0.0,
    poll_s: float = 0.25,
) -> bool:
    """
    Ensure interface is UP, optionally allowing a short settle window.

    Some drivers transiently report AP readiness before link state is reflected.
    """
    if not ifname:
        return False
    if _iface_is_up(ifname):
        return True
    if _ensure_iface_up(ifname):
        return True

    try:
        grace = max(0.0, float(grace_s))
    except Exception:
        grace = 0.0
    if grace <= 0.0:
        return False

    try:
        interval = max(0.1, float(poll_s))
    except Exception:
        interval = 0.25

    deadline = time.time() + grace
    while time.time() < deadline:
        if _iface_is_up(ifname):
            return True
        if not is_running():
            break
        _ensure_iface_up(ifname)
        time.sleep(interval)
    return _iface_is_up(ifname)


def _nmcli_path() -> Optional[str]:
    return shutil.which("nmcli")


def _nm_is_running() -> bool:
    nmcli = _nmcli_path()
    if not nmcli:
        return False
    try:
        p = subprocess.run([nmcli, "-t", "-f", "RUNNING", "g"], capture_output=True, text=True)
    except Exception:
        return False
    return p.returncode == 0 and (p.stdout or "").strip() == "running"


def _nm_device_state(ifname: str) -> Optional[str]:
    nmcli = _nmcli_path()
    if not nmcli:
        return None
    try:
        p = subprocess.run([nmcli, "-t", "-f", "DEVICE,STATE", "dev", "status"], capture_output=True, text=True)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    for raw in (p.stdout or "").splitlines():
        parts = raw.split(":", 1)
        if len(parts) != 2:
            continue
        dev, state = parts[0].strip(), parts[1].strip()
        if dev == ifname:
            return state
    return None


def _nm_state_non_interfering(state: Optional[str]) -> bool:
    if state is None:
        return True
    state_norm = state.strip().lower()
    if state_norm in ("unmanaged", "unavailable", "disconnected"):
        return True
    if state_norm.startswith("disconnected "):
        return True
    return False


def _nm_wait_non_interfering(ifname: str, timeout_s: float = 1.5) -> bool:
    if not ifname or not _nm_is_running():
        return True
    deadline = time.time() + max(0.1, float(timeout_s))
    last_state: Optional[str] = None
    while time.time() < deadline:
        state = _nm_device_state(ifname)
        if _nm_state_non_interfering(state):
            return True
        last_state = state
        time.sleep(0.2)
    return _nm_state_non_interfering(last_state)


def _nm_set_unmanaged(ifname: str) -> Tuple[bool, Optional[str]]:
    if not ifname or not str(ifname).strip():
        return False, "invalid_interface"
    if os.geteuid() != 0:
        return False, "not_root"
    nmcli = _nmcli_path()
    if not nmcli:
        return False, "nmcli_not_found"
    try:
        p = subprocess.run(
            [nmcli, "dev", "set", ifname, "managed", "no"],
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return False, f"nmcli_error:{type(exc).__name__}"
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        low = err.lower()
        if "device" in low and "not found" in low:
            # Interface can transiently disappear during USB driver re-enumeration.
            return True, None
        return False, err or "nmcli_failed"
    return True, None


def _nm_disconnect(ifname: str) -> Tuple[bool, Optional[str]]:
    if not ifname or not str(ifname).strip():
        return False, "invalid_interface"
    nmcli = _nmcli_path()
    if not nmcli:
        return False, "nmcli_not_found"
    try:
        p = subprocess.run(
            [nmcli, "dev", "disconnect", ifname],
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return False, f"nmcli_error:{type(exc).__name__}"
    if p.returncode == 0:
        return True, None

    err = (p.stderr or p.stdout or "").strip()
    low = err.lower()
    # Best-effort operation: treat "already disconnected/unmanaged" conditions as success.
    benign = (
        "not active",
        "not connected",
        "is disconnected",
        "is unmanaged",
        "not managed",
        "is unknown",
        "no suitable device found",
        "not all devices found",
    )
    if any(tok in low for tok in benign):
        return True, None
    if "device" in low and "not found" in low:
        return True, None
    return False, err or "nmcli_disconnect_failed"


def _rfkill_unblock_wifi() -> bool:
    rfkill = shutil.which("rfkill")
    if not rfkill:
        return False
    try:
        p = subprocess.run(
            [rfkill, "unblock", "wifi"],
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    return p.returncode == 0


def _cleanup_p2p_dev_ifaces(parent_ifname: str) -> List[str]:
    """
    Remove p2p-dev interfaces that can keep a radio busy for AP mode transitions.
    Best-effort only.
    """
    removed: List[str] = []
    if not parent_ifname:
        return removed
    try:
        dump = _iw_dev_dump()
    except Exception:
        return removed

    candidates: List[str] = []
    for raw in dump.splitlines():
        line = raw.strip()
        if not line.startswith("Interface "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ifname = parts[1].strip()
        if not ifname:
            continue
        if ifname == f"p2p-dev-{parent_ifname}" or (ifname.startswith("p2p-dev-") and ifname.endswith(parent_ifname)):
            candidates.append(ifname)

    for ifname in sorted(set(candidates)):
        try:
            subprocess.run(
                [_iw_bin(), "dev", ifname, "del"],
                check=False,
                capture_output=True,
                text=True,
                timeout=_CMD_TIMEOUT_S,
            )
            removed.append(ifname)
        except Exception:
            pass
    return removed


def _iface_kernel_driver(ifname: str) -> Optional[str]:
    if not ifname:
        return None
    try:
        driver_link = Path(f"/sys/class/net/{ifname}/device/driver")
        if not driver_link.exists():
            return None
        resolved = driver_link.resolve()
        name = resolved.name.strip()
        return name or None
    except Exception:
        return None


def _iface_bus_type(ifname: str) -> Optional[str]:
    if not ifname:
        return None
    try:
        sub_link = Path(f"/sys/class/net/{ifname}/device/subsystem")
        if not sub_link.exists():
            return None
        name = sub_link.resolve().name.strip().lower()
        return name or None
    except Exception:
        return None


def _driver_reload_recovery_enabled() -> bool:
    raw = str(os.environ.get("VR_HOTSPOTD_ENABLE_DRIVER_RELOAD_RECOVERY", "")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _usb_rebind_iface(ifname: str) -> Tuple[bool, Optional[str]]:
    """
    Rebind a USB wlan interface to clear hard busy states without unloading modules.
    Returns (ok, detail_or_reason).
    """
    if os.geteuid() != 0:
        return False, "not_root"
    bus = _iface_bus_type(ifname)
    if bus != "usb":
        return False, f"non_usb:{bus or 'unknown'}"
    dev_path = Path(f"/sys/class/net/{ifname}/device")
    if not dev_path.exists():
        return False, "device_path_missing"
    try:
        dev_id = dev_path.resolve().name
    except Exception:
        return False, "device_resolve_failed"
    if not dev_id:
        return False, "device_id_missing"
    driver_link = dev_path / "driver"
    if not driver_link.exists():
        return False, "driver_link_missing"
    try:
        driver = driver_link.resolve().name
    except Exception:
        return False, "driver_resolve_failed"
    if not driver:
        return False, "driver_unknown"
    unbind = Path(f"/sys/bus/usb/drivers/{driver}/unbind")
    bind = Path(f"/sys/bus/usb/drivers/{driver}/bind")
    if not unbind.exists() or not bind.exists():
        return False, f"usb_driver_bind_paths_missing:{driver}"
    try:
        unbind.write_text(f"{dev_id}\n", encoding="utf-8")
        time.sleep(0.5)
        bind.write_text(f"{dev_id}\n", encoding="utf-8")
    except Exception as exc:
        return False, f"usb_rebind_failed:{type(exc).__name__}"
    return True, f"{driver}:{dev_id}"


def _reload_wifi_driver_for_iface(ifname: str) -> Tuple[bool, Optional[str]]:
    """
    Best-effort module reload for hard-stuck adapters.
    Intended as a last-resort recovery on platforms where interface up can stay busy.
    """
    if os.geteuid() != 0:
        return False, "not_root"
    driver = _iface_kernel_driver(ifname)
    if not driver:
        return False, "driver_unknown"
    modprobe = shutil.which("modprobe")
    if not modprobe:
        return False, "modprobe_not_found"
    try:
        down = subprocess.run(
            [modprobe, "-r", driver],
            capture_output=True,
            text=True,
            check=False,
            timeout=6.0,
        )
    except Exception as exc:
        return False, f"modprobe_remove_error:{type(exc).__name__}"
    if down.returncode != 0:
        err = (down.stderr or down.stdout or "").strip()
        return False, f"modprobe_remove_failed:{err or down.returncode}"
    time.sleep(0.35)
    try:
        up = subprocess.run(
            [modprobe, driver],
            capture_output=True,
            text=True,
            check=False,
            timeout=6.0,
        )
    except Exception as exc:
        return False, f"modprobe_insert_error:{type(exc).__name__}"
    if up.returncode != 0:
        err = (up.stderr or up.stdout or "").strip()
        return False, f"modprobe_insert_failed:{err or up.returncode}"
    return True, driver


def _prepare_ap_interface(
    ifname: Optional[str],
    *,
    force_nm_disconnect: bool = False,
) -> List[str]:
    """
    Best-effort AP interface prep before engine launch.

    On some platforms (notably Pop!_OS), NetworkManager/wpa_supplicant can still
    hold the adapter even after a managed=no request, causing:
      RTNETLINK answers: Device or resource busy
      Failed bringing <iface> up
    """
    warnings: List[str] = []
    if not ifname:
        return warnings

    iface_present = _iface_exists(ifname)
    if _nm_is_running():
        if force_nm_disconnect:
            # Pop!_OS can still hold the adapter even in "unavailable" state.
            # Force unmanaged first, then disconnect as a best-effort release.
            if iface_present:
                set_ok, set_err = _nm_set_unmanaged(ifname)
                if not set_ok and set_err and set_err not in ("not_root", "nmcli_not_found"):
                    warnings.append(f"nm_set_unmanaged_failed:{set_err}")
                ok, err = _nm_disconnect(ifname)
                if not ok and err:
                    warnings.append(f"nm_disconnect_failed:{err}")
                removed_p2p = _cleanup_p2p_dev_ifaces(ifname)
                if removed_p2p:
                    warnings.append("removed_p2p_dev_iface:" + ",".join(removed_p2p))
                if not _nm_wait_non_interfering(ifname):
                    warnings.append("nm_still_managed_prestart")

    _rfkill_unblock_wifi()

    if _ensure_iface_up(ifname):
        return warnings

    # One extra hard reset attempt can clear transient busy states.
    ip = shutil.which("ip") or "/usr/sbin/ip"
    try:
        subprocess.run(
            [ip, "link", "set", "dev", ifname, "down"],
            capture_output=True,
            text=True,
            check=False,
        )
        time.sleep(0.2)
        subprocess.run(
            [ip, "link", "set", "dev", ifname, "up"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        pass

    if not _iface_is_up(ifname):
        iface_present = _iface_exists(ifname)
        if not iface_present:
            warnings.append("ap_iface_missing_prestart")
        warnings.append("ap_iface_not_up_prestart")
        # First Pop!/USB fallback: USB driver rebind (less disruptive than module reload).
        if force_nm_disconnect and iface_present and _iface_bus_type(ifname) == "usb":
            ok, detail = _usb_rebind_iface(ifname)
            if ok:
                warnings.append(f"ap_iface_driver_reload:usb_rebind:{detail}")
                _rfkill_unblock_wifi()
                _cleanup_p2p_dev_ifaces(ifname)
                if _ensure_iface_up(ifname):
                    return warnings
                warnings.append("ap_iface_not_up_post_driver_reload")
            elif detail:
                warnings.append(f"ap_iface_driver_reload_failed:usb_rebind:{detail}")
        # Last-resort Pop!_OS recovery path: reload driver module once, then retry.
        if force_nm_disconnect and iface_present and _driver_reload_recovery_enabled():
            bus = _iface_bus_type(ifname)
            if bus != "usb":
                if bus:
                    warnings.append(f"ap_iface_driver_reload_skipped_non_usb:{bus}")
            else:
                ok, detail = _reload_wifi_driver_for_iface(ifname)
                if ok:
                    warnings.append(f"ap_iface_driver_reload:{detail}")
                    _rfkill_unblock_wifi()
                    _cleanup_p2p_dev_ifaces(ifname)
                    if _ensure_iface_up(ifname):
                        return warnings
                    warnings.append("ap_iface_not_up_post_driver_reload")
                elif detail:
                    warnings.append(f"ap_iface_driver_reload_failed:{detail}")
    return warnings


def _maybe_reselect_ap_after_prestart_failure(
    *,
    ap_ifname: str,
    preferred_ifname: Optional[str],
    band_pref: str,
    inv: Dict[str, Any],
    adapter: Optional[Dict[str, Any]],
    platform_is_pop: bool,
    prep_warnings: List[str],
) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]], List[str]]:
    """
    On Pop!_OS, driver reload can cause USB wlan ifnames to change/disappear.
    Re-discover adapters and select the current AP-capable iface.
    """
    warnings: List[str] = []
    if not platform_is_pop or not ap_ifname:
        return ap_ifname, inv, adapter, warnings

    missing_iface = not os.path.exists(f"/sys/class/net/{ap_ifname}")
    had_reload_failure = any(
        w == "ap_iface_not_up_post_driver_reload" or str(w).startswith("ap_iface_driver_reload:")
        for w in (prep_warnings or [])
    )
    if not missing_iface and not had_reload_failure:
        return ap_ifname, inv, adapter, warnings

    old_bus = str((adapter or {}).get("bus") or "").strip().lower()

    def _pick_candidate(
        inv_cur: Dict[str, Any],
        *,
        require_bus: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        ordered: List[str] = []
        if preferred_ifname and isinstance(preferred_ifname, str) and preferred_ifname.strip():
            ordered.append(preferred_ifname.strip())
        if ap_ifname:
            ordered.append(ap_ifname)
        rec = inv_cur.get("recommended")
        if isinstance(rec, str) and rec.strip():
            ordered.append(rec.strip())
        for item in inv_cur.get("adapters", []):
            cand = item.get("ifname")
            if isinstance(cand, str) and cand.strip():
                ordered.append(cand.strip())

        seen: Set[str] = set()
        for raw in ordered:
            cand = _normalize_ap_adapter(raw, inv_cur)
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if not os.path.exists(f"/sys/class/net/{cand}"):
                continue
            item = _get_adapter(inv_cur, cand)
            if not item or not item.get("supports_ap"):
                continue
            if require_bus and str(item.get("bus") or "").strip().lower() != require_bus:
                continue
            return cand, item
        return None, None

    # USB adapters can disappear/re-enumerate for a few seconds after reload.
    # Prefer staying on USB instead of falling back to internal PCI radios.
    wait_usb = old_bus == "usb"
    scans = 12 if wait_usb else 1
    for _ in range(scans):
        try:
            inv_refreshed = get_adapters()
        except Exception:
            inv_refreshed = inv
        inv_err = inv_refreshed.get("error") if isinstance(inv_refreshed, dict) else None
        if inv_err and not (inv_refreshed or {}).get("adapters"):
            if wait_usb:
                time.sleep(0.5)
                continue
            warnings.append(f"ap_adapter_reselect_failed:{inv_err}")
            return ap_ifname, inv, adapter, warnings

        require_bus = old_bus if wait_usb else None
        candidate, new_adapter = _pick_candidate(inv_refreshed, require_bus=require_bus)
        if candidate and new_adapter:
            if candidate != ap_ifname:
                warnings.append(f"ap_adapter_reselected_after_reload:{ap_ifname}->{candidate}")
            return candidate, inv_refreshed, new_adapter, warnings

        if wait_usb:
            time.sleep(0.5)
            continue

    if wait_usb:
        warnings.append("ap_adapter_reselect_usb_missing_after_reload")
        return ap_ifname, inv, adapter, warnings

    try:
        inv_refreshed = get_adapters()
        candidate = _normalize_ap_adapter(ap_ifname, inv_refreshed)
        if not candidate:
            return ap_ifname, inv, adapter, warnings
        new_adapter = _get_adapter(inv_refreshed, candidate)
        if new_adapter and new_adapter.get("supports_ap"):
            return candidate, inv_refreshed, new_adapter, warnings
    except Exception as exc:
        warnings.append(f"ap_adapter_reselect_failed:{exc}")
    return ap_ifname, inv, adapter, warnings


def _nm_interference_reason(ifname: str) -> Optional[str]:
    if not ifname or not _nm_is_running():
        return None
    state = _nm_device_state(ifname)
    if not state:
        return None
    if _nm_state_non_interfering(state):
        return None
    state_norm = state.strip().lower()
    return f"nm_state={state_norm}"


def _nm_gate_check(ifname: str) -> Optional[str]:
    """
    Pre-start gate to check if NetworkManager is managing the interface.
    Returns error code if NM owns the interface (before we even try to start).
    """
    if not ifname or not _nm_is_running():
        return None
    state = _nm_device_state(ifname)
    if not state:
        return None
    if _nm_state_non_interfering(state):
        return None
    state_norm = state.strip().lower()
    # Interface is managed by NM - fail fast
    log.warning(
        "nm_interface_managed_prestart_gate",
        extra={"ifname": ifname, "nm_state": state_norm},
    )
    return "nm_interface_managed"


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
                    channel_width_mhz=cur.get("channel_width_mhz"),
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
                "channel_width_mhz": None,
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
            m_width = _IW_WIDTH_RE.search(line)
            if m_width:
                try:
                    cur["channel_width_mhz"] = int(m_width.group(1))
                except Exception:
                    pass
            continue

        m_freq = _IW_FREQ_RE.match(line)
        if m_freq and cur.get("freq_mhz") is None:
            try:
                cur["freq_mhz"] = int(float(m_freq.group(1)))
            except Exception:
                pass
            continue

        m_width = _IW_WIDTH_RE.search(line)
        if m_width:
            try:
                cur["channel_width_mhz"] = int(m_width.group(1))
            except Exception:
                pass

    _finalize_current()
    return aps


def _parse_iw_dev_ap_ifaces(iw_text: str) -> Set[str]:
    return {ap.ifname for ap in _parse_iw_dev_ap_info(iw_text) if ap.ifname}

def _parse_supported_interface_modes(text: str) -> Optional[bool]:
    if not text or "Supported interface modes" not in text:
        return None
    
    in_modes = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Supported interface modes"):
            in_modes = True
            continue
        if in_modes:
            if line.startswith("*"):
                mode = line.lstrip("*").strip()
                if mode in ("AP", "AP/VLAN"):
                    return True
            elif line and not line.startswith("*"):
                # End of section
                break
    return False


def _parse_ap_managed_concurrency(text: str) -> Optional[bool]:
    if not text or "valid interface combinations" not in text:
        return None
    
    # Simple multi-line check: flatten the text or check presence in the relevant section.
    # We look for a combination that supports AP and Managed handling.
    # Example snippet:
    #  * #{ managed } <= 1, #{ AP, P2P-client, P2P-GO } <= 1,
    #    total <= 2, #channels <= 1
    
    found_managed = False
    found_ap = False
    found_total = False
    
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if "valid interface combinations" in line:
            in_section = True
            continue
        if not in_section:
            continue
            
        # Stop at next section if any (usually starting with non-indented text or Specific Keywords)
        # But 'iw phy' output indentation varies. We assume valid combinations block continues until
        # another header or end of file.
        
        if line.startswith("*"):
            # New combination
            found_managed = False
            found_ap = False
            found_total = False
        
        if "#{ managed }" in line:
            found_managed = True
        if "AP" in line:
            found_ap = True
        if "total <=" in line:
            found_total = True
            
        if found_managed and found_ap and found_total:
            return True
            
    return False


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

def _select_ap_by_ifname(iw_text: str, ifname: str) -> Optional[APReadyInfo]:
    aps = _parse_iw_dev_ap_info(iw_text)
    for ap in aps:
        if ap.ifname == ifname:
            return ap
    return None


def _validate_channel_for_band(band: str, channel: int, country: Optional[str] = None) -> Tuple[int, Optional[str]]:
    """
    Validates a channel for a given band.
    Returns (channel, warning_id) or (channel, None) if valid.
    """
    b = band.strip().lower()
    if b in ("2.4ghz", "2.4"):
        if 1 <= channel <= 14:
            return channel, None
        return 6, "channel_invalid_for_band_overridden"
    elif b in ("5ghz", "5"):
        # Very rough check, just ensuring it's in 5GHz range
        if 36 <= channel <= 177:
            return channel, None
        return 36, "channel_invalid_for_band_overridden"
    elif b in ("6ghz", "6"):
        if 1 <= channel <= 233:  # PSC or non-PSC
            return channel, None
        return 37, "channel_invalid_for_band_overridden"
    return channel, "unknown_band"


def _iface_phy(ifname: str) -> Optional[str]:
    try:
        p = subprocess.run(
            [_iw_bin(), "dev", ifname, "info"],
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_S,
        )
    except Exception:
        return None
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
    expected_ap_ifname: Optional[str] = None,
    capture: Optional[Any] = None,
) -> Optional[APReadyInfo]:
    deadline = time.time() + timeout_s
    reported_ap_ifname: Optional[str] = None
    extended = False
    grace_s = max(3.0, min(8.0, float(timeout_s)))

    while time.time() < deadline:
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        stdout_ready = False
        try:
            stdout_lines, stderr_lines = get_tails()
        except Exception:
            if expected_ap_ifname:
                return APReadyInfo(
                    ifname=expected_ap_ifname,
                    phy=target_phy,
                    ssid=ssid,
                    freq_mhz=None,
                    channel=None,
                    channel_width_mhz=None,
                )
        else:
            if isinstance(stdout_lines, str):
                stdout_lines = stdout_lines.splitlines()
            if isinstance(stderr_lines, str):
                stderr_lines = stderr_lines.splitlines()
            combined_lines = list(stdout_lines) + list(stderr_lines)
            if _lines_have_iface_busy_signal(combined_lines) and not is_running():
                # Busy logs are often transient while the engine is still in its own
                # iface-recovery loop. Only fail early once the engine has exited.
                return None
            stdout_ready = _stdout_has_ap_ready(combined_lines)
            if stdout_ready and _stdout_has_ap_not_ready(combined_lines):
                # Ignore AP-ready hints if hostapd already reported AP teardown
                # in the same output window.
                stdout_ready = False
            stdout_ifname = _stdout_extract_ap_ifname(combined_lines)
            conf_ifname = None if stdout_ifname else _infer_ap_ifname_from_conf(adapter_ifname)
            if stdout_ifname or conf_ifname:
                discovered = stdout_ifname or conf_ifname
                expected_ap_ifname = discovered
                if discovered != reported_ap_ifname:
                    update_state(ap_interface=discovered)
                    reported_ap_ifname = discovered
                if not extended:
                    # We saw AP readiness signals but it may take a bit longer for iw/ctrl to catch up.
                    deadline = max(deadline, time.time() + grace_s)
                    extended = True
                    log.info(
                        "ap_ready_grace_extended",
                        extra={"grace_s": grace_s, "reason": "stdout_ready_signal"},
                    )
            elif stdout_ready and not extended:
                deadline = max(deadline, time.time() + grace_s)
                extended = True
                log.info(
                    "ap_ready_grace_extended",
                    extra={"grace_s": grace_s, "reason": "stdout_ready_no_ifname"},
                )
        dump = _iw_dev_dump()
        ap = _select_ap_from_iw(dump, target_phy=target_phy, ssid=ssid)
        if ap:
            if not extended:
                # AP interface is visible; allow a bit more time for hostapd_cli to respond.
                deadline = max(deadline, time.time() + grace_s)
                extended = True
                log.info(
                    "ap_ready_grace_extended",
                    extra={"grace_s": grace_s, "reason": "ap_iface_visible"},
                )
            if _hostapd_ready(ap.ifname, adapter_ifname=adapter_ifname):
                return ap
            if stdout_ready or _iw_interface_is_ap(ap.ifname):
                if stdout_ready or _iface_is_up(ap.ifname):
                    return ap
        if expected_ap_ifname:
            ap_expected = _select_ap_by_ifname(dump, expected_ap_ifname)
            if ap_expected and (
                _hostapd_ready(expected_ap_ifname, adapter_ifname=adapter_ifname)
                or stdout_ready
                or _iw_interface_is_ap(expected_ap_ifname)
            ):
                if stdout_ready or _iface_is_up(expected_ap_ifname) or _hostapd_ready(
                    expected_ap_ifname, adapter_ifname=adapter_ifname
                ):
                    return ap_expected
            if (
                _hostapd_ready(expected_ap_ifname, adapter_ifname=adapter_ifname)
                or stdout_ready
                or _iw_interface_is_ap(expected_ap_ifname)
            ):
                if stdout_ready or _iface_is_up(expected_ap_ifname):
                    return APReadyInfo(
                        ifname=expected_ap_ifname,
                        phy=target_phy,
                        ssid=ssid,
                        freq_mhz=None,
                        channel=None,
                        channel_width_mhz=None,
                    )

        if not is_running() and not ap and not expected_ap_ifname and not stdout_ready:
            # Engine exited and there is no AP-ready signal to wait for.
            return None

        time.sleep(poll_s)

    return None


def _attempt_start_candidate(
    *,
    cmd: List[str],
    firewalld_cfg: Dict[str, object],
    target_phy: Optional[str],
    ap_ready_timeout_s: float,
    ssid: str,
    adapter_ifname: str,
    expected_ap_ifname: Optional[str],
    require_band: Optional[str],
    require_width_mhz: Optional[int],
    iface_up_grace_s: float = 0.0,
    ap_ready_nohint_retry_s: float = 0.0,
) -> Tuple[Optional[APReadyInfo], Optional[object], Optional[str], Optional[str], List[str], List[str]]:
    res = start_engine(cmd, firewalld_cfg=firewalld_cfg)
    update_state(
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

    latest_stdout = res.stdout_tail
    latest_stderr = res.stderr_tail
    if not res.ok:
        # Early engine exits can race with reader threads and return empty tails.
        # Refresh once so higher-level classifiers can still see actionable errors.
        if not latest_stdout and not latest_stderr:
            try:
                latest_stdout, latest_stderr = get_tails()
            except Exception:
                latest_stdout = res.stdout_tail
                latest_stderr = res.stderr_tail
        if latest_stdout or latest_stderr:
            try:
                update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})
            except Exception:
                pass
        return None, res, "hostapd_failed", res.error, latest_stdout, latest_stderr

    ap_info = _wait_for_ap_ready(
        target_phy,
        ap_ready_timeout_s,
        ssid=ssid,
        adapter_ifname=adapter_ifname,
        expected_ap_ifname=expected_ap_ifname,
    )
    if not ap_info:
        def _as_lines(value: object) -> List[str]:
            if isinstance(value, str):
                return value.splitlines()
            if isinstance(value, list):
                return list(value)
            return []

        def _refresh_tails(default_out: List[str], default_err: List[str]) -> Tuple[List[str], List[str]]:
            try:
                out_now, err_now = get_tails()
            except Exception:
                return default_out, default_err
            out_lines = _as_lines(out_now) or default_out
            err_lines = _as_lines(err_now) or default_err
            return out_lines, err_lines

        latest_stdout, latest_stderr = _refresh_tails(_as_lines(res.stdout_tail), _as_lines(res.stderr_tail))
        if latest_stdout or latest_stderr:
            try:
                update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})
            except Exception:
                pass

        # Some drivers emit decisive hostapd failure lines slightly after AP-ready timeout.
        # Give a brief settle window to capture those lines for accurate classification.
        settle_deadline = time.time() + 1.2
        while is_running() and time.time() < settle_deadline:
            combined_now = list(latest_stdout) + list(latest_stderr)
            if (
                _lines_have_iface_busy_signal(combined_now)
                or _stdout_has_ap_not_ready(combined_now)
                or _stdout_has_ap_ready(combined_now)
            ):
                break
            time.sleep(0.2)
            latest_stdout, latest_stderr = _refresh_tails(latest_stdout, latest_stderr)
        if latest_stdout or latest_stderr:
            try:
                update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})
            except Exception:
                pass

        # If logs indicate AP is coming up, wait a bit longer before failing.
        try:
            combined = list(latest_stdout) + list(latest_stderr)
            ready_hint = _stdout_has_ap_ready(combined) or _stdout_extract_ap_ifname(combined)
        except Exception:
            ready_hint = False

        if ready_hint and is_running():
            extra_wait_s = max(4.0, min(12.0, float(ap_ready_timeout_s)))
            log.info(
                "ap_ready_retry_wait",
                extra={"extra_wait_s": extra_wait_s, "reason": "stdout_ready_hint"},
            )
            ap_info = _wait_for_ap_ready(
                target_phy,
                extra_wait_s,
                ssid=ssid,
                adapter_ifname=adapter_ifname,
                expected_ap_ifname=expected_ap_ifname,
            )
            if ap_info:
                return ap_info, res, None, None, latest_stdout, latest_stderr
        if ap_ready_nohint_retry_s > 0 and is_running():
            extra_wait_s = max(2.0, float(ap_ready_nohint_retry_s))
            log.info(
                "ap_ready_retry_wait",
                extra={"extra_wait_s": extra_wait_s, "reason": "platform_nohint_retry"},
            )
            ap_info = _wait_for_ap_ready(
                target_phy,
                extra_wait_s,
                ssid=ssid,
                adapter_ifname=adapter_ifname,
                expected_ap_ifname=expected_ap_ifname,
            )
            if ap_info:
                return ap_info, res, None, None, latest_stdout, latest_stderr
        combined_lines: List[str] = []
        combined_lines.extend(latest_stdout)
        combined_lines.extend(latest_stderr)
        busy_signal = _lines_have_iface_busy_signal(combined_lines)
        if busy_signal and is_running():
            # If the engine is still alive, busy can be transient while retries run.
            # Give a final brief window before classifying it as a hard failure.
            extra_wait_s = max(2.0, min(8.0, float(ap_ready_timeout_s)))
            log.info(
                "ap_ready_retry_wait",
                extra={"extra_wait_s": extra_wait_s, "reason": "iface_busy_signal_engine_running"},
            )
            ap_info = _wait_for_ap_ready(
                target_phy,
                extra_wait_s,
                ssid=ssid,
                adapter_ifname=adapter_ifname,
                expected_ap_ifname=expected_ap_ifname,
            )
            if ap_info:
                return ap_info, res, None, None, latest_stdout, latest_stderr
            latest_stdout, latest_stderr = _refresh_tails(latest_stdout, latest_stderr)
            combined_lines = list(latest_stdout) + list(latest_stderr)
            busy_signal = _lines_have_iface_busy_signal(combined_lines)
            if latest_stdout or latest_stderr:
                try:
                    update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})
                except Exception:
                    pass
        if busy_signal and not is_running():
            # lnxrouter can report busy/bring-up failures and then exit before AP appears.
            return None, res, "hostapd_failed", "iface_busy", latest_stdout, latest_stderr
        if _stdout_has_ap_not_ready(combined_lines):
            return None, res, "hostapd_failed", "ap_disabled", latest_stdout, latest_stderr
        if not is_running():
            return None, res, "hostapd_failed", "engine_not_running", latest_stdout, latest_stderr
        return None, res, "ap_start_timed_out", None, latest_stdout, latest_stderr

    if not _iface_is_up(ap_info.ifname):
        if _ensure_iface_up_with_grace(ap_info.ifname, grace_s=iface_up_grace_s):
            event = "ap_iface_brought_up"
            extra: Dict[str, object] = {"ap_interface": ap_info.ifname}
            if iface_up_grace_s > 0:
                event = "ap_iface_brought_up_with_grace"
                extra["grace_s"] = iface_up_grace_s
            log.info(event, extra=extra)
        else:
            try:
                latest_stdout, latest_stderr = get_tails()
            except Exception:
                pass
            if latest_stdout or latest_stderr:
                try:
                    update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})
                except Exception:
                    pass

            combined_lines: List[str] = []
            if isinstance(latest_stdout, list):
                combined_lines.extend(latest_stdout)
            if isinstance(latest_stderr, list):
                combined_lines.extend(latest_stderr)
            if _lines_have_iface_busy_signal(combined_lines) and not is_running():
                return None, res, "hostapd_failed", "iface_busy", latest_stdout, latest_stderr
            if not is_running():
                return None, res, "hostapd_failed", "engine_not_running", latest_stdout, latest_stderr
            return None, res, "ap_start_timed_out", "iface_not_up", latest_stdout, latest_stderr

    stable_s = min(2.0, max(1.0, ap_ready_timeout_s / 2.0))
    time.sleep(stable_s)
    if not is_running():
        return None, res, "hostapd_failed", "engine_not_running", latest_stdout, latest_stderr

    iw_info = _parse_iw_dev_info(_iw_dev_info(ap_info.ifname))
    freq_mhz = ap_info.freq_mhz or iw_info.get("freq_mhz")
    channel = ap_info.channel or iw_info.get("channel")
    width_mhz = ap_info.channel_width_mhz or iw_info.get("channel_width_mhz")

    if require_band and freq_mhz is not None:
        detected_band = _band_from_freq_mhz(freq_mhz)
        if detected_band and detected_band != require_band:
            return None, res, "hostapd_started_but_width_not_80", f"band_mismatch:{detected_band}", latest_stdout, latest_stderr

    if require_width_mhz is not None:
        if width_mhz is None or width_mhz != require_width_mhz:
            detail = f"width_mismatch:{width_mhz}" if width_mhz is not None else "width_unknown"
            return None, res, "hostapd_started_but_width_not_80", detail, latest_stdout, latest_stderr

    nm_reason = _nm_interference_reason(ap_info.ifname)
    if nm_reason:
        return None, res, "nm_interference_detected", nm_reason, latest_stdout, latest_stderr

    normalized = APReadyInfo(
        ifname=ap_info.ifname,
        phy=ap_info.phy,
        ssid=ap_info.ssid,
        freq_mhz=freq_mhz,
        channel=channel,
        channel_width_mhz=width_mhz,
    )
    return normalized, res, None, None, latest_stdout, latest_stderr


def _start_hotspot_5ghz_strict(
    *,
    cfg: Dict[str, Any],
    inv: Dict[str, Any],
    ap_ifname: str,
    target_phy: Optional[str],
    ssid: str,
    passphrase: str,
    country: Optional[str],
    ap_security: str,
    ap_ready_timeout_s: float,
    optimized_no_virt: bool,
    debug: bool,
    enable_internet: bool,
    bridge_mode: bool,
    bridge_name: Optional[str],
    bridge_uplink: Optional[str],
    gateway_ip: Optional[str],
    dhcp_start_ip: Optional[str],
    dhcp_end_ip: Optional[str],
    dhcp_dns: Optional[str],
    effective_wifi6: bool,
    tuning_state: Dict[str, object],
    start_warnings: List[str],
    fw_cfg: Dict[str, object],
    firewall_backend: str,
    use_hostapd_nat: bool,
    correlation_id: str,
    enforced_channel_5g: Optional[int],
    allow_fallback_40mhz: bool,
    allow_dfs_channels: bool,
    iface_up_grace_s: float = 0.0,
    ap_ready_nohint_retry_s: float = 0.0,
    pop_timeout_retry_no_virt: bool = False,
) -> LifecycleResult:
    attempts: List[Dict[str, Any]] = []
    preferred_primary_channel: Optional[int] = None
    if enforced_channel_5g is not None:
        preferred_primary_channel = enforced_channel_5g
    else:
        val = cfg.get("channel_5g")
        if val is not None:
            try:
                preferred_primary_channel = int(val)
            except Exception:
                preferred_primary_channel = None

    probe = wifi_probe.probe(
        ap_ifname,
        inventory=inv,
        country=country if isinstance(country, str) else None,
        allow_dfs=allow_dfs_channels,
        preferred_primary_channel=preferred_primary_channel,
    )
    wifi = probe.get("wifi") if isinstance(probe, dict) else {}
    wifi_errors = wifi.get("errors") if isinstance(wifi, dict) else []
    wifi_warnings = wifi.get("warnings") if isinstance(wifi, dict) else []
    for w in wifi_warnings or []:
        start_warnings.append(f"wifi_probe_warning:{w}")

    adapter_info = _get_adapter(inv, ap_ifname) if isinstance(inv, dict) else None
    adapter_supports_ap = bool((adapter_info or {}).get("supports_ap"))
    adapter_supports_5ghz = bool((adapter_info or {}).get("supports_5ghz"))
    adapter_supports_80mhz = bool((adapter_info or {}).get("supports_80mhz"))
    prestart_iface_unready = any(
        isinstance(w, str)
        and (
            w == "ap_iface_not_up_prestart"
            or w == "ap_iface_not_up_post_driver_reload"
            or w.startswith("ap_iface_driver_reload:")
        )
        for w in start_warnings
    )
    recoverable_probe_codes = {"driver_no_ap_mode_5ghz", "driver_no_vht80_or_he80"}
    recoverable_probe_errors = bool(wifi_errors) and all(
        isinstance(err, dict) and str(err.get("code", "")) in recoverable_probe_codes
        for err in (wifi_errors or [])
    )
    can_degrade_probe_errors = (
        pop_timeout_retry_no_virt
        and prestart_iface_unready
        and adapter_supports_ap
        and adapter_supports_5ghz
        and adapter_supports_80mhz
        and recoverable_probe_errors
    )

    if wifi_errors and not can_degrade_probe_errors:
        last_error = wifi_errors[0].get("code") if isinstance(wifi_errors[0], dict) else "wifi_probe_failed"
        warnings = list(start_warnings)
        warnings.extend(_safe_revert_tuning(tuning_state))
        state = update_state(
            phase="error",
            running=False,
            adapter=ap_ifname,
            ap_interface=None,
            last_error=last_error,
            last_error_detail={"errors": wifi_errors},
            last_correlation_id=correlation_id,
            fallback_reason=None,
            warnings=warnings,
            attempts=attempts,
            tuning={},
            network_tuning={},
        )
        return LifecycleResult("start_failed", state)
    if wifi_errors and can_degrade_probe_errors:
        start_warnings.append("wifi_probe_errors_degraded_platform_pop")
        for err in wifi_errors:
            if not isinstance(err, dict):
                continue
            code = str(err.get("code", "")).strip()
            if not code:
                continue
            reason = str((err.get("context") or {}).get("reason", "")).strip()
            if reason:
                start_warnings.append(f"wifi_probe_error_degraded:{code}:{reason}")
            else:
                start_warnings.append(f"wifi_probe_error_degraded:{code}")

    candidates = wifi.get("candidates") if isinstance(wifi, dict) else []
    if (not candidates) and can_degrade_probe_errors:
        default_candidates: List[Dict[str, Any]] = [
            {
                "band": 5,
                "width": 80,
                "primary_channel": 36,
                "center_channel": 42,
                "country": country if isinstance(country, str) else None,
                "flags": ["non_dfs"],
                "rationale": "pop_probe_default_36_48",
            },
            {
                "band": 5,
                "width": 80,
                "primary_channel": 149,
                "center_channel": 155,
                "country": country if isinstance(country, str) else None,
                "flags": ["non_dfs"],
                "rationale": "pop_probe_default_149_161",
            },
        ]
        if preferred_primary_channel in (149, 153, 157, 161):
            default_candidates = [default_candidates[1], default_candidates[0]]
        candidates = default_candidates
        start_warnings.append("wifi_probe_default_candidates_used")

    if not candidates:
        last_error = "non_dfs_80mhz_channels_unavailable"
        warnings = list(start_warnings)
        warnings.extend(_safe_revert_tuning(tuning_state))
        state = update_state(
            phase="error",
            running=False,
            adapter=ap_ifname,
            ap_interface=None,
            last_error=last_error,
            last_error_detail=wifi_probe.build_error_detail(last_error, {"reason": "no_candidates"}),
            last_correlation_id=correlation_id,
            fallback_reason=None,
            warnings=warnings,
            attempts=attempts,
            tuning={},
            network_tuning={},
        )
        return LifecycleResult("start_failed", state)
    counts = wifi.get("counts") if isinstance(wifi, dict) else {}
    dfs_count = counts.get("dfs") if isinstance(counts, dict) else None
    log.info(
        f"wifi_probe_candidates_80 count={len(candidates)} dfs={dfs_count}",
        extra={"correlation_id": correlation_id},
    )

    beacon_interval = int(cfg.get("beacon_interval", 50))
    dtim_period = int(cfg.get("dtim_period", 1))
    short_guard_interval = bool(cfg.get("short_guard_interval", True))
    tx_power = cfg.get("tx_power")
    if tx_power is not None:
        try:
            tx_power = int(tx_power)
        except Exception:
            tx_power = None

    last_failure_code: Optional[str] = None
    last_failure_detail: Optional[str] = None
    ap_info_final: Optional[APReadyInfo] = None
    res_final = None
    selected_candidate: Optional[Dict[str, Any]] = None

    # Defensive guard: if caller missed passing the Pop!_OS flag, detect here
    # so iface-busy retries still prefer no-virt mode.
    if (not pop_timeout_retry_no_virt) and os_release.is_pop_os():
        pop_timeout_retry_no_virt = True
        start_warnings.append("platform_pop_retry_no_virt_autodetected")

    def _expected_ifname(no_virt: bool, force_hostapd_nat: bool = False) -> Optional[str]:
        if use_hostapd_nat or force_hostapd_nat or bridge_mode:
            return ap_ifname if no_virt else _virt_ap_ifname(ap_ifname)
        return _lnxrouter_expected_ifname(ap_ifname, no_virt=no_virt)

    def _build_cmd_for_candidate(
        candidate: Dict[str, Any],
        no_virt: bool,
        width_mhz: int,
        force_hostapd_nat: bool = False,
    ) -> List[str]:
        ch = candidate.get("primary_channel")
        center = candidate.get("center_channel")
        width_str = str(width_mhz)
        strict_width = width_mhz >= 80
        effective_hostapd_nat = use_hostapd_nat or force_hostapd_nat
        if bridge_mode:
            return build_cmd_bridge(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band="5ghz",
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=int(ch) if ch is not None else None,
                no_virt=no_virt,
                debug=debug,
                wifi6=effective_wifi6,
                bridge_name=str(bridge_name).strip() if isinstance(bridge_name, str) else None,
                bridge_uplink=str(bridge_uplink).strip() if isinstance(bridge_uplink, str) else None,
                channel_width=width_str,
                beacon_interval=beacon_interval,
                dtim_period=dtim_period,
                short_guard_interval=short_guard_interval,
                tx_power=tx_power,
            )
        if effective_hostapd_nat:
            return build_cmd_nat(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band="5ghz",
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=int(ch) if ch is not None else None,
                no_virt=no_virt,
                debug=debug,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_start_ip=dhcp_start_ip,
                dhcp_end_ip=dhcp_end_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
                channel_width=width_str,
                beacon_interval=beacon_interval,
                dtim_period=dtim_period,
                short_guard_interval=short_guard_interval,
                tx_power=tx_power,
                strict_width=strict_width,
            )
        return build_cmd(
            ap_ifname=ap_ifname,
            ssid=ssid,
            passphrase=passphrase,
            band_preference="5ghz",
            country=country if isinstance(country, str) else None,
            channel=int(ch) if ch is not None else None,
            no_virt=no_virt,
            wifi6=effective_wifi6,
            channel_width=width_str,
            center_channel=int(center) if center is not None else None,
            gateway_ip=gateway_ip,
            dhcp_dns=dhcp_dns,
            enable_internet=enable_internet,
        )

    def _cleanup_attempt() -> None:
        _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
        _remove_conf_dirs(ap_ifname)
        try:
            cleanup_phy = None if pop_timeout_retry_no_virt else target_phy
            _cleanup_virtual_ap_ifaces(target_phy=cleanup_phy)
        except Exception:
            pass

    for candidate in candidates:
        if pop_timeout_retry_no_virt:
            nm_state_now = _nm_device_state(ap_ifname)
            if (not _iface_is_up(ap_ifname)) or (not _nm_state_non_interfering(nm_state_now)):
                prep_loop_warnings = _prepare_ap_interface(ap_ifname, force_nm_disconnect=True)
                if prep_loop_warnings:
                    start_warnings.extend(prep_loop_warnings)

        # In hostapd_nat virtual-first mode, keep the original iface for the
        # initial no-virt retry and only reselect after explicit parent-missing
        # evidence from that retry path.
        prestart_missing_reselect = (
            pop_timeout_retry_no_virt
            and (not _iface_exists(ap_ifname))
            and (optimized_no_virt or (not use_hostapd_nat))
        )
        if prestart_missing_reselect:
            start_warnings.append("ap_iface_missing_prestart")
            old_ifname = ap_ifname
            ap_ifname, inv, adapter_now, reselect_warnings = _maybe_reselect_ap_after_prestart_failure(
                ap_ifname=ap_ifname,
                preferred_ifname=cfg.get("ap_adapter") if isinstance(cfg.get("ap_adapter"), str) else None,
                band_pref="5ghz",
                inv=inv,
                adapter=_get_adapter(inv, old_ifname),
                platform_is_pop=True,
                prep_warnings=["ap_iface_not_up_prestart"],
            )
            if reselect_warnings:
                start_warnings.extend(reselect_warnings)
            if ap_ifname != old_ifname:
                target_phy = _get_adapter_phy(inv, ap_ifname)
                prep_retry_warnings = _prepare_ap_interface(ap_ifname, force_nm_disconnect=True)
                if prep_retry_warnings:
                    start_warnings.extend(prep_retry_warnings)

        log.info(
            f"start_candidate_attempt band=5 width=80 channel={candidate.get('primary_channel')}",
            extra={"correlation_id": correlation_id},
        )
        cmd = _build_cmd_for_candidate(candidate, optimized_no_virt, 80)
        ap_info, res, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
            cmd=cmd,
            firewalld_cfg=fw_cfg,
            target_phy=target_phy,
            ap_ready_timeout_s=ap_ready_timeout_s,
            ssid=ssid,
            adapter_ifname=ap_ifname,
            expected_ap_ifname=_expected_ifname(optimized_no_virt),
            require_band="5ghz",
            require_width_mhz=80,
            iface_up_grace_s=iface_up_grace_s,
            ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
        )
        if ap_info:
            attempts.append({"candidate": candidate, "failure_reason": None, "no_virt": optimized_no_virt})
            ap_info_final = ap_info
            res_final = res
            selected_candidate = candidate
            break

        attempts.append(
            {
                "candidate": candidate,
                "failure_reason": failure_code,
                "failure_detail": failure_detail,
                "no_virt": optimized_no_virt,
            }
        )
        last_failure_code = failure_code
        last_failure_detail = failure_detail

        out_lines = out_tail.splitlines() if isinstance(out_tail, str) else list(out_tail or [])
        err_lines = err_tail.splitlines() if isinstance(err_tail, str) else list(err_tail or [])
        pop_unstable_iface_state = (
            pop_timeout_retry_no_virt
            and failure_code in ("ap_start_timed_out", "hostapd_failed")
            and failure_detail in ("iface_not_up", "ap_disabled", "engine_not_running")
        )
        busy_error = (
            failure_detail == "iface_busy"
            or pop_unstable_iface_state
            or _lines_have_iface_busy_signal(out_lines)
            or _lines_have_iface_busy_signal(err_lines)
        )
        virt_iface_missing_error = (
            _lines_have_virtual_iface_missing_signal(out_lines)
            or _lines_have_virtual_iface_missing_signal(err_lines)
        )
        if (busy_error or virt_iface_missing_error) and (not bridge_mode):
            if busy_error:
                start_warnings.append("ap_iface_busy_recovery")
            if virt_iface_missing_error:
                start_warnings.append("virt_iface_missing_recovery")
            prep_warnings = _prepare_ap_interface(ap_ifname, force_nm_disconnect=True)
            if prep_warnings:
                start_warnings.extend(prep_warnings)

            if use_hostapd_nat and (not optimized_no_virt):
                # hostapd_nat with virtual iface can fail on some drivers/sessions
                # (busy or transient parent-iface disappearance). Retry on the parent
                # iface (--no-virt) before moving to next channel.
                if virt_iface_missing_error:
                    start_warnings.append("virt_iface_missing_retry_no_virt")
                else:
                    start_warnings.append("iface_busy_retry_no_virt")
                _cleanup_attempt()
                retry_no_virt = True
                cmd_retry = _build_cmd_for_candidate(candidate, retry_no_virt, 80)
                ap_info_retry, res_retry, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                    cmd=cmd_retry,
                    firewalld_cfg=fw_cfg,
                    target_phy=target_phy,
                    ap_ready_timeout_s=ap_ready_timeout_s,
                    ssid=ssid,
                    adapter_ifname=ap_ifname,
                    expected_ap_ifname=_expected_ifname(retry_no_virt),
                    require_band="5ghz",
                    require_width_mhz=80,
                    iface_up_grace_s=iface_up_grace_s,
                    ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
                )
                if ap_info_retry:
                    attempts.append(
                        {
                            "candidate": candidate,
                            "failure_reason": None,
                            "no_virt": retry_no_virt,
                            "engine": "hostapd_nat",
                        }
                    )
                    ap_info_final = ap_info_retry
                    res_final = res_retry
                    selected_candidate = candidate
                    break

                retry_out_lines = out_tail.splitlines() if isinstance(out_tail, str) else list(out_tail or [])
                retry_err_lines = err_tail.splitlines() if isinstance(err_tail, str) else list(err_tail or [])
                parent_iface_missing = (
                    _lines_have_parent_iface_missing_signal(retry_out_lines, ap_ifname)
                    or _lines_have_parent_iface_missing_signal(retry_err_lines, ap_ifname)
                    or (not os.path.exists(f"/sys/class/net/{ap_ifname}"))
                )
                if parent_iface_missing and pop_timeout_retry_no_virt:
                    start_warnings.append("ap_parent_iface_missing_reselect")
                    _cleanup_attempt()
                    old_ifname = ap_ifname
                    ap_ifname, inv, adapter_now, reselect_warnings = _maybe_reselect_ap_after_prestart_failure(
                        ap_ifname=ap_ifname,
                        preferred_ifname=cfg.get("ap_adapter") if isinstance(cfg.get("ap_adapter"), str) else None,
                        band_pref="5ghz",
                        inv=inv,
                        adapter=_get_adapter(inv, old_ifname),
                        platform_is_pop=True,
                        prep_warnings=["ap_iface_not_up_post_driver_reload"],
                    )
                    if reselect_warnings:
                        start_warnings.extend(reselect_warnings)
                    # Retry once more after parent-iface recovery even when the
                    # iface name is unchanged. On Pop!_OS, USB adapters can
                    # transiently disappear/reappear under the same ifname.
                    target_phy = _get_adapter_phy(inv, ap_ifname)
                    prep_retry_warnings = _prepare_ap_interface(ap_ifname, force_nm_disconnect=True)
                    if prep_retry_warnings:
                        start_warnings.extend(prep_retry_warnings)
                    cmd_retry2 = _build_cmd_for_candidate(candidate, retry_no_virt, 80)
                    ap_info_retry2, res_retry2, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                        cmd=cmd_retry2,
                        firewalld_cfg=fw_cfg,
                        target_phy=target_phy,
                        ap_ready_timeout_s=ap_ready_timeout_s,
                        ssid=ssid,
                        adapter_ifname=ap_ifname,
                        expected_ap_ifname=_expected_ifname(retry_no_virt),
                        require_band="5ghz",
                        require_width_mhz=80,
                        iface_up_grace_s=iface_up_grace_s,
                        ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
                    )
                    if ap_info_retry2:
                        attempts.append(
                            {
                                "candidate": candidate,
                                "failure_reason": None,
                                "no_virt": retry_no_virt,
                                "engine": "hostapd_nat",
                            }
                        )
                        ap_info_final = ap_info_retry2
                        res_final = res_retry2
                        selected_candidate = candidate
                        break

                attempts.append(
                    {
                        "candidate": candidate,
                        "failure_reason": failure_code,
                        "failure_detail": failure_detail,
                        "no_virt": retry_no_virt,
                        "engine": "hostapd_nat",
                    }
                )
                last_failure_code = failure_code
                last_failure_detail = failure_detail
                _cleanup_attempt()
                # If parent-iface AP mode is still busy after recovery, changing channels
                # is unlikely to help on this adapter/session; fail fast.
                if failure_detail == "iface_busy":
                    if pop_timeout_retry_no_virt:
                        start_warnings.append("ap_iface_busy_continue_channel_hopping")
                    else:
                        start_warnings.append("ap_iface_busy_abort_channel_hopping")
                        break
                continue

            if use_hostapd_nat and optimized_no_virt and pop_timeout_retry_no_virt:
                # Already in no-virt mode; busy scans on Pop!_OS can still clear
                # with a single in-place retry after interface preparation.
                start_warnings.append("iface_busy_retry_same_mode")
                _cleanup_attempt()
                retry_no_virt = True
                cmd_retry = _build_cmd_for_candidate(candidate, retry_no_virt, 80)
                ap_info_retry, res_retry, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                    cmd=cmd_retry,
                    firewalld_cfg=fw_cfg,
                    target_phy=target_phy,
                    ap_ready_timeout_s=ap_ready_timeout_s,
                    ssid=ssid,
                    adapter_ifname=ap_ifname,
                    expected_ap_ifname=_expected_ifname(retry_no_virt),
                    require_band="5ghz",
                    require_width_mhz=80,
                    iface_up_grace_s=iface_up_grace_s,
                    ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
                )
                if ap_info_retry:
                    attempts.append(
                        {
                            "candidate": candidate,
                            "failure_reason": None,
                            "no_virt": retry_no_virt,
                            "engine": "hostapd_nat",
                        }
                    )
                    ap_info_final = ap_info_retry
                    res_final = res_retry
                    selected_candidate = candidate
                    break

                attempts.append(
                    {
                        "candidate": candidate,
                        "failure_reason": failure_code,
                        "failure_detail": failure_detail,
                        "no_virt": retry_no_virt,
                        "engine": "hostapd_nat",
                    }
                )
                last_failure_code = failure_code
                last_failure_detail = failure_detail
                _cleanup_attempt()
                continue

            if not use_hostapd_nat:
                # Pop!/Ubuntu combinations can fail in lnxrouter with RTNETLINK busy.
                # Retry once using hostapd_nat; Pop!_OS prefers no-virt here.
                retry_no_virt = bool(pop_timeout_retry_no_virt)
                if retry_no_virt:
                    start_warnings.append("iface_busy_retry_hostapd_nat_no_virt")
                else:
                    start_warnings.append("iface_busy_retry_hostapd_nat")
                _cleanup_attempt()
                cmd_retry = _build_cmd_for_candidate(
                    candidate,
                    retry_no_virt,
                    80,
                    force_hostapd_nat=True,
                )
                ap_info_retry, res_retry, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                    cmd=cmd_retry,
                    firewalld_cfg=fw_cfg,
                    target_phy=target_phy,
                    ap_ready_timeout_s=ap_ready_timeout_s,
                    ssid=ssid,
                    adapter_ifname=ap_ifname,
                    expected_ap_ifname=_expected_ifname(retry_no_virt, force_hostapd_nat=True),
                    require_band="5ghz",
                    require_width_mhz=80,
                    iface_up_grace_s=iface_up_grace_s,
                    ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
                )
                if ap_info_retry:
                    attempts.append(
                        {
                            "candidate": candidate,
                            "failure_reason": None,
                            "no_virt": retry_no_virt,
                            "engine": "hostapd_nat",
                        }
                    )
                    ap_info_final = ap_info_retry
                    res_final = res_retry
                    selected_candidate = candidate
                    break

                attempts.append(
                    {
                        "candidate": candidate,
                        "failure_reason": failure_code,
                        "failure_detail": failure_detail,
                        "no_virt": retry_no_virt,
                        "engine": "hostapd_nat",
                    }
                )
                last_failure_code = failure_code
                last_failure_detail = failure_detail
                _cleanup_attempt()
                continue

        pop_timeout_retry = (
            pop_timeout_retry_no_virt
            and (not bridge_mode)
            and (not optimized_no_virt)
            and failure_code in ("ap_start_timed_out", "hostapd_failed")
            and (
                not failure_detail
                or failure_detail in ("iface_not_up", "ap_disabled", "engine_not_running")
                or str(failure_detail).startswith("engine_exited_early")
            )
        )
        if pop_timeout_retry:
            start_warnings.append("platform_pop_timeout_retry_no_virt")
            _cleanup_attempt()
            retry_no_virt = True
            cmd_retry = _build_cmd_for_candidate(candidate, retry_no_virt, 80)
            ap_info_retry, res_retry, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                cmd=cmd_retry,
                firewalld_cfg=fw_cfg,
                target_phy=target_phy,
                ap_ready_timeout_s=ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=_expected_ifname(retry_no_virt),
                require_band="5ghz",
                require_width_mhz=80,
                iface_up_grace_s=iface_up_grace_s,
                ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
            )
            if ap_info_retry:
                attempts.append({"candidate": candidate, "failure_reason": None, "no_virt": retry_no_virt})
                ap_info_final = ap_info_retry
                res_final = res_retry
                selected_candidate = candidate
                start_warnings.append("platform_pop_timeout_retry_no_virt_used")
                break

            attempts.append(
                {
                    "candidate": candidate,
                    "failure_reason": failure_code,
                    "failure_detail": failure_detail,
                    "no_virt": retry_no_virt,
                }
            )
            last_failure_code = failure_code
            last_failure_detail = failure_detail
            _cleanup_attempt()
            continue

        driver_error = _stdout_has_hostapd_driver_error(out_tail or [])
        if driver_error and (not bridge_mode):
            start_warnings.append("optimized_no_virt_retry_with_virt" if optimized_no_virt else "optimized_virt_retry_with_no_virt")
            _cleanup_attempt()
            retry_no_virt = not optimized_no_virt
            cmd_retry = _build_cmd_for_candidate(candidate, retry_no_virt, 80)
            ap_info_retry, res_retry, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                cmd=cmd_retry,
                firewalld_cfg=fw_cfg,
                target_phy=target_phy,
                ap_ready_timeout_s=ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=_expected_ifname(retry_no_virt),
                require_band="5ghz",
                require_width_mhz=80,
                iface_up_grace_s=iface_up_grace_s,
                ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
            )
            if ap_info_retry:
                attempts.append({"candidate": candidate, "failure_reason": None, "no_virt": retry_no_virt})
                ap_info_final = ap_info_retry
                res_final = res_retry
                selected_candidate = candidate
                break

            attempts.append(
                {
                    "candidate": candidate,
                    "failure_reason": failure_code,
                    "failure_detail": failure_detail,
                    "no_virt": retry_no_virt,
                }
            )
            last_failure_code = failure_code
            last_failure_detail = failure_detail

        _cleanup_attempt()

    if not ap_info_final and allow_fallback_40mhz:
        log.info("pro_mode_fallback_40mhz_enabled", extra={"correlation_id": correlation_id})
        fallback = wifi_probe.probe_5ghz_40(
            ap_ifname,
            inventory=inv,
            country=country if isinstance(country, str) else None,
            allow_dfs=allow_dfs_channels,
            preferred_primary_channel=preferred_primary_channel,
        )
        fallback_candidates = fallback.get("candidates") if isinstance(fallback, dict) else []
        for candidate in fallback_candidates:
            cmd = _build_cmd_for_candidate(candidate, optimized_no_virt, 40)
            ap_info, res, failure_code, failure_detail, out_tail, err_tail = _attempt_start_candidate(
                cmd=cmd,
                firewalld_cfg=fw_cfg,
                target_phy=target_phy,
                ap_ready_timeout_s=ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=_expected_ifname(optimized_no_virt),
                require_band="5ghz",
                require_width_mhz=40,
                iface_up_grace_s=iface_up_grace_s,
                ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
            )
            if ap_info:
                attempts.append({"candidate": candidate, "failure_reason": None, "no_virt": optimized_no_virt})
                ap_info_final = ap_info
                res_final = res
                selected_candidate = candidate
                start_warnings.append("pro_mode_fallback_40mhz_used")
                break

            attempts.append(
                {
                    "candidate": candidate,
                    "failure_reason": failure_code,
                    "failure_detail": failure_detail,
                    "no_virt": optimized_no_virt,
                }
            )
            last_failure_code = failure_code
            last_failure_detail = failure_detail
            _cleanup_attempt()

    if ap_info_final:
        detected_band = _band_from_freq_mhz(ap_info_final.freq_mhz) or "5ghz"
        affinity_pids = _collect_affinity_pids(
            adapter_ifname=ap_ifname,
            ap_interface=ap_info_final.ifname,
            engine_pid=res_final.pid if res_final else None,
        )
        try:
            tuning_state, runtime_warnings = system_tuning.apply_runtime(
                tuning_state,
                cfg,
                ap_ifname=ap_info_final.ifname,
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
                ap_ifname=ap_info_final.ifname,
                enable_internet=enable_internet,
                firewalld_cfg=fw_cfg,
                firewall_backend=firewall_backend,
            )
        except Exception as e:
            net_state = {}
            net_warnings = [f"network_tuning_apply_failed:{e}"]
        if net_warnings:
            start_warnings.extend(net_warnings)

        selected_channel = ap_info_final.channel
        if selected_channel is None and selected_candidate:
            selected_channel = selected_candidate.get("primary_channel")
        selected_width = ap_info_final.channel_width_mhz
        selected_country = None
        if selected_candidate:
            selected_country = selected_candidate.get("country")
        if not selected_country and isinstance(country, str):
            selected_country = country
        mode = "fallback" if "pro_mode_fallback_40mhz_used" in start_warnings else "optimized"
        fallback_reason = "pro_mode_40mhz" if mode == "fallback" else None
        state = update_state(
            phase="running",
            running=True,
            adapter=ap_ifname,
            ap_interface=ap_info_final.ifname,
            band=detected_band,
            channel_width_mhz=ap_info_final.channel_width_mhz,
            selected_band=detected_band,
            selected_width_mhz=selected_width,
            selected_channel=selected_channel,
            selected_country=selected_country,
            mode=mode,
            fallback_reason=fallback_reason,
            warnings=start_warnings,
            last_error=None,
            last_error_detail=None,
            last_correlation_id=correlation_id,
            attempts=attempts,
            tuning=tuning_state,
            network_tuning=net_state,
            engine={"last_error": None, "last_exit_code": None, "ap_logs_tail": []},
        )
        if _watchdog_enabled(cfg) and is_running():
            _ensure_watchdog_started()
        return LifecycleResult("started" if mode == "optimized" else "started_with_fallback", state)

    last_error = last_failure_code or "ap_start_timed_out"
    error_detail = wifi_probe.build_error_detail(last_error, {"detail": last_failure_detail})
    warnings = list(start_warnings)
    warnings.extend(_safe_revert_tuning(tuning_state))
    state = update_state(
        phase="error",
        running=False,
        adapter=ap_ifname,
        ap_interface=None,
        last_error=last_error,
        last_error_detail=error_detail,
        last_correlation_id=correlation_id,
        fallback_reason=None,
        warnings=warnings,
        attempts=attempts,
        tuning={},
        network_tuning={},
    )
    return LifecycleResult("start_failed", state)


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


_HOSTAPD_DRIVER_ERROR_PATTERNS = (
    "Could not set channel for kernel driver",
    "Failed to set beacon parameters",
    "Could not connect to kernel driver",
    "Interface initialization failed",
    "Unable to setup interface",
    "nl80211: Failed to set beacon",
    "nl80211: Failed to set interface",
)

_IFACE_BUSY_PATTERNS = (
    "rtnetlink answers: device or resource busy",
    "name not unique on network",
    "failed bringing",
    "device or resource busy",
    "too many open files in system",
    "failed to request a scan of neighboring bsses",
)

_VIRT_AP_IFACE_RE = re.compile(r"^x\d+(.+)$")


def _normalize_ap_adapter(preferred: Optional[str], inv: Optional[dict]) -> Optional[str]:
    if not preferred or not isinstance(preferred, str):
        return preferred
    preferred = preferred.strip()
    m = _VIRT_AP_IFACE_RE.match(preferred)
    if not m:
        # Handle stale physical ifnames after USB driver resets (wlx* can change).
        if os.path.exists(f"/sys/class/net/{preferred}"):
            return preferred
        if isinstance(inv, dict) and _get_adapter(inv, preferred):
            return preferred
        if isinstance(inv, dict):
            rec = inv.get("recommended")
            if isinstance(rec, str) and rec and _get_adapter(inv, rec):
                log.warning(
                    "ap_adapter_missing_fallback_recommended",
                    extra={"preferred": preferred, "fallback": rec},
                )
                return rec
            # If recommendation is unavailable, pick any AP-capable adapter, preferring USB.
            adapters = inv.get("adapters") if isinstance(inv.get("adapters"), list) else []
            usb_ap = [a.get("ifname") for a in adapters if a.get("supports_ap") and a.get("bus") == "usb" and a.get("ifname")]
            if usb_ap:
                fallback = str(usb_ap[0])
                log.warning(
                    "ap_adapter_missing_fallback_usb",
                    extra={"preferred": preferred, "fallback": fallback},
                )
                return fallback
            any_ap = [a.get("ifname") for a in adapters if a.get("supports_ap") and a.get("ifname")]
            if any_ap:
                fallback = str(any_ap[0])
                log.warning(
                    "ap_adapter_missing_fallback_any_ap",
                    extra={"preferred": preferred, "fallback": fallback},
                )
                return fallback
        return preferred
    base = m.group(1).strip()
    if not base:
        return preferred
    if os.path.exists(f"/sys/class/net/{base}"):
        return base
    if isinstance(inv, dict) and _get_adapter(inv, base):
        return base
    return preferred


def _stdout_has_hostapd_driver_error(lines: List[str]) -> bool:
    for line in lines:
        for pattern in _HOSTAPD_DRIVER_ERROR_PATTERNS:
            if pattern in line:
                return True
    return False


def _lines_have_iface_busy_signal(lines: List[str]) -> bool:
    for line in lines:
        low = str(line or "").lower()
        for pattern in _IFACE_BUSY_PATTERNS:
            if pattern in low:
                return True
    return False


def _lines_have_virtual_iface_missing_signal(lines: List[str]) -> bool:
    """
    Detect hostapd_nat virtual-AP creation failures like:
      iw dev <if> interface add x0<if> type __ap
      ... No such device (-19)
    """
    if not lines:
        return False
    saw_no_such_device = False
    saw_virtual_add = False
    saw_virtual_iface_missing = False
    for line in lines:
        low = str(line or "").lower()
        if "no such device" in low or "cannot find device" in low:
            saw_no_such_device = True
        if "interface add" in low and "type __ap" in low:
            saw_virtual_add = True
        if "cmd=/usr/sbin/iw dev" in low and "interface add" in low and "type __ap" in low:
            saw_virtual_add = True
        if "cannot find device" in low and ("\"x0" in low or " iface=x0" in low or " iface=x1" in low):
            saw_virtual_iface_missing = True
        if "no such device" in low and (" x0" in low or "\"x0" in low or " iface=x0" in low):
            saw_virtual_iface_missing = True
    if saw_virtual_iface_missing:
        return True
    return saw_no_such_device and saw_virtual_add


def _lines_have_parent_iface_missing_signal(lines: List[str], ifname: Optional[str]) -> bool:
    if not lines or not ifname:
        return False
    token = str(ifname).strip().lower()
    if not token:
        return False
    for line in lines:
        low = str(line or "").lower()
        if f'cannot find device "{token}"' in low or f"cannot find device '{token}'" in low:
            return True
        if f"iface={token}" in low and "no such device" in low:
            return True
        if "cmd=/usr/sbin/ip link set" in low and f" {token} up" in low and "cannot find device" in low:
            return True
        if "cmd=/usr/sbin/iw dev" in low and f" {token} " in low and "no such device" in low:
            return True
    return False


def _stdout_has_ap_enabled(lines: List[str], ifname: str) -> bool:
    if not ifname:
        return False
    needle = f"{ifname}: AP-ENABLED"
    return any(needle in line for line in lines)


_STDOUT_AP_READY_PATTERNS = (
    "AP-ENABLED",
    "interface state HT_SCAN->ENABLED",
)
_STDOUT_AP_NOT_READY_PATTERNS = (
    "AP-DISABLED",
    "interface state HT_SCAN->DISABLED",
    "CTRL-EVENT-TERMINATING",
)

_STDOUT_CREATED_IFACE_RE = re.compile(r"\b([A-Za-z0-9._-]{1,15})\s+created\b")


def _stdout_has_ap_ready(lines: List[str]) -> bool:
    for line in lines:
        for pattern in _STDOUT_AP_READY_PATTERNS:
            if pattern in line:
                return True
    return False


def _stdout_has_ap_not_ready(lines: List[str]) -> bool:
    for line in lines:
        for pattern in _STDOUT_AP_NOT_READY_PATTERNS:
            if pattern in line:
                return True
    return False


def _stdout_extract_ap_ifname(lines: List[str]) -> Optional[str]:
    for raw in reversed(lines):
        m = _STDOUT_CREATED_IFACE_RE.search(raw)
        if m:
            cand = m.group(1).strip()
            if cand:
                return cand
    for raw in reversed(lines):
        if any(pattern in raw for pattern in _STDOUT_AP_READY_PATTERNS):
            if ":" in raw:
                cand = raw.split(":", 1)[0].strip()
                if cand:
                    return cand
    return None


def _iw_interface_is_ap(ifname: str) -> bool:
    if not ifname:
        return False
    try:
        info = _iw_dev_info(ifname)
    except Exception:
        return False
    for raw in info.splitlines():
        if "type AP" in raw:
            return True
    return False


def _infer_ap_ifname_from_conf(adapter_ifname: Optional[str]) -> Optional[str]:
    if not adapter_ifname:
        return None
    conf_dir = _find_latest_conf_dir(adapter_ifname, None)
    if not conf_dir:
        return None
    for reader in (
        lnxrouter_conf.read_hostapd_conf_interface,
        lnxrouter_conf.read_dnsmasq_conf_interface,
        lnxrouter_conf.read_subn_iface,
    ):
        try:
            ifname = reader(conf_dir)
        except Exception:
            ifname = None
        if ifname:
            return ifname
    return None


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
        hostapd_ok = _hostapd_pid_running(conf_dir)
        dnsmasq_ok = (not expect_dns) or _dnsmasq_pid_running(conf_dir)

        # hostapd_nat_engine only writes pidfiles on some platforms.
        # When pidfiles are absent/stale, fall back to runtime process checks.
        if not hostapd_ok and ap_interface:
            hostapd_ok = _hostapd_ready(ap_interface, adapter_ifname=adapter_ifname)
        if not hostapd_ok and engine_pid and _pid_running(engine_pid):
            hostapd_ok = any(_pid_is_hostapd(pid) for pid in _child_pids(engine_pid))
        if expect_dns and not dnsmasq_ok and engine_pid and _pid_running(engine_pid):
            dnsmasq_ok = any(_pid_is_dnsmasq(pid) for pid in _child_pids(engine_pid))

        if not hostapd_ok:
            return "hostapd_exited"
        if expect_dns and not dnsmasq_ok:
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
            ap_interface_hint=state.get("ap_interface"),
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
    # Guard against stale watchdog ticks: only restart when state is still running.
    st_guard = load_state()
    if not isinstance(st_guard, dict) or not st_guard.get("running") or st_guard.get("phase") != "running":
        return

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
    
    try:
        with _OP_LOCK:
            _stop_hotspot_impl(correlation_id=cid + ":stop")
            _start_hotspot_impl(correlation_id=cid + ":start")
    except Exception as exc:
        try:
            st = load_state()
            warnings = list(st.get("warnings") if isinstance(st, dict) and st.get("warnings") else [])
            warnings.append(f"watchdog_restart_failed:{reason}:{type(exc).__name__}")
            update_state(warnings=warnings)
        except Exception:
            pass
        return

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
                            ap_interface_hint=st.get("ap_interface"),
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
            subprocess.run(
                [_iw_bin(), "dev", ifname, "del"],
                check=False,
                capture_output=True,
                text=True,
                timeout=_CMD_TIMEOUT_S,
            )
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
            ap_ifname = _normalize_ap_adapter(preferred.strip(), inv)
        else:
            ap_ifname = inv.get("recommended") or _select_ap_adapter(inv, cfg.get("band_preference", "5ghz"))
        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception:
        ap_ifname = None
        target_phy = None

    _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
    removed_conf_dirs = _remove_conf_dirs(ap_ifname)

    try:
        # Pop!_OS USB adapters can re-enumerate PHYs; stale virtual AP ifaces on
        # old PHYs can poison subsequent starts, so clean across all PHYs there.
        cleanup_phy = None if os_release.is_pop_os() else target_phy
        removed_ifaces = _cleanup_virtual_ap_ifaces(target_phy=cleanup_phy)
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


def start_hotspot(correlation_id: str = "start", overrides: Optional[dict] = None, basic_mode: bool = False):
    with _OP_LOCK:
        return _start_hotspot_impl(correlation_id=correlation_id, overrides=overrides, basic_mode=basic_mode)


def _start_hotspot_impl(correlation_id: str = "start", overrides: Optional[dict] = None, basic_mode: bool = False):
    ensure_config_file()
    state = load_state()
    if state.get("phase") in ("starting", "running") and is_running():
        return LifecycleResult("already_running", state)

    _repair_impl(correlation_id=correlation_id)

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
    allow_fallback_40mhz = bool(cfg.get("allow_fallback_40mhz", False))
    allow_dfs_channels = bool(cfg.get("allow_dfs_channels", False))
    firewall_probe = wifi_probe.detect_firewall_backends()
    firewall_backend = firewall_probe.get("selected_backend") or "unknown"
    platform_info = os_release.read_os_release()
    platform_warnings: List[str] = []
    try:
        cfg, platform_warnings = os_release.apply_platform_overrides(cfg, platform_info)
    except Exception as e:
        platform_warnings = [f"platform_overrides_failed:{e}"]
    platform_is_cachyos = os_release.is_cachyos(platform_info)
    platform_is_pop = os_release.is_pop_os(platform_info)
    use_hostapd_nat = os_release.is_bazzite(platform_info)
    if use_hostapd_nat:
        platform_warnings.append("platform_bazzite_use_hostapd_nat")
    fw_cfg = _build_firewalld_cfg(cfg)
    if firewall_backend == "firewalld":
        fw_cfg["firewalld_enabled"] = True
    else:
        fw_cfg["firewalld_enabled"] = False
    state = update_state(
        attempts=[],
        selected_band=None,
        selected_width_mhz=None,
        selected_channel=None,
        selected_country=None,
        pro_mode_allow_fallback_40mhz=allow_fallback_40mhz,
        last_error_detail=None,
    )

    ssid = cfg.get("ssid", "VR-Hotspot")
    passphrase = cfg.get("wpa2_passphrase", "")
    country = cfg.get("country")
    band_pref = cfg.get("band_preference", "5ghz")
    ap_ready_timeout_s = float(cfg.get("ap_ready_timeout_s", 6.0))
    iface_up_grace_s = 0.0
    ap_ready_nohint_retry_s = 0.0
    if platform_is_cachyos:
        iface_up_grace_s = min(6.0, max(2.0, ap_ready_timeout_s / 2.0))
        platform_warnings.append("platform_cachyos_iface_up_grace")
    if platform_is_pop:
        ap_ready_nohint_retry_s = min(12.0, max(6.0, ap_ready_timeout_s / 2.0))
        platform_warnings.append("platform_pop_ap_ready_nohint_retry")
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

    passphrase_override_provided = isinstance(overrides, dict) and "wpa2_passphrase" in overrides
    if not isinstance(passphrase, str) or len(passphrase) < 8:
        # Fresh installs can have an empty passphrase in config. Auto-provision a strong
        # default once (unless caller explicitly provided an override).
        if not passphrase_override_provided:
            try:
                generated_pw = _get_or_create_bootstrap_passphrase()
                write_config_file({"wpa2_passphrase": generated_pw})
                cfg["wpa2_passphrase"] = generated_pw
                passphrase = generated_pw
                platform_warnings.append("auto_generated_passphrase")
                log.warning("auto_generated_passphrase_for_start")
            except Exception as e:
                platform_warnings.append(f"auto_generate_passphrase_failed:{e}")

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

    ap_ifname = None
    nm_remediation_attempted = False
    nm_remediation_error: Optional[str] = None
    prestart_warnings: List[str] = []
    try:
        inv = get_adapters()
        inv_error = inv.get("error")
        if inv_error and not inv.get("adapters"):
            raise RuntimeError(inv_error)

        enforced_channel_width = None
        enforced_channel_5g = None

        preferred = cfg.get("ap_adapter")
        if preferred and isinstance(preferred, str) and preferred.strip():
            ap_ifname = _normalize_ap_adapter(preferred.strip(), inv)
        else:
            # [Added Logic] Prefer USB adapters for 5GHz if available to ensure better performance/AP support

            if bp == "5ghz":
                 for adapter in inv.get("adapters", []):
                     # Check if it's USB and supports AP mode + 5GHz
                     if (adapter.get("bus") == "usb" and 
                         adapter.get("supports_ap") and 
                         adapter.get("supports_5ghz")):
                         preferred_usb = adapter.get("ifname")
                         log.info(f"auto_selected_usb_adapter_for_performance: {preferred_usb}")
                         ap_ifname = preferred_usb
                         break
                 else:
                     ap_ifname = _select_ap_adapter(inv, bp)
            else:
                 ap_ifname = _select_ap_adapter(inv, bp)

        ap_ifname = _normalize_ap_adapter(ap_ifname, inv)

        # Validate band capability if explicitly requested
        a = _get_adapter(inv, ap_ifname)
        if not a or not a.get("supports_ap"):
            raise RuntimeError("no_ap_capable_adapter_found")

        # Pop!_OS + USB adapters are substantially more stable in hostapd_nat
        # no-virt mode (virtual iface naming and busy churn are common otherwise).
        if (
            platform_is_pop
            and bp == "5ghz"
            and (not bridge_mode)
            and a.get("bus") == "usb"
        ):
            if not optimized_no_virt:
                optimized_no_virt = True
                platform_warnings.append("platform_pop_force_no_virt_usb")
            if not use_hostapd_nat:
                use_hostapd_nat = True
                platform_warnings.append("platform_pop_force_hostapd_nat_usb")

        # --- Basic Mode Enforcement ---
        if basic_mode:
            log.info("basic_mode_enforcement_active", extra={"adapter": ap_ifname, "band": bp})
            # (1) Basic Mode requires specified band (policy.BASIC_MODE_REQUIRED_BAND)
            if bp != BASIC_MODE_REQUIRED_BAND:
                raise RuntimeError(ERROR_BASIC_MODE_REQUIRES_5GHZ)
            # (2) Basic Mode requires 80MHz-capable adapter (policy.BASIC_MODE_REQUIRED_WIDTH_MHZ)
            if not a.get("supports_80mhz"):
                raise RuntimeError(ERROR_BASIC_MODE_REQUIRES_80MHZ_ADAPTER)
            # (3) Basic Mode disables fallback - strict fail-fast
            if allow_fallback_40mhz:
                log.info("basic_mode_disabling_fallback_40mhz", extra={"adapter": ap_ifname})
                allow_fallback_40mhz = False

        # --- Pre-start NetworkManager Gate (all modes) ---
        nm_gate_ifname = ap_ifname
        nm_gate_error = _nm_gate_check(nm_gate_ifname)
        if nm_gate_error:
            nm_remediation_attempted = True
            rem_ok, rem_err = _nm_set_unmanaged(nm_gate_ifname)
            nm_remediation_error = rem_err
            if rem_ok:
                nm_gate_error = _nm_gate_check(nm_gate_ifname)
            if nm_gate_error:
                if not nm_remediation_error:
                    nm_remediation_error = "still_managed"
                raise RuntimeError(nm_gate_error)

        prep_warnings = _prepare_ap_interface(
            ap_ifname,
            force_nm_disconnect=platform_is_pop,
        )
        if prep_warnings:
            prestart_warnings.extend(prep_warnings)
            if (
                platform_is_pop
                and (not bridge_mode)
                and (not use_hostapd_nat)
                and "ap_iface_not_up_prestart" in prep_warnings
            ):
                use_hostapd_nat = True
                platform_warnings.append("platform_pop_use_hostapd_nat_on_iface_busy")

        if not _ensure_iface_up(ap_ifname):
            log.warning("ap_iface_not_up_prestart", extra={"ap_interface": ap_ifname})
            old_ifname = ap_ifname
            ap_ifname, inv, a, reselect_warnings = _maybe_reselect_ap_after_prestart_failure(
                ap_ifname=ap_ifname,
                preferred_ifname=preferred if isinstance(preferred, str) else None,
                band_pref=bp,
                inv=inv,
                adapter=a,
                platform_is_pop=platform_is_pop,
                prep_warnings=prep_warnings,
            )
            if reselect_warnings:
                prestart_warnings.extend(reselect_warnings)
            if ap_ifname != old_ifname:
                prep_retry = _prepare_ap_interface(
                    ap_ifname,
                    force_nm_disconnect=platform_is_pop,
                )
                if prep_retry:
                    prestart_warnings.extend(prep_retry)
                    if (
                        platform_is_pop
                        and (not bridge_mode)
                        and (not use_hostapd_nat)
                        and "ap_iface_not_up_prestart" in prep_retry
                    ):
                        use_hostapd_nat = True
                        platform_warnings.append("platform_pop_use_hostapd_nat_on_iface_busy")
                if not _ensure_iface_up(ap_ifname):
                    log.warning("ap_iface_not_up_post_reselect", extra={"ap_interface": ap_ifname})

        if bp == "5ghz":
            if not a.get("supports_80mhz"):
                raise RuntimeError(f"adapter_lacks_80mhz_support_required_for_vr: {ap_ifname}")

        # Enforce 80MHz optimization for USB adapters on 5GHz (whether auto-selected or manual)
        if bp == "5ghz" and a.get("bus") == "usb" and a.get("supports_5ghz"):
             log.info(f"enforcing_80mhz_optimization_on_usb_adapter: {ap_ifname}")
             enforced_channel_width = "80"
             enforced_channel_5g = 36

        if bp == "6ghz" and not a.get("supports_6ghz"):
            raise RuntimeError("selected_adapter_not_6ghz_capable")

        # Apply adapter-specific profile optimizations
        try:
            cfg = apply_adapter_profile(cfg, a)
        except Exception:
            pass  # Best-effort, continue if profile application fails

        target_phy = _get_adapter_phy(inv, ap_ifname)
    except Exception as e:
        err = str(e)
        error_detail = None
        if err in (
            ERROR_BASIC_MODE_REQUIRES_5GHZ,
            ERROR_BASIC_MODE_REQUIRES_80MHZ_ADAPTER,
            ERROR_NM_INTERFACE_MANAGED,
        ):
            context = {"interface": ap_ifname} if err == ERROR_NM_INTERFACE_MANAGED and ap_ifname else {}
            error_detail = wifi_probe.build_error_detail(err, context)
            if err == ERROR_NM_INTERFACE_MANAGED:
                error_detail["remediation_attempted"] = nm_remediation_attempted
                if nm_remediation_error:
                    error_detail["remediation_error"] = nm_remediation_error
        state = update_state(
            phase="error",
            running=False,
            ap_interface=None,
            last_error=err,
            last_error_detail=error_detail,
            last_correlation_id=correlation_id,
            engine={
                "pid": None,
                "cmd": None,
                "started_ts": None,
                "last_exit_code": None,
                "last_error": err,
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
    if prestart_warnings:
        start_warnings.extend(prestart_warnings)
    if platform_warnings:
        start_warnings.extend(platform_warnings)

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
        hostapd_caps = (preflight_result.get("details") or {}).get("hostapd") or {}
        hostapd_he = hostapd_caps.get("he")
    except Exception:
        hostapd_he = None
    if effective_wifi6 and hostapd_he is False:
        effective_wifi6 = False
        start_warnings.append("wifi6_disabled_hostapd_missing_11ax")

    try:
        tuning_state, tuning_warnings = system_tuning.apply_pre(cfg)
    except Exception as e:
        tuning_state = {}
        tuning_warnings = [f"system_tuning_pre_failed:{e}"]
    if tuning_warnings:
        start_warnings.extend(tuning_warnings)

    if bp == "5ghz":
        return _start_hotspot_5ghz_strict(
            cfg=cfg,
            inv=inv,
            ap_ifname=ap_ifname,
            target_phy=target_phy,
            ssid=ssid,
            passphrase=passphrase,
            country=country if isinstance(country, str) else None,
            ap_security=ap_security,
            ap_ready_timeout_s=ap_ready_timeout_s,
            optimized_no_virt=optimized_no_virt,
            debug=debug,
            enable_internet=enable_internet,
            bridge_mode=bridge_mode,
            bridge_name=bridge_name,
            bridge_uplink=bridge_uplink,
            gateway_ip=gateway_ip,
            dhcp_start_ip=dhcp_start_ip,
            dhcp_end_ip=dhcp_end_ip,
            dhcp_dns=dhcp_dns,
            effective_wifi6=effective_wifi6,
            tuning_state=tuning_state,
            start_warnings=start_warnings,
            fw_cfg=fw_cfg,
            firewall_backend=firewall_backend,
            use_hostapd_nat=use_hostapd_nat,
            correlation_id=correlation_id,
            enforced_channel_5g=enforced_channel_5g,
            allow_fallback_40mhz=allow_fallback_40mhz,
            allow_dfs_channels=allow_dfs_channels,
            iface_up_grace_s=iface_up_grace_s,
            ap_ready_nohint_retry_s=ap_ready_nohint_retry_s,
            pop_timeout_retry_no_virt=platform_is_pop,
        )

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
        selected_channel = None
        if bp == "5ghz":
            # Use enforced channel if set, otherwise config
            if enforced_channel_5g is not None:
                selected_channel = enforced_channel_5g
            else:
                val = cfg.get("channel_5g")
                if val is not None:
                    try:
                        selected_channel = int(val)
                    except Exception:
                        pass

        channel_auto_select = bool(cfg.get("channel_auto_select", False))
        # If auto-select is ON and no manual channel is set (or set to 0), scan for best.
        if channel_auto_select and (selected_channel is None or selected_channel == 0):
            try:
                best_channel = select_best_channel(ap_ifname, bp, None)
                if best_channel:
                    selected_channel = best_channel
                    # If we auto-picked 5GHz, should we persist it?
                    # 6GHz logic persists it. Let's persist it for consistency if it was a 5GHz pick.
                    if bp == "5ghz":
                        write_config_file({"channel_5g": best_channel})
            except Exception:
                pass  # Best-effort

        channel_width = str(cfg.get("channel_width", "auto")).lower()
        if enforced_channel_width:
             channel_width = enforced_channel_width
        beacon_interval = int(cfg.get("beacon_interval", 50))
        dtim_period = int(cfg.get("dtim_period", 1))
        short_guard_interval = bool(cfg.get("short_guard_interval", True))
        tx_power = cfg.get("tx_power")
        if tx_power is not None:
            try:
                tx_power = int(tx_power)
            except Exception:
                tx_power = None

        if use_hostapd_nat:
            strict_width = bp == "5ghz" and str(channel_width) in ("auto", "80", "160")
            cmd1 = build_cmd_nat(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band=bp,
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=selected_channel,
                no_virt=optimized_no_virt,
                debug=debug,
                wifi6=effective_wifi6,
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
                strict_width=strict_width,
            )
        else:
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

    expected_ap_ifname = None
    if use_hostapd_nat:
        expected_ap_ifname = ap_ifname if optimized_no_virt else _virt_ap_ifname(ap_ifname)
    else:
        expected_ap_ifname = _lnxrouter_expected_ifname(ap_ifname, no_virt=optimized_no_virt)

    ap_info = None
    start_failure_reason = None
    latest_stdout = res.stdout_tail
    latest_stderr = res.stderr_tail
    if not res.ok:
        start_failure_reason = res.error or "engine_start_failed"
    else:
        ap_info = _wait_for_ap_ready(
            target_phy,
            ap_ready_timeout_s,
            ssid=ssid,
            adapter_ifname=ap_ifname,
            expected_ap_ifname=expected_ap_ifname,
        )
        if not ap_info:
            start_failure_reason = "ap_ready_timeout"
            try:
                latest_stdout, latest_stderr = get_tails()
            except Exception:
                latest_stdout = res.stdout_tail
                latest_stderr = res.stderr_tail
            if latest_stdout or latest_stderr:
                update_state(engine={"stdout_tail": latest_stdout, "stderr_tail": latest_stderr})

    if ap_info:
        detected_band = _band_from_freq_mhz(ap_info.freq_mhz) or bp
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
                firewall_backend=firewall_backend,
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
            channel_width_mhz=ap_info.channel_width_mhz,
            selected_band=detected_band,
            selected_width_mhz=ap_info.channel_width_mhz,
            selected_channel=ap_info.channel,
            selected_country=country if isinstance(country, str) else None,
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

    driver_error = _stdout_has_hostapd_driver_error(latest_stdout or [])
    if optimized_no_virt and driver_error and (not bridge_mode) and bp in ("2.4ghz", "5ghz"):
        warnings.append("optimized_no_virt_retry_with_virt")
        retry_channel_width = str(cfg.get("channel_width", "auto")).lower()
        retry_beacon_interval = int(cfg.get("beacon_interval", 50))
        retry_dtim_period = int(cfg.get("dtim_period", 1))
        retry_short_guard_interval = bool(cfg.get("short_guard_interval", True))
        retry_tx_power = cfg.get("tx_power")
        if retry_tx_power is not None:
            try:
                retry_tx_power = int(retry_tx_power)
            except Exception:
                retry_tx_power = None

        if use_hostapd_nat:
            strict_width = bp == "5ghz" and str(retry_channel_width) in ("auto", "80", "160")
            cmd_retry = build_cmd_nat(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band=bp,
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=selected_channel,
                no_virt=False,
                debug=debug,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_start_ip=dhcp_start_ip,
                dhcp_end_ip=dhcp_end_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
                channel_width=retry_channel_width,
                beacon_interval=retry_beacon_interval,
                dtim_period=retry_dtim_period,
                short_guard_interval=retry_short_guard_interval,
                tx_power=retry_tx_power,
                strict_width=strict_width,
            )
        else:
            cmd_retry = build_cmd(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band_preference=bp,
                country=country if isinstance(country, str) else None,
                channel=selected_channel,
                no_virt=False,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
            )

        res_retry = start_engine(cmd_retry, firewalld_cfg=fw_cfg)
        update_state(
            adapter=ap_ifname,
            engine={
                "pid": res_retry.pid,
                "cmd": res_retry.cmd,
                "started_ts": res_retry.started_ts,
                "last_exit_code": res_retry.exit_code,
                "last_error": res_retry.error,
                "stdout_tail": res_retry.stdout_tail,
                "stderr_tail": res_retry.stderr_tail,
                "ap_logs_tail": [],
            },
        )

        ap_info_retry = None
        if res_retry.ok:
            retry_expected_ifname = _virt_ap_ifname(ap_ifname) if use_hostapd_nat else _lnxrouter_expected_ifname(ap_ifname, no_virt=False)
            ap_info_retry = _wait_for_ap_ready(
                target_phy,
                ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=retry_expected_ifname,
            )

        if ap_info_retry:
            detected_band = _band_from_freq_mhz(ap_info_retry.freq_mhz) or bp
            affinity_pids = _collect_affinity_pids(
                adapter_ifname=ap_ifname,
                ap_interface=ap_info_retry.ifname,
                engine_pid=res_retry.pid,
            )
            try:
                tuning_state, runtime_warnings = system_tuning.apply_runtime(
                    tuning_state,
                    cfg,
                    ap_ifname=ap_info_retry.ifname,
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
                    ap_ifname=ap_info_retry.ifname,
                    enable_internet=enable_internet,
                    firewalld_cfg=fw_cfg,
                    firewall_backend=firewall_backend,
                )
            except Exception as e:
                net_state = {}
                net_warnings = [f"network_tuning_apply_failed:{e}"]
            if net_warnings:
                warnings.extend(net_warnings)
            state = update_state(
                phase="running",
                running=True,
                ap_interface=ap_info_retry.ifname,
                band=detected_band,
                channel_width_mhz=ap_info_retry.channel_width_mhz,
                selected_band=detected_band,
                selected_width_mhz=ap_info_retry.channel_width_mhz,
                selected_channel=ap_info_retry.channel,
                selected_country=country if isinstance(country, str) else None,
                mode="fallback",
                fallback_reason="no_virt_retry",
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

        warnings.append("optimized_no_virt_retry_failed")
        try:
            ap_candidate = _select_ap_from_iw(_iw_dev_dump(), target_phy=target_phy, ssid=ssid)
        except Exception:
            ap_candidate = None
        ap_logs = _collect_ap_logs(ap_ifname, ap_candidate.ifname if ap_candidate else None)
        if ap_logs:
            update_state(engine={"ap_logs_tail": ap_logs})
        _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
        _remove_conf_dirs(ap_ifname)
    elif (not optimized_no_virt) and driver_error and (not bridge_mode) and bp in ("2.4ghz", "5ghz"):
        warnings.append("optimized_virt_retry_with_no_virt")
        retry_channel_width = str(cfg.get("channel_width", "auto")).lower()
        retry_beacon_interval = int(cfg.get("beacon_interval", 50))
        retry_dtim_period = int(cfg.get("dtim_period", 1))
        retry_short_guard_interval = bool(cfg.get("short_guard_interval", True))
        retry_tx_power = cfg.get("tx_power")
        if retry_tx_power is not None:
            try:
                retry_tx_power = int(retry_tx_power)
            except Exception:
                retry_tx_power = None

        if use_hostapd_nat:
            strict_width = bp == "5ghz" and str(retry_channel_width) in ("auto", "80", "160")
            cmd_retry = build_cmd_nat(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band=bp,
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=selected_channel,
                no_virt=True,
                debug=debug,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_start_ip=dhcp_start_ip,
                dhcp_end_ip=dhcp_end_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
                channel_width=retry_channel_width,
                beacon_interval=retry_beacon_interval,
                dtim_period=retry_dtim_period,
                short_guard_interval=retry_short_guard_interval,
                tx_power=retry_tx_power,
                strict_width=strict_width,
            )
        else:
            cmd_retry = build_cmd(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band_preference=bp,
                country=country if isinstance(country, str) else None,
                channel=selected_channel,
                no_virt=True,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
            )

        res_retry = start_engine(cmd_retry, firewalld_cfg=fw_cfg)
        update_state(
            adapter=ap_ifname,
            engine={
                "pid": res_retry.pid,
                "cmd": res_retry.cmd,
                "started_ts": res_retry.started_ts,
                "last_exit_code": res_retry.exit_code,
                "last_error": res_retry.error,
                "stdout_tail": res_retry.stdout_tail,
                "stderr_tail": res_retry.stderr_tail,
                "ap_logs_tail": [],
            },
        )

        ap_info_retry = None
        if res_retry.ok:
            retry_expected_ifname = ap_ifname if use_hostapd_nat else _lnxrouter_expected_ifname(ap_ifname, no_virt=True)
            ap_info_retry = _wait_for_ap_ready(
                target_phy,
                ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=retry_expected_ifname,
            )

        if ap_info_retry:
            detected_band = _band_from_freq_mhz(ap_info_retry.freq_mhz) or bp
            affinity_pids = _collect_affinity_pids(
                adapter_ifname=ap_ifname,
                ap_interface=ap_info_retry.ifname,
                engine_pid=res_retry.pid,
            )
            try:
                tuning_state, runtime_warnings = system_tuning.apply_runtime(
                    tuning_state,
                    cfg,
                    ap_ifname=ap_info_retry.ifname,
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
                    ap_ifname=ap_info_retry.ifname,
                    enable_internet=enable_internet,
                    firewalld_cfg=fw_cfg,
                    firewall_backend=firewall_backend,
                )
            except Exception as e:
                net_state = {}
                net_warnings = [f"network_tuning_apply_failed:{e}"]
            if net_warnings:
                warnings.extend(net_warnings)
            state = update_state(
                phase="running",
                running=True,
                ap_interface=ap_info_retry.ifname,
                band=detected_band,
                channel_width_mhz=ap_info_retry.channel_width_mhz,
                selected_band=detected_band,
                selected_width_mhz=ap_info_retry.channel_width_mhz,
                selected_channel=ap_info_retry.channel,
                selected_country=country if isinstance(country, str) else None,
                mode="fallback",
                fallback_reason="virt_retry_no_virt",
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

        warnings.append("optimized_virt_retry_failed")
        try:
            ap_candidate = _select_ap_from_iw(_iw_dev_dump(), target_phy=target_phy, ssid=ssid)
        except Exception:
            ap_candidate = None
        ap_logs = _collect_ap_logs(ap_ifname, ap_candidate.ifname if ap_candidate else None)
        if ap_logs:
            update_state(engine={"ap_logs_tail": ap_logs})
        _kill_runtime_processes(ap_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
        _remove_conf_dirs(ap_ifname)
    fallback_no_virt = optimized_no_virt
    if optimized_no_virt and driver_error:
        fallback_no_virt = False
        warnings.append("optimized_no_virt_disabled_on_driver_error")
    elif (not optimized_no_virt) and driver_error:
        fallback_no_virt = True
        warnings.append("optimized_virt_disabled_on_driver_error")

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
            ("5ghz", None, fallback_no_virt, "fallback_to_5ghz"),
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), fallback_no_virt, "fallback_to_2_4ghz"),
        ]
    elif bp == "5ghz":
        fallback_chain = [
            ("2.4ghz", int(cfg.get("fallback_channel_2g", 6)), fallback_no_virt, "fallback_to_2_4ghz"),
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

    fallback_channel_width = str(cfg.get("channel_width", "auto")).lower()
    fallback_beacon_interval = int(cfg.get("beacon_interval", 50))
    fallback_dtim_period = int(cfg.get("dtim_period", 1))
    fallback_short_guard_interval = bool(cfg.get("short_guard_interval", True))
    fallback_tx_power = cfg.get("tx_power")
    if fallback_tx_power is not None:
        try:
            fallback_tx_power = int(fallback_tx_power)
        except Exception:
            fallback_tx_power = None

    for band, channel, no_virt, warning_tag in fallback_chain:
        warnings.append(warning_tag)

        if use_hostapd_nat:
            strict_width = band == "5ghz" and str(fallback_channel_width) in ("auto", "80", "160")
            cmd_fallback = build_cmd_nat(
                ap_ifname=ap_ifname,
                ssid=ssid,
                passphrase=passphrase,
                band=band,
                ap_security=ap_security,
                country=country if isinstance(country, str) else None,
                channel=channel,
                no_virt=no_virt,
                debug=debug,
                wifi6=effective_wifi6,
                gateway_ip=gateway_ip,
                dhcp_start_ip=dhcp_start_ip,
                dhcp_end_ip=dhcp_end_ip,
                dhcp_dns=dhcp_dns,
                enable_internet=enable_internet,
                channel_width=fallback_channel_width,
                beacon_interval=fallback_beacon_interval,
                dtim_period=fallback_dtim_period,
                short_guard_interval=fallback_short_guard_interval,
                tx_power=fallback_tx_power,
                strict_width=strict_width,
            )
        else:
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
            fallback_expected_ifname = None
            if use_hostapd_nat:
                fallback_expected_ifname = ap_ifname if no_virt else _virt_ap_ifname(ap_ifname)
            else:
                fallback_expected_ifname = _lnxrouter_expected_ifname(ap_ifname, no_virt=no_virt)
            ap_info_fallback = _wait_for_ap_ready(
                target_phy,
                ap_ready_timeout_s,
                ssid=ssid,
                adapter_ifname=ap_ifname,
                expected_ap_ifname=fallback_expected_ifname,
            )

        if ap_info_fallback:
            detected_band = _band_from_freq_mhz(ap_info_fallback.freq_mhz) or band
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
                    firewall_backend=firewall_backend,
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
                channel_width_mhz=ap_info_fallback.channel_width_mhz,
                selected_band=detected_band,
                selected_width_mhz=ap_info_fallback.channel_width_mhz,
                selected_channel=ap_info_fallback.channel,
                selected_country=country if isinstance(country, str) else None,
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

    cfg = load_config()
    fw_cfg = _build_firewalld_cfg(cfg)
    adapter_ifname = state.get("adapter") if isinstance(state, dict) else None

    runtime_present = False
    try:
        runtime_present = bool(
            is_running()
            or _find_our_lnxrouter_pids()
            or _find_hostapd_pids(adapter_ifname)
            or _find_dnsmasq_pids(adapter_ifname)
        )
    except Exception:
        runtime_present = bool(is_running())

    if state["phase"] == "stopped" and not runtime_present:
        return LifecycleResult("already_stopped", state)

    state = update_state(
        phase="stopping",
        last_op="stop",
        last_correlation_id=correlation_id,
        last_error=None,
    )

    ok, rc, out_tail, err_tail, err = stop_engine(firewalld_cfg=fw_cfg)

    # Always run a second-pass teardown in case engine children or orphan helpers remain.
    _kill_runtime_processes(adapter_ifname, firewalld_cfg=fw_cfg, stop_engine_first=True)
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


def collect_capture_logs(
    capture_dir: Optional[str],
    lnxrouter_config_dir: Optional[str],
    max_lines: int = 50,
) -> List[str]:
    """
    Collect diagnostic logs from capture directory and lnxrouter config directory.
    Returns a list of log lines (most recent lines from various log files).
    """
    lines = []
    
    # Collect from capture directory
    if capture_dir and os.path.isdir(capture_dir):
        try:
            for filename in sorted(os.listdir(capture_dir)):
                if filename.endswith('.log') or filename.endswith('.txt'):
                    filepath = os.path.join(capture_dir, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            file_lines = f.readlines()
                            lines.append(f"=== {filename} ===")
                            lines.extend([f"[{filename}] {line.rstrip()}" for line in file_lines[-max_lines:]])
                    except Exception:
                        pass
        except Exception:
            pass
    
    # Strategy for finding lnxrouter logs:
    # 1. If lnxrouter_config_dir provided, check it.
    # 2. If provided but not found, check if it exists inside capture_dir/lnxrouter_tmp (mapping).
    # 3. If NOT provided, find the newest in capture_dir/lnxrouter_tmp.
    
    target_dirs = []
    
    if lnxrouter_config_dir:
        # 1. Direct path
        if os.path.isdir(lnxrouter_config_dir):
            target_dirs.append(lnxrouter_config_dir)
        elif capture_dir and os.path.isdir(capture_dir):
            # 2. Mapped path
            name = os.path.basename(lnxrouter_config_dir.rstrip('/'))
            mapped = os.path.join(capture_dir, "lnxrouter_tmp", name)
            if os.path.isdir(mapped):
                target_dirs.append(mapped)

    if not target_dirs and capture_dir and os.path.isdir(capture_dir):
        # 3. Automatic newest
        captured_conf_root = os.path.join(capture_dir, "lnxrouter_tmp")
        if os.path.isdir(captured_conf_root):
            try:
                conf_dirs = []
                for d in os.listdir(captured_conf_root):
                    path = os.path.join(captured_conf_root, d)
                    if os.path.isdir(path):
                        conf_dirs.append(path)
                
                if conf_dirs:
                    conf_dirs.sort(key=lambda p: os.path.getmtime(p))
                    target_dirs.append(conf_dirs[-1])
            except Exception:
                pass

    # Collect from all identified targets (usually just one)
    for conf_dir in target_dirs:
        try:
            for filename in ['hostapd.log', 'dnsmasq.log', 'hostapd.conf']:
                filepath = os.path.join(conf_dir, filename)
                if os.path.isfile(filepath):
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            file_lines = f.readlines()
                            lines.append(f"=== {filename} ===")
                            lines.extend([f"[{filename}] {line.rstrip()}" for line in file_lines[-max_lines:]])
                    except Exception:
                        pass
        except Exception:
            pass
    
    return lines
