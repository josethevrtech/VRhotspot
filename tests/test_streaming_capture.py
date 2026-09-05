import json
import threading
import time
from types import SimpleNamespace

import pytest

from vr_hotspotd.diagnostics import streaming
from vr_hotspotd.diagnostics.streaming import CaptureError, StreamingCaptureManager


def _wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "capture did not settle"
        time.sleep(0.005)


@pytest.fixture
def managers():
    created = []

    def make(provider):
        manager = StreamingCaptureManager(provider)
        created.append(manager)
        return manager

    yield make
    for manager in created:
        manager.close()


@pytest.mark.parametrize("duration", [None, True, False, 9, 601, 10.0, "120", float("nan"), float("inf")])
def test_duration_is_a_bounded_integer(managers, duration):
    manager = managers(lambda: {})
    with pytest.raises(CaptureError, match="invalid_duration"):
        manager.start(duration)
    assert manager.status()["state"] == "idle"


def test_capture_samples_are_passive_bounded_and_copy_isolated(managers, monkeypatch):
    monkeypatch.setattr(streaming, "SAMPLE_INTERVAL_S", 0.01)
    monkeypatch.setattr(streaming, "MAX_SAMPLES", 3)
    calls = []

    def provider():
        calls.append(True)
        return {"clients": [{"station": "client-1", "signal_dbm": -48}], "signal": float("nan")}

    manager = managers(provider)
    metadata = manager.start(10)
    _wait_until(lambda: manager.status()["sample_count"] == 3)
    manager.stop(metadata["capture_id"])
    report = manager.report(metadata["capture_id"])
    assert report["sample_count"] == 3
    assert len(calls) == 3
    assert report["passive"] is True
    assert report["samples"][0]["data"]["signal"] is None
    assert report["started_at"].endswith("+00:00")
    assert report["finished_at"].endswith("+00:00")
    times = [sample["elapsed_s"] for sample in report["samples"]]
    assert times == sorted(times)
    report["samples"][0]["data"]["clients"].clear()
    assert manager.report(metadata["capture_id"])["samples"][0]["data"]["clients"]


def test_provider_error_is_redacted_and_capture_continues(managers, monkeypatch):
    monkeypatch.setattr(streaming, "SAMPLE_INTERVAL_S", 0.01)
    monkeypatch.setattr(streaming, "MAX_SAMPLES", 2)
    calls = []

    def provider():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("SSID=PrivateNetwork secret-auth-token")
        return {"healthy": True}

    manager = managers(provider)
    capture_id = manager.start(10)["capture_id"]
    _wait_until(lambda: manager.status()["sample_count"] == 2)
    manager.stop(capture_id)
    report = manager.report(capture_id)
    assert report["warnings"] == ["sample_unavailable"]
    assert report["samples"][1]["data"] == {"healthy": True}
    assert "PrivateNetwork" not in json.dumps(report)
    assert "secret-auth-token" not in json.dumps(report)


@pytest.mark.parametrize("sample, expected", [([], "invalid_sample"), ({str(i): "x" * 512 for i in range(64)}, "sample_too_large")])
def test_provider_payload_bounds(managers, monkeypatch, sample, expected):
    monkeypatch.setattr(streaming, "MAX_SAMPLES", 1)
    manager = managers(lambda: sample)
    capture_id = manager.start(10)["capture_id"]
    _wait_until(lambda: manager.status()["sample_count"] == 1)
    manager.stop(capture_id)
    report = manager.report(capture_id)
    assert report["samples"][0]["data"] == {"error": expected}
    assert len(json.dumps(report).encode()) < streaming.MAX_REPORT_BYTES


def test_only_one_capture_and_ids_prevent_cross_session_operations(managers):
    manager = managers(lambda: {})
    first = manager.start(10)["capture_id"]
    with pytest.raises(CaptureError, match="capture_in_progress"):
        manager.start(10)
    manager.stop(first)
    second = manager.start(10)["capture_id"]
    assert first != second
    for action in (manager.mark, manager.stop, manager.report):
        with pytest.raises(CaptureError, match="capture_not_found"):
            action(first)
    assert manager.status()["capture_id"] == second
    assert manager.status()["state"] == "running"


@pytest.mark.parametrize("capture_id", [None, 1, "", "../../secret", "x" * 10000])
def test_invalid_capture_id_does_not_mutate_capture(managers, capture_id):
    manager = managers(lambda: {})
    real_id = manager.start(10)["capture_id"]
    with pytest.raises(CaptureError, match="invalid_capture_id"):
        manager.stop(capture_id)
    assert manager.status()["capture_id"] == real_id
    assert manager.status()["state"] == "running"


def test_markers_are_fixed_bounded_and_only_accepted_while_running(managers, monkeypatch):
    monkeypatch.setattr(streaming, "MAX_MARKERS", 2)
    manager = managers(lambda: {})
    capture_id = manager.start(10)["capture_id"]
    manager.mark(capture_id)
    manager.mark(capture_id)
    with pytest.raises(CaptureError, match="marker_limit_reached"):
        manager.mark(capture_id)
    assert manager.report(capture_id)["markers"][0]["kind"] == "freeze"
    manager.stop(capture_id)
    with pytest.raises(CaptureError, match="capture_not_running"):
        manager.mark(capture_id)


def test_stop_waits_for_inflight_provider_and_discards_its_sample(managers):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def provider():
        entered.set()
        assert release.wait(2)
        return {"late": True}

    manager = managers(provider)
    capture_id = manager.start(10)["capture_id"]
    assert entered.wait(1)
    stopper = threading.Thread(target=lambda: (manager.stop(capture_id), finished.set()))
    stopper.start()
    try:
        assert not finished.wait(0.03)
        release.set()
        stopper.join(1)
        assert finished.is_set()
        assert not manager._thread.is_alive()
        assert manager.report(capture_id)["samples"] == []
        assert manager.status()["state"] == "stopped"
    finally:
        release.set()
        stopper.join(1)


def test_provider_does_not_hold_manager_lock(managers):
    status_read = threading.Event()
    manager = None

    def provider():
        reader = threading.Thread(target=lambda: (manager.status(), status_read.set()))
        reader.start()
        reader.join(0.5)
        return {"lock_free": status_read.is_set()}

    manager = managers(provider)
    capture_id = manager.start(10)["capture_id"]
    _wait_until(lambda: manager.status()["sample_count"] == 1)
    assert manager.report(capture_id)["samples"][0]["data"]["lock_free"] is True


def test_retention_expires_and_close_prevents_new_background_work(managers, monkeypatch):
    manager = managers(lambda: {})
    capture_id = manager.start(10)["capture_id"]
    manager.stop(capture_id)
    monkeypatch.setattr(streaming, "RETENTION_S", 0)
    assert manager.status()["state"] == "idle"
    with pytest.raises(CaptureError, match="capture_not_found"):
        manager.report(capture_id)
    manager.close()
    with pytest.raises(CaptureError, match="capture_closed"):
        manager.start(10)
    assert not manager._thread.is_alive()


def test_report_enforces_final_serialized_size_limit(managers, monkeypatch):
    manager = managers(lambda: {})
    capture_id = manager.start(10)["capture_id"]
    monkeypatch.setattr(streaming, "MAX_REPORT_BYTES", 10)
    with pytest.raises(CaptureError, match="report_size_limit"):
        manager.report(capture_id)


def test_sample_limit_does_not_end_observation_window_early(managers, monkeypatch):
    monkeypatch.setattr(streaming, "MAX_SAMPLES", 1)
    calls = []
    manager = managers(lambda: calls.append(True) or {})
    capture_id = manager.start(10)["capture_id"]
    _wait_until(lambda: manager.status()["sample_count"] == 1)
    assert manager.status()["state"] == "running"
    assert manager.mark(capture_id)["marker_count"] == 1
    assert len(calls) == 1
    manager.stop(capture_id)
    assert not manager._thread.is_alive()


def test_marker_after_deadline_is_rejected_even_during_last_provider_call(managers, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def provider():
        entered.set()
        assert release.wait(2)
        return {}

    manager = managers(provider)
    capture_id = manager.start(10)["capture_id"]
    assert entered.wait(1)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: manager._started + 11))
    try:
        assert manager.status()["state"] == "running"
        with pytest.raises(CaptureError, match="capture_not_running"):
            manager.mark(capture_id)
        assert manager.status()["marker_count"] == 0
    finally:
        release.set()
        manager.stop(capture_id)
