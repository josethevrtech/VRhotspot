import io
from email.message import Message

import pytest

import vr_hotspotd.devtools.devhub_api as devhub_api
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler


@pytest.mark.parametrize(
    ("path", "content_type", "payload"),
    (
        ("/assets/devhub.js", "application/javascript; charset=utf-8", b"console.log('hub');"),
        ("/assets/devhub.css", "text/css; charset=utf-8", b".devhub-page{}"),
    ),
)
def test_developer_hub_assets_are_served_without_api_auth(
    monkeypatch,
    tmp_path,
    path,
    content_type,
    payload,
):
    asset = tmp_path / path.rsplit("/", 1)[-1]
    asset.write_bytes(payload)
    monkeypatch.setattr(
        devhub_api,
        "_resolve_asset_path",
        lambda name: str(asset) if name == asset.name else None,
    )

    handler = DevHubAPIHandler.__new__(DevHubAPIHandler)
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None
    handler._headers = {}

    def send_response(code, _message=None):
        handler._last_code = code

    def send_header(key, value):
        handler._headers[key.casefold()] = value

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = lambda: None

    handler.do_GET()

    assert handler._last_code == 200
    assert handler._headers["content-type"] == content_type
    assert handler.wfile.getvalue() == payload


def test_missing_developer_hub_asset_returns_404(monkeypatch):
    monkeypatch.setattr(devhub_api, "_resolve_asset_path", lambda _name: None)

    handler = DevHubAPIHandler.__new__(DevHubAPIHandler)
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /assets/devhub.js HTTP/1.1"
    handler.path = "/assets/devhub.js"
    handler._last_code = None
    handler.send_response = lambda code, _message=None: setattr(handler, "_last_code", code)
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None

    handler.do_GET()

    assert handler._last_code == 404
    assert handler.wfile.getvalue() == b"Not Found"
