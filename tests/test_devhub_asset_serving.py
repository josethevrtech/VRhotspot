import io
import re
from email.message import Message
from pathlib import Path

import pytest

import vr_hotspotd.api as api
import vr_hotspotd.devtools.devhub_api as devhub_api
from vr_hotspotd.devtools.devhub_api import DevHubAPIHandler

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


@pytest.mark.parametrize(
    ("path", "content_type", "payload"),
    (
        ("/assets/devhub.js", "application/javascript; charset=utf-8", b"console.log('hub');"),
        ("/assets/devhub.css", "text/css; charset=utf-8", b".devhub-page{}"),
        (
            "/assets/devhub_upload.js",
            "application/javascript; charset=utf-8",
            b"console.log('upload');",
        ),
        (
            "/assets/devhub_upload.css",
            "text/css; charset=utf-8",
            b".devhub-file-drop{}",
        ),
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


def test_every_portal_asset_has_a_registered_content_type():
    referenced = set()
    for source in (*ASSETS_DIR.glob("*.js"), ASSETS_DIR / "index.html"):
        referenced.update(
            re.findall(r"/assets/([A-Za-z0-9_.-]+?\.[a-z]+)", source.read_text())
        )

    registered = {
        path.rsplit("/", 1)[-1] for path in devhub_api._DEVHUB_ASSET_TYPES
    } | set(api._ASSET_CONTENT_TYPES)

    missing = sorted(referenced - registered)
    assert not missing, f"portal references unregistered assets: {missing}"


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
