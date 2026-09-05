"""Bounded, passive streaming evidence capture; never changes a radio or session."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import threading
import time
from typing import Any, Callable, Dict, Optional
import uuid

SAMPLE_INTERVAL_S = 2.0
MAX_SAMPLES = 300
MAX_MARKERS = 256
MAX_SAMPLE_BYTES = 2_700
MAX_REPORT_BYTES = 900_000
RETENTION_S = 30 * 60


class CaptureError(ValueError):
    """Stable error code safe to return without leaking provider exceptions."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Defensive shape limits in addition to the provider's privacy allowlist."""
    if depth > 8:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (float, int)):
        try:
            return value if math.isfinite(value) and abs(value) <= 1e18 else None
        except OverflowError:
            return None
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        return {
            key[:128]: _json_safe(item, depth + 1)
            for key, item in list(value.items())[:64]
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value[:64]]
    return None


class StreamingCaptureManager:
    """One in-memory capture. The provider must enforce bounded I/O timeouts.

    Stop and shutdown wait for a current provider call to finish, discard its
    result, and join the sampler. No sampler runs after these methods return.
    Retention expiry is enforced on every access; no retention worker is needed.
    """

    def __init__(self, provider: Callable[[], Dict[str, Any]]):
        self._provider = provider
        self._lock = threading.RLock()
        self._capture: Optional[Dict[str, Any]] = None
        self._started = 0.0
        self._finished: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._closed = False

    def _expire_locked(self) -> None:
        if self._finished is not None and time.monotonic() - self._finished >= RETENTION_S:
            self._capture = None
            self._finished = None

    def _metadata_locked(self) -> Dict[str, Any]:
        if self._capture is None:
            return {"schema_version": 1, "state": "idle", "capture_id": None, "passive": True}
        result = {key: deepcopy(value) for key, value in self._capture.items()
                  if key not in {"samples", "markers"}}
        endpoint = self._finished if self._finished is not None else time.monotonic()
        result["elapsed_s"] = round(max(0.0, endpoint - self._started), 3)
        result["sample_count"] = len(self._capture["samples"])
        result["marker_count"] = len(self._capture["markers"])
        return result

    def _require_locked(self, capture_id: str) -> Dict[str, Any]:
        if not isinstance(capture_id, str):
            raise CaptureError("invalid_capture_id")
        try:
            valid = str(uuid.UUID(capture_id)) == capture_id
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            raise CaptureError("invalid_capture_id")
        self._expire_locked()
        if self._capture is None or self._capture["capture_id"] != capture_id:
            raise CaptureError("capture_not_found")
        return self._capture

    def start(self, duration_s: int = 120) -> Dict[str, Any]:
        if isinstance(duration_s, bool) or not isinstance(duration_s, int) or not 10 <= duration_s <= 600:
            raise CaptureError("invalid_duration")
        with self._lock:
            if self._closed:
                raise CaptureError("capture_closed")
            self._expire_locked()
            if self._capture is not None and self._capture["state"] == "running":
                raise CaptureError("capture_in_progress")
            self._started = time.monotonic()
            self._finished = None
            self._stop_event = threading.Event()
            capture_id = str(uuid.uuid4())
            self._capture = {
                "schema_version": 1, "capture_id": capture_id, "state": "running",
                "passive": True, "duration_s": duration_s,
                "sample_interval_s": SAMPLE_INTERVAL_S, "started_at": _utc_now(),
                "finished_at": None, "samples": [], "markers": [], "warnings": [],
            }
            self._thread = threading.Thread(
                target=self._sample_loop, args=(capture_id, self._stop_event),
                name="vr-streaming-capture", daemon=True,
            )
            self._thread.start()
            return self._metadata_locked()

    def _sample_loop(self, capture_id: str, stop_event: threading.Event) -> None:
        deadline = self._started + self._capture["duration_s"]
        next_sample = self._started
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= deadline:
                break
            if now < next_sample:
                stop_event.wait(min(next_sample, deadline) - now)
                continue
            sample_time = now
            timestamp = _utc_now()
            try:
                raw = self._provider()
                data = _json_safe(raw) if isinstance(raw, dict) else {"error": "invalid_sample"}
                encoded = json.dumps(data, allow_nan=False).encode("utf-8")
                if len(encoded) > MAX_SAMPLE_BYTES:
                    data = {"error": "sample_too_large"}
            except Exception:
                data = {"error": "sample_unavailable"}
            with self._lock:
                if stop_event.is_set() or self._capture is None or self._capture["capture_id"] != capture_id:
                    break
                self._capture["samples"].append({
                    "elapsed_s": round(max(0.0, sample_time - self._started), 3),
                    "timestamp": timestamp, "data": data,
                })
                error = data.get("error")
                if isinstance(error, str) and error in {"invalid_sample", "sample_too_large", "sample_unavailable"}:
                    if error not in self._capture["warnings"]:
                        self._capture["warnings"].append(error)
                sample_limit_reached = len(self._capture["samples"]) >= MAX_SAMPLES
            if sample_limit_reached:
                # The sample budget does not shorten the requested observation
                # window: a 600s capture takes its last sample at t=598s and
                # remains available for freeze markers until t=600s.
                stop_event.wait(max(0.0, deadline - time.monotonic()))
                break
            # Skip missed slots; never issue a burst of catch-up calls after slow I/O.
            elapsed = max(0.0, time.monotonic() - self._started)
            next_sample = self._started + (math.floor(elapsed / SAMPLE_INTERVAL_S) + 1) * SAMPLE_INTERVAL_S
        with self._lock:
            if self._capture is not None and self._capture["capture_id"] == capture_id:
                self._capture["state"] = "stopped" if stop_event.is_set() else "completed"
                self._capture["finished_at"] = _utc_now()
                self._finished = time.monotonic()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return self._metadata_locked()

    def mark(self, capture_id: str) -> Dict[str, Any]:
        with self._lock:
            capture = self._require_locked(capture_id)
            elapsed = max(0.0, time.monotonic() - self._started)
            if capture["state"] != "running" or elapsed >= capture["duration_s"]:
                raise CaptureError("capture_not_running")
            if len(capture["markers"]) >= MAX_MARKERS:
                raise CaptureError("marker_limit_reached")
            capture["markers"].append({"kind": "freeze", "timestamp": _utc_now(),
                                       "elapsed_s": round(elapsed, 3)})
            return self._metadata_locked()

    def stop(self, capture_id: str) -> Dict[str, Any]:
        with self._lock:
            self._require_locked(capture_id)
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._lock:
            # A competing start must not cause stop to report another session.
            self._require_locked(capture_id)
            return self._metadata_locked()

    def report(self, capture_id: str) -> Dict[str, Any]:
        with self._lock:
            capture = self._require_locked(capture_id)
            result = self._metadata_locked()
            result["samples"] = deepcopy(capture["samples"])
            result["markers"] = deepcopy(capture["markers"])
        if len(json.dumps(result, allow_nan=False).encode("utf-8")) > MAX_REPORT_BYTES:
            raise CaptureError("report_size_limit")
        return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
