import io
import json
from email.message import Message

import pytest

import vr_hotspotd.server as api_server
from vr_hotspotd.api import APIHandler


def _handler_with_headers(headers, *, path="/v1/info", method="GET", body=b"{}"):
    handler = APIHandler.__new__(APIHandler)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    msg = Message()
    for key, value in headers.items():
        msg[key] = value
    msg["Content-Length"] = str(len(body))
    handler.headers = msg
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


def _perform(handler):
    getattr(handler, f"do_{handler.command}")()
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_get_req_token_prefers_x_api_token():
    handler = _handler_with_headers(
        {
            "X-Api-Token": "token-x",
            "Authorization": "Bearer token-bearer",
        }
    )
    assert handler._get_req_token() == "token-x"


def test_get_req_token_bearer():
    handler = _handler_with_headers({"Authorization": "Bearer token-bearer"})
    assert handler._get_req_token() == "token-bearer"


@pytest.mark.parametrize(
    ("path", "expected_status"),
    (
        ("/", 302),
        ("/ui", 200),
        ("/assets/ui.js", 200),
        ("/favicon.ico", 204),
        ("/healthz", 200),
    ),
)
def test_public_get_routes_remain_public_without_configured_token(
    monkeypatch,
    path,
    expected_status,
):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)

    handler = _perform(_handler_with_headers({}, path=path))

    assert handler._last_code == expected_status
    if path == "/healthz":
        assert handler.wfile.getvalue() == b"ok\n"


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/v1"),
        ("GET", "/v1/status"),
        ("GET", "/v1/adapters"),
        ("GET", "/v1/adapters/readiness"),
        ("GET", "/v1/config"),
        ("GET", "/v1/info"),
        ("GET", "/v1/diagnostics/clients"),
        ("GET", "/v1/diagnostics/preflight"),
        ("GET", "/v1/diagnostics/support_bundle"),
        ("GET", "/v1/future-privileged"),
        ("POST", "/v1/start"),
        ("POST", "/v1/stop"),
        ("POST", "/v1/repair"),
        ("POST", "/v1/restart"),
        ("POST", "/v1/autostart"),
        ("POST", "/v1/config"),
        ("POST", "/v1/config/reveal_passphrase"),
        ("POST", "/v1/diagnostics/ping"),
        ("POST", "/v1/diagnostics/ping_under_load"),
        ("POST", "/v1/diagnostics/udp_latency"),
        ("POST", "/v1/future-privileged"),
        ("PUT", "/v1/config"),
    ),
)
def test_privileged_routes_fail_closed_without_configured_token(monkeypatch, method, path):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)

    handler = _perform(_handler_with_headers({}, path=path, method=method))
    payload = _response_json(handler)

    assert handler._last_code == 503
    assert payload["result_code"] == "api_token_missing"
    assert payload["warnings"] == ["api_token_not_configured"]
    assert "VR_HOTSPOTD_API_TOKEN" in payload["data"]["hint"]
    assert "web ui" not in payload["data"]["hint"].lower()


@pytest.mark.parametrize(
    "headers",
    (
        pytest.param({"X-Api-Token": "attacker-x-token"}, id="x-api-token"),
        pytest.param(
            {"Authorization": "Bearer attacker-bearer-token"},
            id="bearer-token",
        ),
        pytest.param({"X-Api-Token": ""}, id="empty-x-api-token"),
        pytest.param({"Authorization": "Bearer "}, id="empty-bearer-token"),
    ),
)
def test_client_token_cannot_bypass_missing_server_token(monkeypatch, headers):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)

    handler = _perform(_handler_with_headers(headers))
    payload_text = handler.wfile.getvalue().decode("utf-8")

    assert handler._last_code == 503
    assert json.loads(payload_text)["result_code"] == "api_token_missing"
    assert "attacker-x-token" not in payload_text
    assert "attacker-bearer-token" not in payload_text


@pytest.mark.parametrize("server_token", (None, "", "   "))
def test_missing_or_blank_server_token_is_never_authorized(monkeypatch, server_token):
    if server_token is None:
        monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", server_token)
    handler = _handler_with_headers({"X-Api-Token": "any-client-token"})

    assert handler._is_authorized() is False

    _perform(handler)
    assert handler._last_code == 503
    assert _response_json(handler)["result_code"] == "api_token_missing"


@pytest.mark.parametrize(
    "headers",
    (
        pytest.param({"X-Api-Token": "configured-secret"}, id="x-api-token"),
        pytest.param(
            {"Authorization": "Bearer configured-secret"},
            id="bearer-token",
        ),
    ),
)
def test_configured_token_accepts_supported_client_headers(monkeypatch, headers):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")

    handler = _perform(_handler_with_headers(headers))
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert payload["data"]["token_configured"] is True


@pytest.mark.parametrize(
    "headers",
    (
        pytest.param({}, id="missing-client-token"),
        pytest.param({"X-Api-Token": "wrong"}, id="wrong-x-api-token"),
        pytest.param({"Authorization": "Bearer wrong"}, id="wrong-bearer-token"),
        pytest.param({"X-Api-Token": ""}, id="empty-x-api-token"),
        pytest.param({"Authorization": "Bearer "}, id="empty-bearer-token"),
    ),
)
def test_configured_token_rejects_missing_empty_or_wrong_client_token(monkeypatch, headers):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")

    handler = _perform(_handler_with_headers(headers))
    payload = _response_json(handler)

    assert handler._last_code == 401
    assert payload["result_code"] == "unauthorized"
    assert payload["warnings"] == ["missing_or_invalid_token"]


@pytest.mark.parametrize("configured_token", (None, "", "   "))
def test_non_loopback_bind_without_token_remains_refused(
    monkeypatch,
    configured_token,
):
    monkeypatch.setenv("VR_HOTSPOTD_HOST", "0.0.0.0")
    monkeypatch.setenv("VR_HOTSPOTD_PORT", "8732")
    if configured_token is None:
        monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", configured_token)

    def must_not_build(*_args, **_kwargs):
        raise AssertionError("server construction must not occur")

    monkeypatch.setattr(api_server, "ThreadingHTTPServer", must_not_build)

    with pytest.raises(SystemExit) as exc_info:
        api_server.build_server()

    assert exc_info.value.code == 1


def test_loopback_bind_without_token_still_starts_for_public_routes(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_HOST", "127.0.0.1")
    monkeypatch.setenv("VR_HOTSPOTD_PORT", "9876")
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    seen = {}

    class FakeServer:
        daemon_threads = False

    def build(address, handler_class):
        seen["address"] = address
        seen["handler_class"] = handler_class
        return FakeServer()

    monkeypatch.setattr(api_server, "ThreadingHTTPServer", build)

    built = api_server.build_server()

    assert seen == {
        "address": ("127.0.0.1", 9876),
        "handler_class": APIHandler,
    }
    assert built.daemon_threads is True
