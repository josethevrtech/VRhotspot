import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from . import firewalld  # SteamOS: firewalld owns nftables
from vr_hotspotd import os_release
from vr_hotspotd.vendor_paths import resolve_vendor_required, vendor_bin_dirs, vendor_lib_dirs

ENGINE_STDOUT_MAX_LINES = 200
ENGINE_STDERR_MAX_LINES = 200

_ln_proc: Optional[subprocess.Popen] = None
_stdout_tail: Deque[str] = deque(maxlen=ENGINE_STDOUT_MAX_LINES)
_stderr_tail: Deque[str] = deque(maxlen=ENGINE_STDERR_MAX_LINES)
_stdout_head: List[str] = []
_stderr_head: List[str] = []
_stdout_line_count = 0
_stderr_line_count = 0

_last_ap_ifname: Optional[str] = None
_last_firewalld_cfg: Dict[str, object] = {}
_stdout_line_observer: Optional[Callable[[str], None]] = None

_HOSTAPD_UNKNOWN_RE = re.compile(r"unknown configuration item '([^']+)'", re.IGNORECASE)


class VendorSelectionError(RuntimeError):
    def __init__(self, payload: Dict[str, object]) -> None:
        super().__init__("vendor_selection_failed")
        self.payload = payload

    def to_payload(self) -> Dict[str, object]:
        return dict(self.payload)


def _hostapd_probe_config() -> str:
    return "\n".join(
        [
            "ssid=vrhs-probe",
            "hw_mode=a",
            "channel=36",
            "ieee80211n=1",
            "secondary_channel=1",
            "ieee80211ac=1",
            "vht_oper_chwidth=1",
            "vht_oper_centr_freq_seg0_idx=42",
            "",
        ]
    )


def _hostapd_supports_ht_vht(
    hostapd_path: Optional[str],
    *,
    vendor_lib: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    if not hostapd_path:
        return None
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    if vendor_lib and os.path.isdir(vendor_lib):
        ld = env.get("LD_LIBRARY_PATH", "")
        if vendor_lib not in ld.split(":"):
            env["LD_LIBRARY_PATH"] = f"{vendor_lib}:{ld}" if ld else vendor_lib
    conf_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(_hostapd_probe_config())
            conf_path = f.name
        p = subprocess.run(
            [hostapd_path, "-t", conf_path],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=env,
        )
    except Exception as e:
        return {"supports_ht": False, "supports_vht": False, "unknown": [], "rc": None, "error": str(e)}
    finally:
        if conf_path:
            try:
                os.unlink(conf_path)
            except Exception:
                pass

    # Even on non-zero rc, parse stdout/stderr for unknown config items.
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    unknown = _HOSTAPD_UNKNOWN_RE.findall(out)
    unknown_set = {u.strip() for u in unknown}
    supports_ht = "secondary_channel" not in unknown_set
    supports_vht = not bool(
        {"ieee80211ac", "vht_oper_chwidth", "vht_oper_centr_freq_seg0_idx"} & unknown_set
    )
    return {
        "supports_ht": supports_ht,
        "supports_vht": supports_vht,
        "unknown": sorted(unknown_set),
        "rc": p.returncode,
    }


def _note(msg: str) -> None:
    # Keep supervisor notes in stderr tail so they show up for diagnostics.
    _stderr_tail.append(f"[supervisor] {msg}")


def _reader_thread(stream, tail: Deque[str], label: str) -> None:
    global _stdout_line_count, _stderr_line_count
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            clean = line.rstrip("\n")
            tail.append(clean)
            if label == "stdout":
                if len(_stdout_head) < ENGINE_STDOUT_MAX_LINES:
                    _stdout_head.append(clean)
                _stdout_line_count += 1
                observer = _stdout_line_observer
                if observer:
                    try:
                        observer(clean)
                    except Exception as e:
                        _note(f"stdout observer error: {e}")
            else:
                if len(_stderr_head) < ENGINE_STDERR_MAX_LINES:
                    _stderr_head.append(clean)
                _stderr_line_count += 1
    except Exception:
        tail.append(f"[{label}] reader error")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def set_stdout_observer(observer: Optional[Callable[[str], None]]) -> None:
    """
    Register a line observer for engine stdout (used for capture/discovery).
    """
    global _stdout_line_observer
    _stdout_line_observer = observer


@dataclass
class EngineStartResult:
    ok: bool
    pid: Optional[int]
    exit_code: Optional[int]
    stdout_tail: List[str]
    stderr_tail: List[str]
    error: Optional[str]
    cmd: List[str]
    started_ts: Optional[int]


def is_running() -> bool:
    global _ln_proc
    return _ln_proc is not None and _ln_proc.poll() is None


def get_tails() -> Tuple[List[str], List[str]]:
    return list(_stdout_tail), list(_stderr_tail)


def _merge_head_tail(
    head: List[str],
    tail: Deque[str],
    count: int,
    max_lines: int,
) -> List[str]:
    tail_list = list(tail)
    head_list = list(head)
    if count <= 0:
        # Count only tracks process stream lines; supervisor notes can still exist in tail.
        return tail_list or head_list
    if count <= max_lines:
        return tail_list or head_list
    if not head_list:
        return tail_list
    if not tail_list:
        return head_list
    overlap = (max_lines * 2) - count
    if overlap > 0:
        tail_list = tail_list[overlap:] if overlap < len(tail_list) else []
    if not tail_list:
        return head_list
    return head_list + ["..."] + tail_list


def _collect_failure_output() -> Tuple[List[str], List[str]]:
    return (
        _merge_head_tail(_stdout_head, _stdout_tail, _stdout_line_count, ENGINE_STDOUT_MAX_LINES),
        _merge_head_tail(_stderr_head, _stderr_tail, _stderr_line_count, ENGINE_STDERR_MAX_LINES),
    )


def _which_in_path(exe: str, path: str) -> Optional[str]:
    for d in path.split(":"):
        cand = os.path.join(d, exe)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _split_tokens(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.replace(",", " ").split() if item.strip()]


def _prefer_vendor_for_platform() -> bool:
    info = os_release.read_os_release()
    tokens: List[str] = []
    for key in ("id", "id_like", "variant_id", "variant", "name"):
        tokens.extend(_split_tokens(info.get(key)))
    return "cachyos" in tokens


def _build_engine_env() -> Dict[str, str]:
    """
    Environment for lnxrouter execution.

    Behavior:
      - Prefer OS-specific vendor bundles when present (e.g., vendor/bin/bazzite).
      - Otherwise prefer system hostapd + dnsmasq when available.
    """
    env = os.environ.copy()
    vendor_bins = vendor_bin_dirs()
    vendor_resolved, vendor_lib_dir, vendor_profile, vendor_missing = resolve_vendor_required(
        ["hostapd", "dnsmasq"]
    )
    vendor_hostapd = vendor_resolved.get("hostapd")
    vendor_dnsmasq = vendor_resolved.get("dnsmasq")
    sys_path = "/usr/sbin:/usr/bin:/sbin:/bin"

    sys_hostapd = _which_in_path("hostapd", sys_path)
    sys_dnsmasq = _which_in_path("dnsmasq", sys_path)

    force_vendor = env.get("VR_HOTSPOT_FORCE_VENDOR_BIN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    force_system = env.get("VR_HOTSPOT_FORCE_SYSTEM_BIN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    strict_vendor = env.get("VR_HOTSPOT_VENDOR_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    vendor_hostapd_ok = bool(vendor_hostapd)
    vendor_dnsmasq_ok = bool(vendor_dnsmasq)
    force_vendor_effective = force_vendor or strict_vendor

    prefer_vendor_platform = _prefer_vendor_for_platform()
    prefer_vendor = False
    if force_system:
        prefer_vendor = False
    elif force_vendor_effective:
        prefer_vendor = True
    else:
        # Prefer OS-specific vendor bundles when present (e.g., bazzite/hostapd).
        prefer_vendor = bool(vendor_profile) or prefer_vendor_platform

    vendor_bin_path = ":".join(str(p) for p in vendor_bins if p)
    if prefer_vendor and (vendor_hostapd_ok or vendor_dnsmasq_ok):
        env["PATH"] = f"{vendor_bin_path}:{sys_path}" if vendor_bin_path else sys_path
    else:
        env["PATH"] = f"{sys_path}:{vendor_bin_path}" if vendor_bin_path else sys_path

    vendor_libs = vendor_lib_dirs(preferred_profile=vendor_profile)
    vendor_lib_path = ""
    if vendor_libs:
        ld_path = env.get("LD_LIBRARY_PATH", "")
        ordered = []
        seen = set()
        for p in vendor_libs:
            s = str(p)
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
        vendor_lib_path = ":".join(ordered)
        env["LD_LIBRARY_PATH"] = f"{vendor_lib_path}:{ld_path}" if ld_path else vendor_lib_path

    chosen_hostapd: Optional[str] = None
    chosen_dnsmasq: Optional[str] = None
    chosen_lib_dir: Optional[str] = None

    if force_system:
        if not sys_hostapd or not sys_dnsmasq:
            selection_result = {
                "vendor_profile": vendor_profile,
                "force_vendor": force_vendor_effective,
                "force_system": force_system,
                "vendor_hostapd": vendor_hostapd,
                "vendor_dnsmasq": vendor_dnsmasq,
                "sys_hostapd": sys_hostapd,
                "sys_dnsmasq": sys_dnsmasq,
                "chosen_hostapd": None,
                "chosen_dnsmasq": None,
                "vendor_lib_dirs": [str(p) for p in vendor_libs],
                "chosen_lib_dir": None,
            }
            missing = [name for name, path in (("hostapd", sys_hostapd), ("dnsmasq", sys_dnsmasq)) if not path]
            raise VendorSelectionError(
                {
                    "error": "force_system_missing",
                    "missing": missing,
                    "selection": selection_result,
                }
            )
        chosen_hostapd = sys_hostapd
        chosen_dnsmasq = sys_dnsmasq
    elif force_vendor_effective:
        if not vendor_hostapd_ok or not vendor_dnsmasq_ok:
            selection_result = {
                "vendor_profile": vendor_profile,
                "force_vendor": force_vendor_effective,
                "force_system": force_system,
                "vendor_hostapd": vendor_hostapd,
                "vendor_dnsmasq": vendor_dnsmasq,
                "sys_hostapd": sys_hostapd,
                "sys_dnsmasq": sys_dnsmasq,
                "chosen_hostapd": None,
                "chosen_dnsmasq": None,
                "vendor_lib_dirs": [str(p) for p in vendor_libs],
                "chosen_lib_dir": None,
            }
            missing = vendor_missing or [
                name for name, path in (("hostapd", vendor_hostapd), ("dnsmasq", vendor_dnsmasq)) if not path
            ]
            raise VendorSelectionError(
                {
                    "error": "force_vendor_missing",
                    "missing": missing,
                    "selection": selection_result,
                }
            )
        chosen_hostapd = vendor_hostapd
        chosen_dnsmasq = vendor_dnsmasq
        chosen_lib_dir = str(vendor_lib_dir) if vendor_lib_dir else None
    else:
        if prefer_vendor:
            chosen_hostapd = vendor_hostapd if vendor_hostapd_ok else sys_hostapd
            chosen_dnsmasq = vendor_dnsmasq if vendor_dnsmasq_ok else sys_dnsmasq
        else:
            chosen_hostapd = sys_hostapd or (vendor_hostapd if vendor_hostapd_ok else None)
            chosen_dnsmasq = sys_dnsmasq or (vendor_dnsmasq if vendor_dnsmasq_ok else None)

        sys_probe = _hostapd_supports_ht_vht(sys_hostapd)
        vendor_probe = _hostapd_supports_ht_vht(
            vendor_hostapd if vendor_hostapd_ok else None,
            vendor_lib=str(vendor_lib_dir) if vendor_lib_dir else None,
        )

        if sys_probe:
            _note(
                "hostapd_probe sys ht="
                f"{sys_probe.get('supports_ht')} vht={sys_probe.get('supports_vht')} "
                f"unknown={sys_probe.get('unknown')}"
            )
        if vendor_probe:
            _note(
                "hostapd_probe vendor ht="
                f"{vendor_probe.get('supports_ht')} vht={vendor_probe.get('supports_vht')} "
                f"unknown={vendor_probe.get('unknown')}"
            )

    def _supports_vht(p: Optional[Dict[str, object]]) -> bool:
        return bool(p and p.get("supports_vht"))

    def _supports_ht(p: Optional[Dict[str, object]]) -> bool:
        return bool(p and p.get("supports_ht"))

    if not force_system and not force_vendor_effective:
        if _supports_vht(vendor_probe) and not _supports_vht(sys_probe) and vendor_hostapd_ok:
            chosen_hostapd = vendor_hostapd
            _note("hostapd_select vendor (vht_supported)")
        elif _supports_vht(sys_probe) and not _supports_vht(vendor_probe) and sys_hostapd:
            chosen_hostapd = sys_hostapd
            _note("hostapd_select system (vht_supported)")
        elif _supports_ht(vendor_probe) and not _supports_ht(sys_probe) and vendor_hostapd_ok:
            chosen_hostapd = vendor_hostapd
            _note("hostapd_select vendor (ht_supported)")
        elif _supports_ht(sys_probe) and not _supports_ht(vendor_probe) and sys_hostapd:
            chosen_hostapd = sys_hostapd
            _note("hostapd_select system (ht_supported)")

        if chosen_hostapd == vendor_hostapd and vendor_lib_dir:
            chosen_lib_dir = str(vendor_lib_dir)

    if vendor_missing:
        _note(f"vendor_missing_required {','.join(vendor_missing)}")

    selection_result = {
        "vendor_profile": vendor_profile,
        "prefer_vendor_platform": prefer_vendor_platform,
        "force_vendor": force_vendor_effective,
        "force_system": force_system,
        "vendor_hostapd": vendor_hostapd,
        "vendor_dnsmasq": vendor_dnsmasq,
        "sys_hostapd": sys_hostapd,
        "sys_dnsmasq": sys_dnsmasq,
        "chosen_hostapd": chosen_hostapd,
        "chosen_dnsmasq": chosen_dnsmasq,
        "vendor_lib_dirs": [str(p) for p in vendor_libs],
        "chosen_lib_dir": chosen_lib_dir,
    }

    _note(
        "selection_result "
        f"vendor_profile={vendor_profile or 'none'} "
        f"force_vendor={'1' if force_vendor_effective else '0'} "
        f"force_system={'1' if force_system else '0'} "
        f"hostapd_select={chosen_hostapd or 'none'} "
        f"dnsmasq_select={chosen_dnsmasq or 'none'} "
        f"LD_LIBRARY_PATH_prefix={vendor_lib_path or 'none'}"
    )

    if chosen_hostapd:
        env["HOSTAPD"] = chosen_hostapd
    else:
        env.pop("HOSTAPD", None)

    if chosen_dnsmasq:
        env["DNSMASQ"] = chosen_dnsmasq
    else:
        env.pop("DNSMASQ", None)

    env.setdefault("LC_ALL", "C")
    env.setdefault("LANG", "C")
    return env


def _extract_ap_ifname(cmd: List[str]) -> Optional[str]:
    """
    lnxrouter contract:
      --ap <iface> <SSID>
    """
    try:
        i = cmd.index("--ap")
        return cmd[i + 1]
    except Exception:
        pass

    # hostapd6_engine contract:
    #   --ap-ifname <iface> [--no-virt]
    try:
        i = cmd.index("--ap-ifname")
        base = cmd[i + 1]
    except Exception:
        return None

    if not base:
        return None
    if "--no-virt" in cmd:
        return base

    # Mirrors hostapd6_engine virtual name behavior (x0 + base, max 15 chars).
    cand = f"x0{base}"
    return cand[:15]


def _apply_firewalld(ap_ifname: str, cfg: Dict[str, object]) -> bool:
    """
    Apply firewalld runtime policy for the AP interface.
    Must use firewall-cmd (NOT raw nft) on SteamOS.
    """
    enabled = bool(cfg.get("firewalld_enabled", True))
    if not enabled:
        _note("firewalld integration disabled by config")
        return True

    if not firewalld.is_running():
        _note("firewalld not running; skipping firewall configuration")
        return True

    zone = str(cfg.get("firewalld_zone", "trusted"))

    add_ok, out = firewalld.add_interface(zone, ap_ifname)
    _note(f"firewalld add-interface zone={zone} if={ap_ifname} ok={add_ok} out={out}")

    if bool(cfg.get("firewalld_enable_masquerade", True)):
        ok, out = firewalld.enable_masquerade(zone)
        _note(f"firewalld add-masquerade zone={zone} ok={ok} out={out}")

    if bool(cfg.get("firewalld_enable_forward", True)):
        ok, out = firewalld.enable_forward(zone)
        _note(f"firewalld add-forward zone={zone} ok={ok} out={out}")
    return add_ok


def _retry_firewalld(ap_ifname: str, cfg: Dict[str, object], attempts: int = 5, delay_s: float = 0.4) -> None:
    for _ in range(attempts):
        time.sleep(delay_s)
        if _apply_firewalld(ap_ifname, cfg):
            return


def _cleanup_firewalld(ap_ifname: str, cfg: Dict[str, object]) -> None:
    enabled = bool(cfg.get("firewalld_enabled", True))
    if not enabled:
        return
    if not bool(cfg.get("firewalld_cleanup_on_stop", True)):
        return
    if not firewalld.is_running():
        return

    zone = str(cfg.get("firewalld_zone", "trusted"))
    ok, out = firewalld.remove_interface(zone, ap_ifname)
    _note(f"firewalld remove-interface zone={zone} if={ap_ifname} ok={ok} out={out}")


def _kill_process_group(pid: int, sig: int) -> None:
    """
    Kill the entire process group for a PID, with fallback to killing just the PID.
    lnxrouter spawns helpers; PGID kill prevents orphans.
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return

    try:
        os.killpg(pgid, sig)
        return
    except ProcessLookupError:
        return
    except PermissionError:
        pass

    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return


def _redact_cmd(cmd: List[str]) -> List[str]:
    """
    Prevent secrets leaking into /v1/status:
    - Replace the value after -p or --passphrase with ********.
    """
    out = list(cmd)
    for flag in ("-p", "--passphrase"):
        try:
            i = out.index(flag)
            if i + 1 < len(out):
                out[i + 1] = "********"
        except ValueError:
            pass
    return out


def start_engine(
    cmd: List[str],
    early_fail_window_s: float = 1.0,
    firewalld_cfg: Optional[Dict[str, object]] = None,
) -> EngineStartResult:
    global _ln_proc, _stdout_tail, _stderr_tail, _last_ap_ifname, _last_firewalld_cfg
    global _stdout_line_count, _stderr_line_count

    if firewalld_cfg is None:
        firewalld_cfg = {}
    _last_firewalld_cfg = dict(firewalld_cfg)

    # If already running, do not restart here; just report.
    if is_running():
        return EngineStartResult(
            ok=True,
            pid=_ln_proc.pid if _ln_proc else None,
            exit_code=None,
            stdout_tail=list(_stdout_tail),
            stderr_tail=list(_stderr_tail),
            error=None,
            cmd=_redact_cmd(cmd),
            started_ts=int(time.time()),
        )

    _stdout_tail.clear()
    _stderr_tail.clear()
    _stdout_head.clear()
    _stderr_head.clear()
    _stdout_line_count = 0
    _stderr_line_count = 0

    started_ts = int(time.time())

    ap_ifname = _extract_ap_ifname(cmd)
    if ap_ifname:
        _last_ap_ifname = ap_ifname
        applied = _apply_firewalld(ap_ifname, firewalld_cfg)
        if not applied:
            _note("firewalld add-interface failed; will retry")
            threading.Thread(
                target=_retry_firewalld,
                args=(ap_ifname, firewalld_cfg),
                daemon=True,
            ).start()
    else:
        _note("could not extract AP ifname from cmd; skipping firewalld integration")

    try:
        env = _build_engine_env()
    except VendorSelectionError as e:
        if ap_ifname:
            _cleanup_firewalld(ap_ifname, firewalld_cfg)
        payload = e.to_payload()
        payload.setdefault("error", "vendor_selection_failed")
        err_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return EngineStartResult(
            ok=False,
            pid=None,
            exit_code=None,
            stdout_tail=[],
            stderr_tail=[],
            error=f"vendor_selection_failed:{err_json}",
            cmd=_redact_cmd(cmd),
            started_ts=None,
        )
    except Exception as e:
        if ap_ifname:
            _cleanup_firewalld(ap_ifname, firewalld_cfg)
        return EngineStartResult(
            ok=False,
            pid=None,
            exit_code=None,
            stdout_tail=[],
            stderr_tail=[],
            error=f"spawn_failed: {e}",
            cmd=_redact_cmd(cmd),
            started_ts=None,
        )

    try:
        _ln_proc = subprocess.Popen(
            cmd,  # IMPORTANT: full cmd used to spawn (includes real passphrase)
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            close_fds=True,
            env=env,
            # isolate lnxrouter into its own session/PGID so we can reliably kill its whole tree
            start_new_session=True,
        )
    except Exception as e:
        _ln_proc = None
        if ap_ifname:
            _cleanup_firewalld(ap_ifname, firewalld_cfg)
        return EngineStartResult(
            ok=False,
            pid=None,
            exit_code=None,
            stdout_tail=[],
            stderr_tail=[],
            error=f"spawn_failed: {e}",
            cmd=_redact_cmd(cmd),
            started_ts=None,
        )

    assert _ln_proc.stdout is not None
    assert _ln_proc.stderr is not None

    stdout_thread = threading.Thread(
        target=_reader_thread,
        args=(_ln_proc.stdout, _stdout_tail, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader_thread,
        args=(_ln_proc.stderr, _stderr_tail, "stderr"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    # Detect immediate exits (common when hostapd fails quickly)
    deadline = time.time() + early_fail_window_s
    while time.time() < deadline:
        rc = _ln_proc.poll()
        if rc is not None:
            stdout_thread.join(timeout=0.5)
            stderr_thread.join(timeout=0.5)
            out, err = _collect_failure_output()

            # Cleanup: treat as a failed start, so revert firewalld if configured.
            if ap_ifname:
                _cleanup_firewalld(ap_ifname, firewalld_cfg)

            _ln_proc = None
            return EngineStartResult(
                ok=False,
                pid=None,
                exit_code=rc,
                stdout_tail=out,
                stderr_tail=err,
                error=f"engine_exited_early: rc={rc}",
                cmd=_redact_cmd(cmd),
                started_ts=started_ts,
            )
        time.sleep(0.05)

    return EngineStartResult(
        ok=True,
        pid=_ln_proc.pid,
        exit_code=None,
        stdout_tail=list(_stdout_tail),
        stderr_tail=list(_stderr_tail),
        error=None,
        cmd=_redact_cmd(cmd),  # IMPORTANT: redacted for API/state
        started_ts=started_ts,
    )


def stop_engine(
    timeout_s: float = 5.0,
    firewalld_cfg: Optional[Dict[str, object]] = None,
) -> Tuple[bool, Optional[int], List[str], List[str], Optional[str]]:
    global _ln_proc, _last_ap_ifname, _last_firewalld_cfg

    if firewalld_cfg is None:
        firewalld_cfg = dict(_last_firewalld_cfg)

    if _ln_proc is None:
        out, err = get_tails()
        return True, None, out, err, None

    # Already exited
    if _ln_proc.poll() is not None:
        rc = _ln_proc.returncode
        out, err = get_tails()
        _ln_proc = None
        if _last_ap_ifname:
            _cleanup_firewalld(_last_ap_ifname, firewalld_cfg)
        return True, rc, out, err, None

    pid = _ln_proc.pid

    try:
        _kill_process_group(pid, signal.SIGTERM)
    except Exception as e:
        out, err = get_tails()
        return False, None, out, err, f"sigterm_failed: {e}"

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rc = _ln_proc.poll()
        if rc is not None:
            out, err = get_tails()
            _ln_proc = None
            if _last_ap_ifname:
                _cleanup_firewalld(_last_ap_ifname, firewalld_cfg)
            return True, rc, out, err, None
        time.sleep(0.05)

    try:
        _kill_process_group(pid, signal.SIGKILL)
    except Exception as e:
        out, err = get_tails()
        return False, None, out, err, f"sigkill_failed: {e}"

    time.sleep(0.2)
    rc = _ln_proc.poll()
    out, err = get_tails()

    _ln_proc = None
    if _last_ap_ifname:
        _cleanup_firewalld(_last_ap_ifname, firewalld_cfg)

    return (rc is not None), rc, out, err, ("killed" if rc is not None else "kill_timeout")
