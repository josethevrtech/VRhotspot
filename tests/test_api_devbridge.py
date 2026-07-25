import io
import json
from email.message import Message

import vr_hotspotd.api as api
from vr_hotspotd.api import APIHandler


def _make_handler(path: str):
    handler = APIHandler.__new__(APIHandler)
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None

    def send_response(code, _message=None):
        handler._last_code = code

    handler.send_response = send_response
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _authed_get(monkeypatch, path):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler = _make_handler(path)
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()
    return handler


def test_devbridge_status_endpoint(monkeypatch):
    sentinel = {"schema_version": 1, "mode": "read_only"}
    monkeypatch.setattr(api, "collect_devbridge_status", lambda: sentinel)

    handler = _authed_get(monkeypatch, "/v1/devbridge/status")
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert set(payload) == {"correlation_id", "result_code", "warnings", "data"}
    assert payload["result_code"] == "ok"
    assert payload["data"] == sentinel


def test_devbridge_devices_endpoint_probes_by_default(monkeypatch):
    seen = {}

    def fake_collect(*, probe_adb):
        seen["probe_adb"] = probe_adb
        return {"devices": []}

    monkeypatch.setattr(api, "collect_devbridge_devices", fake_collect)

    handler = _authed_get(monkeypatch, "/v1/devbridge/devices")

    assert handler._last_code == 200
    assert seen["probe_adb"] is True
    assert _response_json(handler)["data"] == {"devices": []}


def test_devbridge_devices_endpoint_probe_can_be_disabled(monkeypatch):
    seen = {}

    def fake_collect(*, probe_adb):
        seen["probe_adb"] = probe_adb
        return {"devices": []}

    monkeypatch.setattr(api, "collect_devbridge_devices", fake_collect)

    handler = _authed_get(monkeypatch, "/v1/devbridge/devices?probe=0")

    assert handler._last_code == 200
    assert seen["probe_adb"] is False


def test_devbridge_adb_endpoint_passes_ip_and_kind(monkeypatch):
    seen = {}

    def fake_collect(*, ip, kind):
        seen["ip"] = ip
        seen["kind"] = kind
        return {"kind": kind}

    monkeypatch.setattr(api, "collect_adb_command_report", fake_collect)

    handler = _authed_get(
        monkeypatch, "/v1/devbridge/adb?ip=192.168.68.23&kind=logcat"
    )

    assert handler._last_code == 200
    assert seen == {"ip": "192.168.68.23", "kind": "logcat"}
    assert _response_json(handler)["data"] == {"kind": "logcat"}


def test_devbridge_adb_endpoint_defaults(monkeypatch):
    seen = {}

    def fake_collect(*, ip, kind):
        seen["ip"] = ip
        seen["kind"] = kind
        return {}

    monkeypatch.setattr(api, "collect_adb_command_report", fake_collect)

    handler = _authed_get(monkeypatch, "/v1/devbridge/adb")

    assert handler._last_code == 200
    assert seen == {"ip": None, "kind": "all"}


def test_devbridge_adb_endpoint_rejects_invalid_ip(monkeypatch):
    monkeypatch.setattr(
        api,
        "collect_adb_command_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("collector must not run for invalid parameters")
        ),
    )

    handler = _authed_get(monkeypatch, "/v1/devbridge/adb?ip=not-an-ip")
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == "invalid_request"
    assert payload["warnings"] == ["invalid_ip_parameter"]


def test_devbridge_adb_endpoint_rejects_invalid_kind(monkeypatch):
    monkeypatch.setattr(
        api,
        "collect_adb_command_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("collector must not run for invalid parameters")
        ),
    )

    handler = _authed_get(monkeypatch, "/v1/devbridge/adb?kind=bogus")
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == "invalid_request"
    assert payload["warnings"] == ["invalid_kind_parameter"]
    assert payload["data"]["allowed_kinds"] == ["all", "connect", "logcat"]


def test_devbridge_readiness_endpoint(monkeypatch):
    seen = {}

    def fake_collect(*, probe_adb):
        seen["probe_adb"] = probe_adb
        return {"overall": "ready"}

    monkeypatch.setattr(api, "collect_devbridge_readiness", fake_collect)

    handler = _authed_get(monkeypatch, "/v1/devbridge/readiness")

    assert handler._last_code == 200
    assert seen["probe_adb"] is True
    assert _response_json(handler)["data"] == {"overall": "ready"}


def test_devbridge_tools_status_endpoint(monkeypatch):
    sentinel = {
        "schema_version": 1,
        "mode": "read_only",
        "platform_tools_pin": {"implementation_blocked": True},
    }
    monkeypatch.setattr(api, "collect_devbridge_tools_status", lambda: sentinel)

    handler = _authed_get(monkeypatch, "/v1/devbridge/tools/status")
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert set(payload) == {"correlation_id", "result_code", "warnings", "data"}
    assert payload["result_code"] == "ok"
    assert payload["data"] == sentinel


def test_devbridge_endpoints_require_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    for name in (
        "collect_devbridge_status",
        "collect_devbridge_devices",
        "collect_adb_command_report",
        "collect_devbridge_readiness",
        "collect_devbridge_tools_status",
    ):
        monkeypatch.setattr(
            api,
            name,
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("unauthorized requests must not run collectors")
            ),
        )

    for path in (
        "/v1/devbridge/status",
        "/v1/devbridge/devices",
        "/v1/devbridge/adb",
        "/v1/devbridge/readiness",
        "/v1/devbridge/tools/status",
    ):
        handler = _make_handler(path)
        handler.do_GET()
        assert handler._last_code == 401, path
        assert _response_json(handler)["result_code"] == "unauthorized"
