import io
import json
from email.message import Message

import vr_hotspotd.devtools.devhub_api as devhub_api
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler


def _make_handler(path: str, *, body=None):
    raw = json.dumps(body or {}).encode("utf-8")
    handler = DevHubAPIHandler.__new__(DevHubAPIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(raw))
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None
    handler.send_response = lambda code, _message=None: setattr(handler, "_last_code", code)
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def _authorize(monkeypatch, handler):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler.headers["X-Api-Token"] = "secret"
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _result(operation, code="ok", success=True):
    return {
        "schema_version": 1,
        "operation": operation,
        "success": success,
        "result_code": code,
        "message": code,
        "warnings": [],
        "data": {},
    }


def test_tools_install_passes_explicit_license_acceptance(monkeypatch):
    seen = {}

    def install(*, license_accepted):
        seen["license_accepted"] = license_accepted
        return _result("install")

    monkeypatch.setattr(devhub_api, "install_managed_platform_tools", install)
    handler = _authorize(
        monkeypatch,
        _make_handler(
            "/v1/devbridge/tools/install",
            body={"license_accepted": True},
        ),
    )

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert seen == {"license_accepted": True}
    assert payload["result_code"] == "ok"
    assert payload["data"]["operation"] == "install"


def test_tools_install_maps_license_requirement_to_400(monkeypatch):
    monkeypatch.setattr(
        devhub_api,
        "install_managed_platform_tools",
        lambda *, license_accepted: _result(
            "install",
            "license_not_accepted",
            False,
        ),
    )
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/tools/install", body={}),
    )

    handler.do_POST()

    assert handler._last_code == 400
    assert _response_json(handler)["result_code"] == "license_not_accepted"


def test_tools_remove_calls_managed_removal(monkeypatch):
    called = False

    def remove():
        nonlocal called
        called = True
        return _result("remove")

    monkeypatch.setattr(devhub_api, "remove_managed_platform_tools", remove)
    handler = _authorize(
        monkeypatch,
        _make_handler("/v1/devbridge/tools/remove", body={}),
    )

    handler.do_POST()

    assert called is True
    assert handler._last_code == 200
    assert _response_json(handler)["data"]["operation"] == "remove"


def test_tools_install_busy_maps_to_conflict(monkeypatch):
    monkeypatch.setattr(
        devhub_api,
        "install_managed_platform_tools",
        lambda *, license_accepted: _result(
            "install",
            "tools_install_busy",
            False,
        ),
    )
    handler = _authorize(
        monkeypatch,
        _make_handler(
            "/v1/devbridge/tools/install",
            body={"license_accepted": True},
        ),
    )

    handler.do_POST()

    assert handler._last_code == 409
    assert _response_json(handler)["result_code"] == "tools_install_busy"


def test_tools_management_routes_require_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(
        devhub_api,
        "install_managed_platform_tools",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized request installed tools")
        ),
    )
    monkeypatch.setattr(
        devhub_api,
        "remove_managed_platform_tools",
        lambda: (_ for _ in ()).throw(
            AssertionError("unauthorized request removed tools")
        ),
    )

    for path in (
        "/v1/devbridge/tools/install",
        "/v1/devbridge/tools/remove",
    ):
        handler = _make_handler(path, body={})
        handler.do_POST()
        assert handler._last_code == 401
        assert _response_json(handler)["result_code"] == "unauthorized"
