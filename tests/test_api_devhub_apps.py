import io
import json
from email.message import Message

import vr_hotspotd.devtools.devhub_api as devhub_api
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler


SERIAL = "192.168.68.24:5555"
PACKAGE = "com.example.xrtraining"


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


def _authorize(monkeypatch, handler):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler.headers["X-Api-Token"] = "secret"
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


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


def test_packages_endpoint_passes_selected_device_and_filter(monkeypatch):
    seen = {}

    def fake_execute(operation, request=None):
        seen["operation"] = operation
        seen["request"] = dict(request or {})
        return _ok_result(operation, packages=[PACKAGE])

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)
    handler = _authorize(
        monkeypatch,
        _make_handler(
            "/v1/devbridge/adb/packages?serial=192.168.68.24%3A5555&third_party_only=0"
        ),
    )

    handler.do_GET()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert seen == {
        "operation": "packages",
        "request": {"serial": SERIAL, "third_party_only": False},
    }
    assert payload["data"]["data"]["packages"] == [PACKAGE]


def test_packages_endpoint_defaults_to_third_party_only(monkeypatch):
    seen = {}

    def fake_execute(operation, request=None):
        seen.update(dict(request or {}))
        return _ok_result(operation, packages=[])

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)
    handler = _authorize(
        monkeypatch,
        _make_handler(f"/v1/devbridge/adb/packages?serial={SERIAL}"),
    )

    handler.do_GET()

    assert handler._last_code == 200
    assert seen == {"serial": SERIAL, "third_party_only": True}


def test_app_post_routes_pass_structured_bodies(monkeypatch):
    calls = []

    def fake_execute(operation, request=None):
        calls.append((operation, dict(request or {})))
        return _ok_result(operation)

    monkeypatch.setattr(devhub_api, "execute_adb_operation", fake_execute)
    cases = (
        (
            "/v1/devbridge/adb/install",
            "install",
            {
                "serial": SERIAL,
                "apk_path": "/home/deck/builds/training.apk",
                "reinstall": True,
                "grant_permissions": True,
            },
        ),
        (
            "/v1/devbridge/adb/launch",
            "launch",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            "/v1/devbridge/adb/stop",
            "stop",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            "/v1/devbridge/adb/clear-data",
            "clear_data",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            "/v1/devbridge/adb/uninstall",
            "uninstall",
            {"serial": SERIAL, "package": PACKAGE, "keep_data": True},
        ),
    )

    for path, _operation, body in cases:
        handler = _authorize(
            monkeypatch,
            _make_handler(path, method="POST", body=body),
        )
        handler.do_POST()
        assert handler._last_code == 200

    assert calls == [(operation, body) for _path, operation, body in cases]


def test_app_routes_require_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(
        devhub_api,
        "execute_adb_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized request executed ADB")
        ),
    )

    requests = (
        ("GET", f"/v1/devbridge/adb/packages?serial={SERIAL}", None),
        ("POST", "/v1/devbridge/adb/install", {}),
        ("POST", "/v1/devbridge/adb/launch", {}),
        ("POST", "/v1/devbridge/adb/stop", {}),
        ("POST", "/v1/devbridge/adb/clear-data", {}),
        ("POST", "/v1/devbridge/adb/uninstall", {}),
    )

    for method, path, body in requests:
        handler = _make_handler(path, method=method, body=body)
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        assert handler._last_code == 401, path
        assert _response_json(handler)["result_code"] == "unauthorized"
