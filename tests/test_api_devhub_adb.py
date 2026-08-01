import io
import json
from email.message import Message

import vr_hotspotd.devtools.devhub_api as devhub_api
from vr_hotspotd.api import APIHandler
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler


def _make_handler(path: str, *, method: str = "GET", body=None):
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    handler = DevHubAPIHandler.__new__(DevHubAPIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    if raw:
        handler.headers["Content-Length"] = str(len(raw))
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
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


def _authorize(monkeypatch, handler):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler.headers["X-Api-Token"] = "secret"
    return handler


def _ok_result(operation, **data):
    return {
        "schema_version": 1,
        "operation": operation,
        "success": True,
        "result_code": "ok",
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "data": data,
    }


def test_devices_endpoint_executes_typed_operation(monkeypatch):
    seen = {}

    def fake_execute(operation, request=None):
        seen["operation"] = operation
        seen["request"] = request
        return _ok_result("devices", devices=[])

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/adb/devices"),
    )

    handler.do_GET()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert seen == {"operation": "devices", "request": None}
    assert payload["result_code"] == "ok"
    assert payload["data"]["data"]["devices"] == []


def test_version_endpoint_executes_typed_operation(monkeypatch):
    monkeypatch.setattr(
        devhub_api,
        "execute_adb_operation",
        lambda operation, request=None: _ok_result(operation),
    )
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/adb/version"),
    )

    handler.do_GET()

    assert handler._last_code == 200
    assert _response_json(handler)["data"]["operation"] == "version"


def test_pair_endpoint_passes_structured_body(monkeypatch):
    seen = {}

    def fake_execute(operation, request=None):
        seen["operation"] = operation
        seen["request"] = dict(request or {})
        return _ok_result("pair", target="192.168.68.23:37143")

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)
    request = {
        "ip": "192.168.68.23",
        "port": 37143,
        "pairing_code": "123456",
    }
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/adb/pair", method="POST", body=request),
    )

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert seen == {"operation": "pair", "request": request}
    assert payload["data"]["data"] == {"target": "192.168.68.23:37143"}
    assert "123456" not in json.dumps(payload)


def test_connect_and_disconnect_route_to_expected_operations(monkeypatch):
    calls = []

    def fake_execute(operation, request=None):
        calls.append((operation, dict(request or {})))
        return _ok_result(operation)

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)

    connect = _authorize(
        monkeypatch,
        _make_handler(
            "/v1/devbridge/adb/connect",
            method="POST",
            body={"ip": "192.168.68.23", "port": 5555},
        ),
    )
    connect.do_POST()

    disconnect = _authorize(
        monkeypatch,
        _make_handler(
            "/v1/devbridge/adb/disconnect",
            method="POST",
            body={"serial": "192.168.68.23:5555"},
        ),
    )
    disconnect.do_POST()

    assert connect._last_code == 200
    assert disconnect._last_code == 200
    assert calls == [
        ("connect", {"ip": "192.168.68.23", "port": 5555}),
        ("disconnect", {"serial": "192.168.68.23:5555"}),
    ]


def test_operation_result_codes_map_to_http_status(monkeypatch):
    cases = {
        "invalid_request": 400,
        "tools_unavailable": 503,
        "timeout": 504,
        "output_limit_exceeded": 502,
        "failed": 409,
    }

    for result_code, expected_status in cases.items():
        monkeypatch.setattr(
            devhub_api,
            "execute_adb_operation",
            lambda operation, request=None, code=result_code: {
                "schema_version": 1,
                "operation": operation,
                "success": False,
                "result_code": code,
                "returncode": None,
                "stdout": "",
                "stderr": code,
                "data": {},
            },
        )
        handler = _authorize(
            monkeypatch,
            _make_handler("/v1/devbridge/adb/devices"),
        )
        handler.do_GET()
        assert handler._last_code == expected_status
        assert _response_json(handler)["result_code"] == result_code


def test_devhub_adb_routes_require_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(
        devhub_api,
        "execute_adb_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized request executed ADB")
        ),
    )

    requests = (
        ("GET", "/v1/devbridge/adb/version", None),
        ("GET", "/v1/devbridge/adb/devices", None),
        ("POST", "/v1/devbridge/adb/pair", {}),
        ("POST", "/v1/devbridge/adb/connect", {}),
        ("POST", "/v1/devbridge/adb/disconnect", {}),
    )
    for method, path, body in requests:
        handler = _make_handler(path, method=method, body=body)
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        assert handler._last_code == 401, path
        assert _response_json(handler)["result_code"] == "unauthorized"


def test_invalid_json_body_is_rejected_before_execution(monkeypatch):
    monkeypatch.setattr(
        devhub_api,
        "execute_adb_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid body executed ADB")
        ),
    )
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/adb/connect", method="POST"),
    )
    handler.rfile = io.BytesIO(b"{")
    handler.headers["Content-Length"] = "1"

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == "invalid_request"
    assert payload["warnings"] == ["body_json_parse_failed"]


def test_non_devhub_routes_delegate_to_base_handler(monkeypatch):
    seen = {}

    def fake_base_get(self):
        seen["path"] = self.path

    monkeypatch.setattr(APIHandler, "do_GET", fake_base_get)
    handler = _make_handler("/v1/status")

    handler.do_GET()

    assert seen == {"path": "/v1/status"}
