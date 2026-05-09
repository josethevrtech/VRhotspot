import io
import json
from email.message import Message

import vr_hotspotd.api as api
from vr_hotspotd.api import APIHandler


def _make_handler(path: str = "/v1/adapters/readiness"):
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

    def send_header(_key, _value):
        return

    def end_headers():
        return

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    return handler


def _response_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_adapter_readiness_endpoint_exists(monkeypatch):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    monkeypatch.setattr(api, "get_adapters", lambda: {"adapters": [], "recommended": None})

    handler = _make_handler()
    handler.do_GET()

    payload = _response_json(handler)
    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert payload["data"]["adapters"] == []
    assert payload["data"]["summary"]["reason_codes"] == ["no_adapter_found"]


def test_adapter_readiness_requires_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")

    handler = _make_handler()
    handler.do_GET()

    assert handler._last_code == 401
    assert _response_json(handler)["result_code"] == "unauthorized"


def test_adapter_readiness_calls_model_with_inventory(monkeypatch):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    inventory = {
        "recommended": "wlan1",
        "global_regdom": {"country": "US"},
        "adapters": [{"ifname": "wlan1", "supports_ap": True}],
    }
    seen = {}

    def fake_build_readiness_model(arg):
        seen["inventory"] = arg
        return {"sentinel": True}

    monkeypatch.setattr(api, "get_adapters", lambda: inventory)
    monkeypatch.setattr(api, "build_readiness_model", fake_build_readiness_model)

    handler = _make_handler()
    handler.do_GET()

    assert handler._last_code == 200
    assert seen["inventory"] is inventory
    assert _response_json(handler)["data"] == {"sentinel": True}


def test_adapter_readiness_no_adapter_response_shape(monkeypatch):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    monkeypatch.setattr(
        api,
        "get_adapters",
        lambda: {
            "recommended": None,
            "adapters": [],
            "global_regdom": {"country": "US", "raw": "country US: DFS-FCC"},
        },
    )

    handler = _make_handler()
    handler.do_GET()

    data = _response_json(handler)["data"]
    assert handler._last_code == 200
    assert data["recommended"] is None
    assert data["basic_mode_recommended"] is None
    assert data["adapters"] == []
    assert data["global_regulatory_domain"]["country"] == "US"
    assert data["summary"]["readiness_state"] == "unsupported"
    assert data["summary"]["six_ghz_state"] == "unknown"
    assert data["summary"]["recommendation_score"] == 0
    assert data["summary"]["reason_codes"] == ["no_adapter_found"]


def test_adapters_endpoint_output_unchanged(monkeypatch):
    monkeypatch.delenv("VR_HOTSPOTD_API_TOKEN", raising=False)
    inventory = {"recommended": "wlan1", "adapters": [{"ifname": "wlan1"}]}
    monkeypatch.setattr(api, "get_adapters", lambda: inventory)

    handler = _make_handler("/v1/adapters")
    handler.do_GET()

    assert handler._last_code == 200
    assert _response_json(handler)["data"] is not inventory
    assert _response_json(handler)["data"] == inventory
