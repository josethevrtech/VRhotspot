import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

STATE_PATH = Path("/run/vr-hotspot/state.json")
STATE_TMP = Path("/run/vr-hotspot/state.json.tmp")

# Guards load-modify-save cycles under concurrent requests.
_LOCK = threading.Lock()

SCHEMA_VERSION = 1

DEFAULT_STATE: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,

    "running": False,
    "phase": "stopped",          # stopped | starting | running | stopping | error
    "adapter": None,
    "ap_interface": None,
    "band": None,

    "mode": None,                # optimized | fallback
    "fallback_reason": None,
    "warnings": [],

    "engine": {
        "pid": None,
        "cmd": None,
        "started_ts": None,
        "last_exit_code": None,
        "last_error": None,
        "stdout_tail": [],
        "stderr_tail": [],
        "ap_logs_tail": [],
    },

    "last_error": None,
    "last_op": None,
    "last_op_ts": None,
    "last_correlation_id": None,

    "tuning": {},
    "network_tuning": {},
    "preflight": {},
}


def _deepcopy_default() -> Dict[str, Any]:
    # JSON roundtrip is fine here; state is small.
    return json.loads(json.dumps(DEFAULT_STATE))


def load_state() -> Dict[str, Any]:
    """
    Load state from disk and merge into defaults, so new fields roll forward.
    Never throws; returns a valid state dict.
    """
    if not STATE_PATH.exists():
        return _deepcopy_default()

    try:
        data = json.loads(STATE_PATH.read_text())
        merged = _deepcopy_default()

        if isinstance(data, dict):
            for k, v in data.items():
                if k == "engine" and isinstance(v, dict):
                    merged["engine"].update(v)
                elif k == "warnings" and isinstance(v, list):
                    merged["warnings"] = v
                else:
                    merged[k] = v

        # Ensure schema_version is always present
        merged.setdefault("schema_version", SCHEMA_VERSION)
        return merged
    except Exception:
        return _deepcopy_default()


def _write_atomic(path: Path, tmp: Path, payload: str) -> None:
    """
    Atomic replace with optional fsync.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write temp file explicitly (lets us fsync).
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            # On some FS / environments fsync may fail; best-effort.
            pass

    os.replace(tmp, path)


def save_state(state: Dict[str, Any]) -> None:
    """
    Persist the given state dict atomically.
    """
    # Ensure schema_version stays correct
    state.setdefault("schema_version", SCHEMA_VERSION)

    payload = json.dumps(state, indent=2, sort_keys=True)
    _write_atomic(STATE_PATH, STATE_TMP, payload)

    # Runtime state is non-secret; 0644 is reasonable.
    try:
        os.chmod(STATE_PATH, 0o644)
    except Exception:
        pass


def update_state(**kwargs) -> Dict[str, Any]:
    """
    Load-modify-save under a lock.
    """
    with _LOCK:
        state = load_state()

        for k, v in kwargs.items():
            if k == "engine" and isinstance(v, dict):
                state["engine"].update(v)
            elif k == "warnings" and isinstance(v, list):
                state["warnings"] = v
            else:
                state[k] = v

        state["last_op_ts"] = int(time.time())
        save_state(state)
        return state
