import io
import json
from email.message import Message

import pytest

from vr_hotspotd import api, config, lifecycle
from vr_hotspotd.engine import hostapd6_engine, hostapd_nat_engine


def _config(**updates):
    candidate = dict(config.DEFAULT_CONFIG)
    candidate["wpa2_passphrase"] = "password123"
    candidate.update(updates)
    return candidate


def _api_handler(*, path, body):
    raw = json.dumps(body).encode("utf-8")
    handler = api.APIHandler.__new__(api.APIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    headers = Message()
    headers["Content-Length"] = str(len(raw))
    headers["X-Api-Token"] = "secret"
    handler.headers = headers
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {path} HTTP/1.1"
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


def test_default_and_legacy_missing_network_fields_validate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing-config.json")

    loaded = config.load_config_snapshot()

    assert config.validate_network_config(loaded) == []
    assert config.validate_network_config({"ssid": "legacy-config"}) == []
    assert loaded["lan_gateway_ip"] == "192.168.68.1"
    assert loaded["dhcp_start_ip"] == "192.168.68.10"
    assert loaded["dhcp_end_ip"] == "192.168.68.250"


@pytest.mark.parametrize(
    ("updates", "expected_error"),
    (
        (
            {
                "dhcp_start_ip": "192.168.69.10",
                "dhcp_end_ip": "192.168.69.20",
            },
            "dhcp_range_not_in_gateway_subnet",
        ),
        (
            {
                "dhcp_start_ip": "192.168.68.200",
                "dhcp_end_ip": "192.168.68.100",
            },
            "dhcp_range_invalid",
        ),
        (
            {
                "dhcp_start_ip": "192.168.68.1",
                "dhcp_end_ip": "192.168.68.10",
            },
            "dhcp_range_includes_gateway",
        ),
        (
            {
                "dhcp_start_ip": "192.168.68.0",
                "dhcp_end_ip": "192.168.68.10",
            },
            "dhcp_range_includes_network_address",
        ),
        (
            {
                "dhcp_start_ip": "192.168.68.250",
                "dhcp_end_ip": "192.168.68.255",
            },
            "dhcp_range_includes_broadcast_address",
        ),
        ({"lan_gateway_ip": "not-an-ip"}, "invalid_ip:lan_gateway_ip"),
        ({"dhcp_start_ip": "192.168.68.999"}, "invalid_ip:dhcp_start_ip"),
        ({"dhcp_end_ip": "192.168.68/24"}, "invalid_ip:dhcp_end_ip"),
    ),
)
def test_invalid_network_combinations_are_rejected(updates, expected_error):
    assert expected_error in config.validate_network_config(_config(**updates))


def test_single_address_dhcp_range_is_rejected():
    candidate = _config(
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.10",
    )

    assert config.validate_network_config(candidate) == ["dhcp_range_invalid"]


@pytest.mark.parametrize(
    ("engine", "extra_args"),
    (
        (
            hostapd_nat_engine,
            ["--band", "5ghz", "--ap-security", "wpa2"],
        ),
        (hostapd6_engine, []),
    ),
)
def test_hostapd_engines_validate_network_plan_before_host_actions(
    monkeypatch,
    engine,
    extra_args,
):
    argv = [
        engine.__name__,
        "--ap-ifname",
        "wlan1",
        "--ssid",
        "VR-Hotspot",
        "--passphrase",
        "password123",
        "--country",
        "US",
        "--gateway-ip",
        "192.168.68.1",
        "--dhcp-start",
        "192.168.69.10",
        "--dhcp-end",
        "192.168.69.20",
        *extra_args,
    ]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("engine host action must not run")

    monkeypatch.setattr(engine.sys, "argv", argv)
    monkeypatch.setattr(engine, "_maybe_set_regdom", forbidden)
    monkeypatch.setattr(engine, "_resolve_binary", forbidden)

    with pytest.raises(config.ConfigValidationError) as exc_info:
        engine.main()

    assert exc_info.value.errors == ("dhcp_range_not_in_gateway_subnet",)


def test_write_config_file_rejects_invalid_candidate_without_changing_disk(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config, "CONFIG_TMP", tmp_path / "config.json.tmp")
    config.write_config_file({})
    before = config_path.read_bytes()

    with pytest.raises(config.ConfigValidationError) as exc_info:
        config.write_config_file(
            {
                "dhcp_start_ip": "10.0.0.10",
                "dhcp_end_ip": "10.0.0.20",
            }
        )

    assert exc_info.value.errors == ("dhcp_range_not_in_gateway_subnet",)
    assert config_path.read_bytes() == before


def test_load_config_does_not_write_invalid_migration(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_tmp = tmp_path / "config.json.tmp"
    on_disk = {
        "version": config.CONFIG_SCHEMA_VERSION - 1,
        "ssid": "legacy-invalid",
        "lan_gateway_ip": "192.168.68.1",
        "dhcp_start_ip": "192.168.68.10",
        "dhcp_end_ip": "192.168.68.10",
    }
    before = (json.dumps(on_disk, indent=2) + "\n").encode("utf-8")
    config_path.write_bytes(before)
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config, "CONFIG_TMP", config_tmp)

    loaded = config.load_config()

    assert loaded["version"] == config.CONFIG_SCHEMA_VERSION
    assert config.validate_network_config(loaded) == ["dhcp_range_invalid"]
    assert config_path.read_bytes() == before
    assert not config_tmp.exists()


@pytest.mark.parametrize(
    "updates",
    (
        {"lan_gateway_ip": "10.42.0.1"},
        {"ssid": "must-not-save", "dhcp_start_ip": "malformed"},
    ),
)
def test_config_api_rejects_invalid_merged_candidate_without_persisting(
    monkeypatch,
    updates,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(api, "load_config_snapshot", lambda: _config())
    writes = []
    monkeypatch.setattr(api, "write_config_file", lambda value: writes.append(value))
    handler = _api_handler(path="/v1/config", body={"config": updates})

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == config.INVALID_NETWORK_CONFIG
    assert payload["data"]["validation_errors"]
    assert writes == []


def test_config_api_handles_writer_validation_error_without_persisting(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config, "CONFIG_TMP", tmp_path / "config.json.tmp")
    config.write_config_file({})
    before = config_path.read_bytes()
    monkeypatch.setattr(
        api.APIHandler,
        "_network_config_errors",
        lambda _self, _updates: [],
    )
    proposed_passphrase = "must-not-save-secret"
    updates = {
        "ssid": "must-not-save",
        "wpa2_passphrase": proposed_passphrase,
        "dhcp_start_ip": "192.168.68.10",
        "dhcp_end_ip": "192.168.68.10",
    }
    handler = _api_handler(path="/v1/config", body={"config": updates})

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert set(payload) == {"correlation_id", "result_code", "warnings", "data"}
    assert payload["correlation_id"]
    assert payload["result_code"] == config.INVALID_NETWORK_CONFIG
    assert payload["warnings"] == ["dhcp_range_invalid"]
    assert payload["data"] == {"validation_errors": ["dhcp_range_invalid"]}
    assert proposed_passphrase not in handler.wfile.getvalue().decode("utf-8")
    assert config_path.read_bytes() == before


def test_config_api_preserves_valid_update_behavior(monkeypatch):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    stored = _config()
    monkeypatch.setattr(api, "load_config_snapshot", lambda: dict(stored))
    monkeypatch.setattr(api, "load_config", lambda: dict(stored))
    writes = []

    def write(updates):
        writes.append(dict(updates))
        stored.update(updates)
        return dict(stored)

    monkeypatch.setattr(api, "write_config_file", write)
    handler = _api_handler(
        path="/v1/config",
        body={"config": {"dhcp_end_ip": "192.168.68.200"}},
    )

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 200
    assert payload["result_code"] == "config_saved"
    assert payload["data"]["dhcp_end_ip"] == "192.168.68.200"
    assert writes == [{"dhcp_end_ip": "192.168.68.200"}]


@pytest.mark.parametrize("path", ("/v1/start", "/v1/restart"))
def test_start_apis_reject_invalid_overrides_before_lifecycle_mutation(
    monkeypatch,
    path,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(api, "load_config_snapshot", lambda: _config())
    calls = []
    monkeypatch.setattr(api, "start_hotspot", lambda **_kwargs: calls.append("start"))
    monkeypatch.setattr(api, "stop_hotspot", lambda **_kwargs: calls.append("stop"))
    monkeypatch.setattr(api, "repair", lambda **_kwargs: calls.append("repair"))
    handler = _api_handler(
        path=path,
        body={"overrides": {"dhcp_start_ip": "192.168.69.10"}},
    )

    handler.do_POST()
    payload = _response_json(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == config.INVALID_NETWORK_CONFIG
    assert calls == []


@pytest.mark.parametrize("invalid_source", ("persisted", "override"))
def test_lifecycle_rejects_invalid_network_config_before_snapshot_or_mutation(
    monkeypatch,
    invalid_source,
):
    candidate = _config()
    overrides = None
    if invalid_source == "persisted":
        candidate["dhcp_end_ip"] = "192.168.69.20"
    else:
        overrides = {"dhcp_end_ip": "192.168.69.20"}

    state = {"phase": "stopped", "running": False}

    def update_state(**updates):
        state.update(updates)
        return dict(state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("host inspection or network mutation must not run")

    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "load_state", lambda: dict(state))
    monkeypatch.setattr(lifecycle, "load_config", lambda: dict(candidate))
    monkeypatch.setattr(lifecycle, "update_state", update_state)
    monkeypatch.setattr(lifecycle, "build_host_facts_snapshot", forbidden)
    monkeypatch.setattr(lifecycle, "get_adapters", forbidden)
    monkeypatch.setattr(lifecycle, "_repair_impl", forbidden)
    monkeypatch.setattr(lifecycle, "_maybe_set_regdom", forbidden)
    monkeypatch.setattr(lifecycle.system_tuning, "apply_pre", forbidden)
    monkeypatch.setattr(lifecycle, "start_engine", forbidden)

    result = lifecycle.start_hotspot(overrides=overrides)

    assert result.code == config.INVALID_NETWORK_CONFIG
    assert result.state["last_error"] == config.INVALID_NETWORK_CONFIG
    assert result.state["last_error_detail"]["errors"] == [
        "dhcp_range_not_in_gateway_subnet"
    ]


def test_watchdog_rejects_invalid_network_config_before_teardown(monkeypatch):
    candidate = _config(
        dhcp_start_ip="192.168.69.10",
        dhcp_end_ip="192.168.69.20",
    )
    state = {"phase": "running", "running": True, "warnings": []}
    updates = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("watchdog teardown or restart must not run")

    monkeypatch.setattr(lifecycle, "load_state", lambda: dict(state))
    monkeypatch.setattr(lifecycle, "load_config", lambda: dict(candidate))
    monkeypatch.setattr(lifecycle, "update_state", lambda **value: updates.append(value))
    monkeypatch.setattr(lifecycle, "_stop_hotspot_impl", forbidden)
    monkeypatch.setattr(lifecycle, "_start_hotspot_impl", forbidden)

    lifecycle._restart_from_watchdog("connection_quality_degraded:score=20")

    assert len(updates) == 1
    assert updates[0]["last_error"] == config.INVALID_NETWORK_CONFIG
    assert updates[0]["last_error_detail"] == {
        "errors": ["dhcp_range_not_in_gateway_subnet"],
    }
    assert updates[0]["last_correlation_id"].startswith("watchdog-")
    assert updates[0]["warnings"] == ["dhcp_range_not_in_gateway_subnet"]
