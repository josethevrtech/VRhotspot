import io
import json
import zipfile
from email.message import Message

import vr_hotspotd.api as api
from vr_hotspotd.api import APIHandler


def _make_handler(path: str = "/v1/diagnostics/support_bundle"):
    handler = APIHandler.__new__(APIHandler)
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None
    handler._sent_headers = []

    def send_response(code, _message=None):
        handler._last_code = code

    def send_header(key, value):
        handler._sent_headers.append((key, value))

    def end_headers():
        return

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    return handler


def _headers(handler):
    return {key.lower(): value for key, value in handler._sent_headers}


def _zip_members(handler):
    with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _stub_bundle_sources(monkeypatch):
    monkeypatch.setattr(
        APIHandler,
        "_status_view",
        lambda self, include_logs: {
            "running": True,
            "wpa2_passphrase": "raw-wifi-passphrase",
            "config_text": "wpa_passphrase=raw-config-passphrase\n",
        },
    )
    monkeypatch.setattr(
        api,
        "get_adapters",
        lambda: {
            "recommended": "wlan1",
            "adapters": [
                {
                    "ifname": "wlan1",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "api_token": "raw-adapter-token",
                }
            ],
        },
    )
    monkeypatch.setattr(
        api,
        "build_readiness_model",
        lambda inventory: {
            "recommended": inventory.get("recommended"),
            "secret": "raw-readiness-secret",
        },
    )


def test_support_bundle_requires_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")

    handler = _make_handler()
    handler.do_GET()

    assert handler._last_code == 401


def test_support_bundle_returns_zip_headers_and_required_members(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    _stub_bundle_sources(monkeypatch)

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()

    headers = _headers(handler)
    members = _zip_members(handler)

    assert handler._last_code == 200
    assert headers["content-type"] == "application/zip"
    assert "filename=\"vr-hotspot-support-bundle-" in headers["content-disposition"]
    assert headers["content-disposition"].endswith(".zip\"")
    assert "manifest.json" in members
    assert "README.txt" in members
    assert json.loads(members["manifest.json"].decode("utf-8"))


def test_support_bundle_archive_does_not_leak_raw_tokens_or_passphrases(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "raw-api-token")
    _stub_bundle_sources(monkeypatch)

    handler = _make_handler()
    handler.headers["Authorization"] = "Bearer raw-api-token"
    handler.do_GET()

    archive_bytes = handler.wfile.getvalue()
    members = _zip_members(handler)
    all_member_bytes = b"".join(members.values())

    assert b"raw-api-token" not in archive_bytes
    assert b"raw-api-token" not in all_member_bytes
    assert b"raw-wifi-passphrase" not in all_member_bytes
    assert b"raw-config-passphrase" not in all_member_bytes
    assert b"raw-adapter-token" not in all_member_bytes
    assert b"raw-readiness-secret" not in all_member_bytes
