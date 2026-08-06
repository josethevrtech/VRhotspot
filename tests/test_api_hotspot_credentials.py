"""GET /v1/config/hotspot-credentials: the narrow secret-reveal contract.

The manual-join wizard needs the hotspot SSID and passphrase on demand. The
endpoint must require the API token, return only those two fields with
Cache-Control: no-store, and the passphrase must never leak into the general
config view, request logs, or support bundles.
"""

import io
import json
import logging
from email.message import Message
from unittest.mock import patch

import pytest

from vr_hotspotd.api import APIHandler
from vr_hotspotd.diagnostics.support_bundle import redact_support_bundle_data


SECRET = "swordfish-quest-passphrase"
CONFIG = {"ssid": "VR-Hotspot", "wpa2_passphrase": SECRET, "debug": False}


def _handler(headers, *, path="/v1/config/hotspot-credentials", method="GET"):
    handler = APIHandler.__new__(APIHandler)
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    msg = Message()
    for key, value in headers.items():
        msg[key] = value
    handler.headers = msg
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None
    handler._sent_headers = {}

    def send_response(code, _message=None):
        handler._last_code = code

    handler.send_response = send_response
    handler.send_header = lambda key, value: handler._sent_headers.__setitem__(key, value)
    handler.end_headers = lambda: None
    return handler


def _perform(handler):
    getattr(handler, f"do_{handler.command}")()
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_unauthenticated_request_is_rejected(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")

    handler = _perform(_handler({"X-Api-Token": "wrong"}))
    payload = _response_json(handler)

    assert handler._last_code == 401
    assert payload["result_code"] == "unauthorized"
    assert SECRET not in handler.wfile.getvalue().decode("utf-8")


def test_missing_server_token_fails_closed(monkeypatch):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)

    handler = _perform(_handler({}))

    assert handler._last_code == 503
    assert _response_json(handler)["result_code"] == "api_token_missing"


@patch("vr_hotspotd.api.load_config")
def test_authorized_request_returns_only_ssid_and_passphrase(mock_load_config, monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")
    mock_load_config.return_value = dict(CONFIG)

    handler = _perform(_handler({"X-Api-Token": "configured-secret"}))
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert payload["data"] == {"ssid": "VR-Hotspot", "wpa2_passphrase": SECRET}


@patch("vr_hotspotd.api.load_config")
def test_response_is_marked_no_store(mock_load_config, monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")
    mock_load_config.return_value = dict(CONFIG)

    handler = _perform(_handler({"X-Api-Token": "configured-secret"}))

    assert handler._sent_headers.get("Cache-Control") == "no-store"


@patch("vr_hotspotd.api.load_config")
def test_unset_passphrase_returns_passphrase_not_set(mock_load_config, monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")
    mock_load_config.return_value = {"ssid": "VR-Hotspot", "wpa2_passphrase": ""}

    handler = _perform(_handler({"X-Api-Token": "configured-secret"}))
    payload = _response_json(handler)

    assert handler._last_code == 404
    assert payload["result_code"] == "passphrase_not_set"
    assert payload["data"] == {}


@pytest.mark.parametrize("query", ("", "?include_secrets=1"))
@patch("vr_hotspotd.api.load_config")
def test_ordinary_config_response_remains_redacted(mock_load_config, monkeypatch, query):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")
    mock_load_config.return_value = dict(CONFIG)

    handler = _perform(
        _handler({"X-Api-Token": "configured-secret"}, path=f"/v1/config{query}")
    )
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert "wpa2_passphrase" not in payload["data"]
    assert payload["data"]["wpa2_passphrase_set"] is True
    assert SECRET not in handler.wfile.getvalue().decode("utf-8")


@patch("vr_hotspotd.api.load_config")
def test_passphrase_never_appears_in_request_logs(mock_load_config, monkeypatch, caplog):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-secret")
    mock_load_config.return_value = dict(CONFIG)

    with caplog.at_level(logging.DEBUG):
        _perform(_handler({"X-Api-Token": "configured-secret"}))

    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(getattr(record, "__dict__", {}))
    assert SECRET not in caplog.text


def test_support_bundle_redaction_covers_the_passphrase():
    redacted = redact_support_bundle_data(
        {"config": {"ssid": "VR-Hotspot", "wpa2_passphrase": SECRET}}
    )

    assert SECRET not in json.dumps(redacted)
