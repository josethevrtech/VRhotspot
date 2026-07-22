import io
import json
from dataclasses import replace
from email.message import Message

from vr_hotspotd import host_facts
import vr_hotspotd.api as api
from vr_hotspotd.api import APIHandler
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


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


def _snapshot_without_adapters():
    snapshot = make_host_facts_snapshot()
    return replace(
        snapshot,
        iw_dev=replace(snapshot.iw_dev, interfaces=()),
        iw_phys=(),
        adapters=(),
    )


def test_adapter_readiness_endpoint_exists(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = _snapshot_without_adapters()
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda *, operation_kind: snapshot,
    )

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()

    payload = _response_json(handler)
    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert payload["data"]["adapters"] == []
    assert payload["data"]["summary"]["reason_codes"] == ["no_adapter_found"]


def test_adapter_readiness_requires_auth(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized requests must not collect host facts")
        ),
    )

    handler = _make_handler()
    handler.do_GET()

    assert handler._last_code == 401
    assert _response_json(handler)["result_code"] == "unauthorized"


def test_adapter_readiness_reuses_one_snapshot_for_inventory_and_model(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = make_host_facts_snapshot()
    inventory = {
        "recommended": "wlan1",
        "global_regdom": {"country": "US"},
        "adapters": [{"ifname": "wlan1", "supports_ap": True}],
    }
    seen = {"builder": []}

    def fake_builder(*, operation_kind):
        seen["builder"].append(operation_kind)
        return snapshot

    def fake_get_adapters(*, host_facts_snapshot):
        seen["inventory_snapshot"] = host_facts_snapshot
        return inventory

    def fake_build_readiness_model(*, host_facts_snapshot):
        seen["readiness_snapshot"] = host_facts_snapshot
        return {"sentinel": True}

    monkeypatch.setattr(api, "build_host_facts_snapshot", fake_builder)
    monkeypatch.setattr(api, "get_adapters", fake_get_adapters)
    monkeypatch.setattr(api, "build_readiness_model", fake_build_readiness_model)

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()

    assert handler._last_code == 200
    assert seen["builder"] == ["adapter_readiness"]
    assert seen["inventory_snapshot"] is snapshot
    assert seen["readiness_snapshot"] is snapshot
    assert _response_json(handler)["data"] == {"sentinel": True}


def test_adapter_readiness_no_adapter_response_shape(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = _snapshot_without_adapters()
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda *, operation_kind: snapshot,
    )

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
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
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = make_host_facts_snapshot()
    inventory = {"recommended": "wlan1", "adapters": [{"ifname": "wlan1"}]}
    seen = []
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda *, operation_kind: seen.append(operation_kind) or snapshot,
    )
    monkeypatch.setattr(
        api,
        "get_adapters",
        lambda *, host_facts_snapshot: inventory,
    )

    handler = _make_handler("/v1/adapters")
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()

    assert handler._last_code == 200
    assert seen == ["adapter_inventory"]
    assert _response_json(handler)["data"] is not inventory
    assert _response_json(handler)["data"] == inventory


def test_adapter_endpoints_preserve_existing_response_shapes(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = make_host_facts_snapshot()
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda *, operation_kind: snapshot,
    )

    adapters_handler = _make_handler("/v1/adapters")
    adapters_handler.headers["X-Api-Token"] = "secret"
    adapters_handler.do_GET()
    adapters_payload = _response_json(adapters_handler)

    readiness_handler = _make_handler("/v1/adapters/readiness")
    readiness_handler.headers["X-Api-Token"] = "secret"
    readiness_handler.do_GET()
    readiness_payload = _response_json(readiness_handler)

    assert set(adapters_payload) == {
        "correlation_id",
        "result_code",
        "warnings",
        "data",
    }
    assert set(adapters_payload["data"]) == {
        "global_regdom",
        "recommended",
        "adapters",
        "notes",
    }
    assert set(adapters_payload["data"]["adapters"][0]) == {
        "id",
        "ifname",
        "phy",
        "bus",
        "supports_ap",
        "supports_wifi6",
        "supports_2ghz",
        "supports_5ghz",
        "supports_6ghz",
        "supports_80mhz",
        "regdom",
        "score",
        "score_breakdown",
        "warnings",
    }
    assert set(readiness_payload) == set(adapters_payload)
    assert set(readiness_payload["data"]) == {
        "recommended",
        "basic_mode_recommended",
        "adapters",
        "global_regulatory_domain",
        "notes",
    }
    assert set(readiness_payload["data"]["adapters"][0]) == {
        "interface",
        "driver",
        "bus_type",
        "chipset_vendor_guess",
        "supports_ap_mode",
        "supports_2ghz",
        "supports_5ghz",
        "supports_6ghz",
        "regulatory_domain",
        "channel_width_hints",
        "basic_mode_visibility",
        "readiness_state",
        "six_ghz_state",
        "recommendation_score",
        "reason_codes",
        "explanation",
    }


def test_readiness_endpoint_surfaces_iw_dev_snapshot_failure_conservatively(
    monkeypatch,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    snapshot = _snapshot_without_adapters()
    snapshot = replace(
        snapshot,
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.dev",
                kind="missing",
                message="required tool is unavailable: iw",
                exit_status=None,
            ),
        ),
    )
    monkeypatch.setattr(
        api,
        "build_host_facts_snapshot",
        lambda *, operation_kind: snapshot,
    )

    adapters_handler = _make_handler("/v1/adapters")
    adapters_handler.headers["X-Api-Token"] = "secret"
    adapters_handler.do_GET()
    adapters_payload = _response_json(adapters_handler)

    handler = _make_handler()
    handler.headers["X-Api-Token"] = "secret"
    handler.do_GET()
    payload = _response_json(handler)

    assert adapters_handler._last_code == 200
    assert adapters_payload["warnings"] == []
    assert adapters_payload["data"] == {
        "error": "snapshot_iw_dev_unavailable",
        "adapters": [],
        "recommended": None,
        "global_regdom": None,
    }
    assert handler._last_code == 200
    assert payload["warnings"] == ["adapter_inventory_error"]
    assert payload["data"]["recommended"] is None
    assert payload["data"]["basic_mode_recommended"] is None
    assert payload["data"]["adapters"] == []
    assert payload["data"]["summary"]["readiness_state"] == "unsupported"
