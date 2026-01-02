import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from . import firewalld  # SteamOS: firewalld owns nftables

ENGINE_STDOUT_MAX_LINES = 200
ENGINE_STDERR_MAX_LINES = 200

_ln_proc: Optional[subprocess.Popen] = None
_stdout_tail: Deque[str] = deque(maxlen=ENGINE_STDOUT_MAX_LINES)
_stderr_tail: Deque[str] = deque(maxlen=ENGINE_STDERR_MAX_LINES)

_last_ap_ifname: Optional[str] = None
_last_firewalld_cfg: Dict[str, object] = {}


def _note(msg: str) -> None:
    # Keep supervisor notes in stderr tail so they show up for diagnostics.
    _stderr_tail.append(f"[supervisor] {msg}")


def _reader_thread(stream, tail: Deque[str], label: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            tail.append(line.rstrip("\n"))
    except Exception:
        tail.append(f"[{label}] reader error")
    finally:
        try:
            stream.close()
        except Exception:
            pass


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


def _vendor_bin() -> str:
    """
    Resolve vendor/bin. Works both in repo layout and installed layout.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # repo layout: backend/vr_hotspotd/engine -> backend/vendor/bin
        os.path.abspath(os.path.join(here, "..", "..", "..", "vendor", "bin")),
        # installed layout
        "/var/lib/vr-hotspot/app/backend/vendor/bin",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return os.path.abspath(os.path.join(here, "..", "..", "..", "vendor", "bin"))


def _which_in_path(exe: str, path: str) -> Optional[str]:
    for d in path.split(":"):
        cand = os.path.join(d, exe)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _build_engine_env() -> Dict[str, str]:
    """
    Environment for lnxrouter execution.

    Behavior:
      - Prefer system hostapd + dnsmasq if present.
      - Otherwise use bundled vendor/bin hostapd + dnsmasq.
    """
    env = os.environ.copy()
    vendor = _vendor_bin()
    sys_path = "/usr/sbin:/usr/bin:/sbin:/bin"

    sys_hostapd = _which_in_path("hostapd", sys_path)
    sys_dnsmasq = _which_in_path("dnsmasq", sys_path)

    if sys_hostapd and sys_dnsmasq:
        env["PATH"] = sys_path
        env.pop("HOSTAPD", None)
        env.pop("DNSMASQ", None)
    else:
        env["PATH"] = f"{vendor}:{sys_path}"
        env["HOSTAPD"] = os.path.join(vendor, "hostapd")
        env["DNSMASQ"] = os.path.join(vendor, "dnsmasq")

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
        return None


def _apply_firewalld(ap_ifname: str, cfg: Dict[str, object]) -> None:
    """
    Apply firewalld runtime policy for the AP interface.
    Must use firewall-cmd (NOT raw nft) on SteamOS.
    """
    enabled = bool(cfg.get("firewalld_enabled", True))
    if not enabled:
        _note("firewalld integration disabled by config")
        return

    if not firewalld.is_running():
        _note("firewalld not running; skipping firewall configuration")
        return

    zone = str(cfg.get("firewalld_zone", "trusted"))

    ok, out = firewalld.add_interface(zone, ap_ifname)
    _note(f"firewalld add-interface zone={zone} if={ap_ifname} ok={ok} out={out}")

    if bool(cfg.get("firewalld_enable_masquerade", True)):
        ok, out = firewalld.enable_masquerade(zone)
        _note(f"firewalld add-masquerade zone={zone} ok={ok} out={out}")

    if bool(cfg.get("firewalld_enable_forward", True)):
        ok, out = firewalld.enable_forward(zone)
        _note(f"firewalld add-forward zone={zone} ok={ok} out={out}")


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

    started_ts = int(time.time())

    ap_ifname = _extract_ap_ifname(cmd)
    if ap_ifname:
        _last_ap_ifname = ap_ifname
        _apply_firewalld(ap_ifname, firewalld_cfg)
    else:
        _note("could not extract AP ifname from cmd; skipping firewalld integration")

    try:
        _ln_proc = subprocess.Popen(
            cmd,  # IMPORTANT: full cmd used to spawn (includes real passphrase)
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            close_fds=True,
            env=_build_engine_env(),
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

    threading.Thread(
        target=_reader_thread,
        args=(_ln_proc.stdout, _stdout_tail, "stdout"),
        daemon=True,
    ).start()
    threading.Thread(
        target=_reader_thread,
        args=(_ln_proc.stderr, _stderr_tail, "stderr"),
        daemon=True,
    ).start()

    # Detect immediate exits (common when hostapd fails quickly)
    deadline = time.time() + early_fail_window_s
    while time.time() < deadline:
        rc = _ln_proc.poll()
        if rc is not None:
            out, err = get_tails()

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
