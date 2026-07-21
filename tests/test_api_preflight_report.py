import io
import json
from email.message import Message

import vr_hotspotd.api as api
from vr_hotspotd.api import APIHandler


def _make_handler(path: str = "/v1/diagnostics/preflight"):
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


def test_preflight_report_endpoint_requires_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    called = []
    monkeypatch.setattr(
        api,
        "collect_preflight_report",
        lambda **_kwargs: called.append(True) or {},
    )

    handler = _make_handler()
    handler.do_GET()

    assert handler._last_code == 401
    assert _response_json(handler)["result_code"] == "unauthorized"
    assert called == []


def test_preflight_report_endpoint_accepts_valid_token_and_uses_collector(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    config = {"band_preference": "5ghz"}
    report = {"schema_version": 1, "overall_readiness": "ready"}
    seen = {}
    monkeypatch.setattr(api, "load_config_snapshot", lambda: config)

    def collect(*, config):
        seen["config"] = config
        return report

    monkeypatch.setattr(api, "collect_preflight_report", collect)

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()

    payload = _response_json(handler)
    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert payload["data"] == report
    assert seen["config"] is config
