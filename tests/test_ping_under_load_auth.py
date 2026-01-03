import io
from email.message import Message

from vr_hotspotd.api import APIHandler


def _make_handler(path: str, method: str = "POST", body: bytes = b"{}"):
    handler = APIHandler.__new__(APIHandler)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None

    def send_response(code, _message=None):
        handler._last_code = code

    def send_header(_key, _value):
        return

    def end_headers():
        return

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    return handler


def test_ping_under_load_requires_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler = _make_handler("/v1/diagnostics/ping_under_load")
    handler.do_POST()
    assert handler._last_code == 401
