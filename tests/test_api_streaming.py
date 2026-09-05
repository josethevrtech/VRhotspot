from email.message import Message
import io
import json
import uuid

import pytest

from vr_hotspotd import api
from vr_hotspotd.diagnostics.streaming import StreamingCaptureManager

BASE = "/v1/diagnostics/streaming"


def _request(path=BASE, *, method="GET", body=None, token="test-streaming-token", raw=None):
    encoded = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
    handler = api.APIHandler.__new__(api.APIHandler)
    handler.rfile = io.BytesIO(encoded)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(encoded))
    if token is not None:
        handler.headers["X-Api-Token"] = token
    handler.command, handler.path = method, path
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler._last_code = None
    handler.send_response = lambda code, _message=None: setattr(handler, "_last_code", code)
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    getattr(handler, "do_" + method)()
    return handler._last_code, json.loads(handler.wfile.getvalue())


@pytest.fixture
def isolated_api(monkeypatch):
    calls = []
    manager = StreamingCaptureManager(lambda: calls.append(True) or {})
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "test-streaming-token")
    monkeypatch.setattr(api, "streaming_capture", manager)
    yield manager, calls
    manager.close()


@pytest.mark.parametrize("method, path", [("GET", BASE), ("POST", BASE),
    ("POST", BASE + "/mark"), ("POST", BASE + "/stop"),
    ("GET", BASE + "/report?capture_id=" + str(uuid.uuid4()))])
def test_all_capture_endpoints_require_auth_before_any_work(isolated_api, method, path):
    manager, calls = isolated_api
    code, result = _request(path, method=method, token=None)
    assert code == 401
    assert result["result_code"] == "unauthorized"
    assert manager.status()["state"] == "idle"
    assert calls == []


def test_start_mark_stop_report_share_one_capture_and_existing_api_envelope(isolated_api):
    _manager, _calls = isolated_api
    code, started = _request(method="POST", body={"duration_s": 10})
    assert code == 200
    assert started["result_code"] == "ok"
    capture_id = started["data"]["capture_id"]
    code, marked = _request(BASE + "/mark", method="POST", body={"capture_id": capture_id})
    assert code == 200
    assert marked["data"]["marker_count"] == 1
    code, stopped = _request(BASE + "/stop", method="POST", body={"capture_id": capture_id})
    assert code == 200
    assert stopped["data"]["state"] == "stopped"
    code, report = _request(BASE + "/report?capture_id=" + capture_id)
    assert code == 200
    assert report["data"]["capture_id"] == capture_id
    assert report["data"]["markers"][0]["kind"] == "freeze"
    assert report["data"]["summary"]["vr_qualified"] is False
    assert report["data"]["summary"]["network_rtt_p99_ms"] is None
    assert len(json.dumps(report).encode()) < 1_000_000


@pytest.mark.parametrize("duration", [True, None, 9, 601, 10.5, "120", float("nan"), float("inf")])
def test_invalid_duration_is_rejected_without_starting_capture(isolated_api, duration):
    manager, calls = isolated_api
    code, result = _request(method="POST", body={"duration_s": duration})
    assert code == 400
    assert result["result_code"] == "invalid_duration"
    assert manager.status()["state"] == "idle"
    assert calls == []


@pytest.mark.parametrize("path, body", [(BASE + "?extra=1", {}),
    (BASE, {"channel": 36}), (BASE + "/mark", {"capture_id": "invalid", "note": "private-user-text"}),
    (BASE + "/stop", {})])
def test_unrecognized_inputs_fail_closed_without_echoing_values(isolated_api, path, body):
    manager, _calls = isolated_api
    code, result = _request(path, method="POST", body=body)
    assert code == 400
    assert "private-user-text" not in json.dumps(result)
    assert manager.status()["state"] == "idle"


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null"])
def test_malformed_body_does_not_default_to_start(isolated_api, raw):
    manager, calls = isolated_api
    code, _result = _request(method="POST", raw=raw)
    assert code == 400
    assert manager.status()["state"] == "idle"
    assert calls == []


def test_conflict_and_stale_ids_have_distinct_codes(isolated_api):
    manager, _calls = isolated_api
    first = manager.start(10)["capture_id"]
    code, result = _request(method="POST", body={"duration_s": 10})
    assert code == 409
    assert result["result_code"] == "capture_in_progress"
    manager.stop(first)
    second = manager.start(10)["capture_id"]
    code, result = _request(BASE + "/stop", method="POST", body={"capture_id": first})
    assert code == 404
    assert result["result_code"] == "capture_not_found"
    assert manager.status()["capture_id"] == second


@pytest.mark.parametrize("query", ["", "?capture_id=bad", "?extra=1", "?capture_id={id}&extra=1", "?capture_id={id}&capture_id={id}"])
def test_report_query_is_exact_and_unambiguous(isolated_api, query):
    manager, _calls = isolated_api
    capture_id = manager.start(10)["capture_id"]
    code, _result = _request(BASE + "/report" + query.format(id=capture_id))
    assert code == 400
