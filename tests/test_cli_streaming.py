import io
import json
import os
import stat
import uuid
from urllib.error import HTTPError

import pytest

from vr_hotspotd import cli

CAPTURE_ID = "69f10238-01a6-4de6-83ce-53fc4b7ca52c"


class Response(io.BytesIO):
    status = 200


@pytest.fixture(autouse=True)
def clear_settings(monkeypatch):
    for key in (*cli._ENV_KEYS, "VR_HOTSPOTD_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)


def _args(tmp_path, operation, *extra):
    return ["diagnostics", "streaming", operation, "--env-file", str(tmp_path / "missing"), *extra]


def _mock_requests(monkeypatch, results):
    requests = []
    iterator = iter(results)

    def request(req, *, timeout):
        requests.append(req)
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return Response(json.dumps({"result_code": "ok", "data": result}).encode())

    monkeypatch.setattr(cli, "_open_preflight_request", request)
    monkeypatch.setattr(cli.time, "sleep", lambda _duration: None)
    return requests


def test_capture_polls_and_exports_private_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "private-api-token")
    running = {"capture_id": CAPTURE_ID, "state": "running"}
    completed = {"capture_id": CAPTURE_ID, "state": "completed"}
    report = {**completed, "samples": [{"data": {"healthy": True}}]}
    requests = _mock_requests(monkeypatch, [running, completed, report])
    target = tmp_path / "capture.json"
    assert cli.main(_args(tmp_path, "capture", "--duration", "10", "--output", str(target))) == 0
    assert json.loads(target.read_text()) == report
    assert [request.get_method() for request in requests] == ["POST", "GET", "GET"]
    assert json.loads(requests[0].data) == {"duration_s": 10}
    assert requests[2].full_url.endswith("/report?capture_id=" + CAPTURE_ID)
    assert requests[0].get_header("X-api-token") == "private-api-token"
    output = capsys.readouterr()
    assert "private-api-token" not in output.out + output.err + target.read_text()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_detached_capture_returns_id_without_polling(monkeypatch, tmp_path, capsys):
    requests = _mock_requests(monkeypatch, [{"capture_id": CAPTURE_ID, "state": "running"}])
    assert cli.main(_args(tmp_path, "capture", "--detach")) == 0
    assert len(requests) == 1
    assert json.loads(capsys.readouterr().out)["capture_id"] == CAPTURE_ID


def test_cli_roundtrip_through_real_loopback_api(monkeypatch, tmp_path, capsys):
    from http.server import ThreadingHTTPServer
    import threading
    from vr_hotspotd import api
    from vr_hotspotd.diagnostics.streaming import StreamingCaptureManager

    manager = StreamingCaptureManager(lambda: {"measurement": "test_fixture"})
    monkeypatch.setattr(api, "streaming_capture", manager)
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "loopback-test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.APIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VR_HOTSPOTD_API_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        assert cli.main(_args(tmp_path, "capture", "--duration", "10", "--detach")) == 0
        capture_id = json.loads(capsys.readouterr().out)["capture_id"]
        for operation in ("mark", "stop"):
            assert cli.main(_args(tmp_path, operation, "--capture-id", capture_id)) == 0
            capsys.readouterr()
        target = tmp_path / "loopback-session.json"
        assert cli.main(_args(tmp_path, "report", "--capture-id", capture_id, "--output", str(target))) == 0
        report = json.loads(target.read_text())
        assert report["state"] == "stopped"
        assert report["capture_id"] == capture_id
        assert report["markers"][0]["kind"] == "freeze"
        assert report["summary"]["vr_qualified"] is False
        assert "loopback-test-token" not in target.read_text()
    finally:
        manager.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("duration", ["0", "9", "601", "10.5", "nan", "inf"])
def test_invalid_duration_never_contacts_daemon(monkeypatch, tmp_path, duration):
    requests = _mock_requests(monkeypatch, [])
    with pytest.raises(SystemExit) as error:
        cli.main(_args(tmp_path, "capture", "--duration", duration))
    assert error.value.code == 2
    assert requests == []


@pytest.mark.parametrize("operation, method, suffix", [
    ("status", "GET", ""), ("mark", "POST", "/mark"),
    ("stop", "POST", "/stop"), ("report", "GET", "/report?capture_id=" + CAPTURE_ID),
])
def test_commands_use_scoped_authenticated_endpoints(monkeypatch, tmp_path, capsys, operation, method, suffix):
    requests = _mock_requests(monkeypatch, [{"capture_id": CAPTURE_ID, "state": "running"}])
    extra = [] if operation == "status" else ["--capture-id", CAPTURE_ID]
    assert cli.main(_args(tmp_path, operation, *extra)) == 0
    assert requests[0].get_method() == method
    assert requests[0].full_url == cli.DEFAULT_API_URL + cli.STREAMING_PATH + suffix
    if method == "POST":
        assert json.loads(requests[0].data) == {"capture_id": CAPTURE_ID}
    assert json.loads(capsys.readouterr().out)["capture_id"] == CAPTURE_ID


def test_ctrl_c_stops_only_own_capture_and_exports_partial(monkeypatch, tmp_path, capsys):
    requests = _mock_requests(monkeypatch, [
        {"capture_id": CAPTURE_ID, "state": "running"},
        {"capture_id": CAPTURE_ID, "state": "stopped"},
        {"capture_id": CAPTURE_ID, "state": "stopped", "samples": [1]},
    ])

    def interrupt(_duration):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupt)
    target = tmp_path / "partial.json"
    assert cli.main(_args(tmp_path, "capture", "--output", str(target))) == 130
    assert requests[1].full_url.endswith("/stop")
    assert json.loads(requests[1].data) == {"capture_id": CAPTURE_ID}
    assert json.loads(target.read_text())["samples"] == [1]
    assert "partial streaming capture" in capsys.readouterr().err


def test_existing_output_is_rejected_before_start(monkeypatch, tmp_path, capsys):
    target = tmp_path / "keep.json"
    target.write_text("existing-user-data")
    requests = _mock_requests(monkeypatch, [])
    with pytest.raises(SystemExit) as error:
        cli.main(_args(tmp_path, "capture", "--output", str(target)))
    assert error.value.code == 1
    assert target.read_text() == "existing-user-data"
    assert requests == []
    assert "refusing to overwrite" in capsys.readouterr().err


def test_changed_session_is_not_stopped_or_exported(monkeypatch, tmp_path, capsys):
    requests = _mock_requests(monkeypatch, [
        {"capture_id": CAPTURE_ID, "state": "running"},
        {"capture_id": str(uuid.uuid4()), "state": "running"},
    ])
    with pytest.raises(SystemExit):
        cli.main(_args(tmp_path, "capture"))
    assert len(requests) == 2
    assert all(not request.full_url.endswith("/stop") for request in requests)
    assert "refusing to operate on another session" in capsys.readouterr().err


def test_report_token_leak_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret-api-token")
    _mock_requests(monkeypatch, [{"capture_id": CAPTURE_ID, "secret": "secret-api-token"}])
    target = tmp_path / "refused.json"
    with pytest.raises(SystemExit):
        cli.main(_args(tmp_path, "report", "--capture-id", CAPTURE_ID, "--output", str(target)))
    captured = capsys.readouterr()
    assert "secret-api-token" not in captured.out + captured.err
    assert not target.exists()


def test_response_size_is_bounded(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "STREAMING_RESPONSE_LIMIT", 100)
    _mock_requests(monkeypatch, [{"capture_id": CAPTURE_ID, "oversized": "x" * 500}])
    with pytest.raises(SystemExit):
        cli.main(_args(tmp_path, "status"))
    assert "exceeded the size limit" in capsys.readouterr().err


def test_redirect_is_rejected_and_error_token_redacted(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret-api-token")
    error = HTTPError("https://example.invalid", 302, "secret-api-token", {}, io.BytesIO(b"{}"))
    requests = _mock_requests(monkeypatch, [error])
    with pytest.raises(SystemExit):
        cli.main(_args(tmp_path, "capture", "--detach"))
    assert len(requests) == 1
    captured = capsys.readouterr()
    assert "redirects are not allowed" in captured.err
    assert "secret-api-token" not in captured.out + captured.err


@pytest.mark.parametrize("bad_id", [None, "not-a-uuid"])
def test_invalid_server_id_does_not_poll(monkeypatch, tmp_path, capsys, bad_id):
    requests = _mock_requests(monkeypatch, [{"capture_id": bad_id, "state": "running"}])
    with pytest.raises(SystemExit):
        cli.main(_args(tmp_path, "capture", "--detach"))
    assert len(requests) == 1
    assert "invalid streaming capture ID" in capsys.readouterr().err


def test_main_status_is_read_only(monkeypatch, tmp_path, capsys):
    requests = _mock_requests(monkeypatch, [{"running": True}])
    assert cli.main(["status", "--env-file", str(tmp_path / "missing")]) == 0
    assert requests[0].get_method() == "GET"
    assert requests[0].full_url.endswith("/v1/status")
    assert json.loads(capsys.readouterr().out) == {"running": True}
