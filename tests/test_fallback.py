from copy import deepcopy
from types import SimpleNamespace

import vr_hotspotd.lifecycle as lifecycle
from vr_hotspotd.engine.hostapd_nat_cmd import build_cmd_nat
from vr_hotspotd.state import DEFAULT_STATE


def _state_helpers():
    state = deepcopy(DEFAULT_STATE)

    def load_state():
        return state

    def update_state(**kwargs):
        for key, value in kwargs.items():
            if key == "engine" and isinstance(value, dict):
                state["engine"].update(value)
            elif key == "warnings" and isinstance(value, list):
                state["warnings"] = value
            else:
                state[key] = value
        return state

    return state, load_state, update_state


def _stubbed_env(monkeypatch, cfg, ap_ready_returns):
    state, load_state, update_state = _state_helpers()

    monkeypatch.setattr(lifecycle, "load_state", load_state)
    monkeypatch.setattr(lifecycle, "update_state", update_state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "_repair_impl", lambda correlation_id="repair": state)
    monkeypatch.setattr(lifecycle, "_maybe_set_regdom", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "get_adapters",
        lambda: {
            "recommended": "wlan0",
            "adapters": [
                {
                    "ifname": "wlan0",
                    "supports_ap": True,
                    "supports_6ghz": True,
                    "phy": "phy0",
                }
            ],
        },
    )

    calls = []

    def start_engine(cmd, firewalld_cfg=None, early_fail_window_s=1.0):
        calls.append(cmd)
        return SimpleNamespace(
            ok=True,
            pid=123,
            exit_code=None,
            stdout_tail=[],
            stderr_tail=[],
            error=None,
            cmd=cmd,
            started_ts=123456,
        )

    monkeypatch.setattr(lifecycle, "start_engine", start_engine)
    monkeypatch.setattr(lifecycle, "stop_engine", lambda **_kwargs: (True, 0, [], [], None))

    ap_iter = iter(ap_ready_returns)
    monkeypatch.setattr(lifecycle, "_wait_for_ap_ready", lambda *_args, **_kwargs: next(ap_iter))

    def _build_engine_cmd(band, **kwargs):
        return build_cmd_nat(
            ap_ifname=kwargs.get("ap_ifname") or "wlan0",
            ssid=kwargs.get("ssid") or cfg.get("ssid", "Test"),
            passphrase=kwargs.get("passphrase") or cfg.get("wpa2_passphrase", "password123"),
            band=band,
            ap_security=str(cfg.get("ap_security", "wpa2")).lower(),
            country=kwargs.get("country"),
            channel=kwargs.get("channel"),
            no_virt=kwargs.get("no_virt", False),
            debug=kwargs.get("debug", False),
            wifi6=kwargs.get("wifi6", True),
            gateway_ip=kwargs.get("gateway_ip"),
            dhcp_start_ip=kwargs.get("dhcp_start_ip"),
            dhcp_end_ip=kwargs.get("dhcp_end_ip"),
            dhcp_dns=kwargs.get("dhcp_dns"),
            enable_internet=kwargs.get("enable_internet", True),
            channel_width=kwargs.get("channel_width", "auto"),
            beacon_interval=kwargs.get("beacon_interval", 50),
            dtim_period=kwargs.get("dtim_period", 1),
            short_guard_interval=kwargs.get("short_guard_interval", True),
            tx_power=kwargs.get("tx_power"),
        )

    monkeypatch.setattr(
        lifecycle,
        "build_cmd_6ghz",
        lambda **kwargs: _build_engine_cmd("6ghz", **kwargs),
    )
    monkeypatch.setattr(
        lifecycle,
        "build_cmd",
        lambda **kwargs: _build_engine_cmd(str(kwargs.get("band_preference") or "5ghz"), **kwargs),
    )

    return state, calls


def _bands_from_calls(calls):
    bands = []
    for call in calls:
        if "--band" not in call:
            raise AssertionError(f"missing --band in call: {call}")
        band_index = call.index("--band")
        if band_index + 1 >= len(call):
            raise AssertionError(f"missing band value in call: {call}")
        bands.append(call[band_index + 1])
    return bands


def _channel_width_for_band(calls, band):
    for call in calls:
        if "--band" not in call:
            continue
        band_index = call.index("--band")
        if band_index + 1 >= len(call):
            continue
        if call[band_index + 1] != band:
            continue
        if "--channel-width" not in call:
            raise AssertionError(f"missing --channel-width in call: {call}")
        width_index = call.index("--channel-width")
        if width_index + 1 >= len(call):
            raise AssertionError(f"missing channel-width value in call: {call}")
        return call[width_index + 1]
    raise AssertionError(f"missing call for band {band}")


def test_fallback_chain_6_to_5_to_2_4(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "6ghz",
        "ap_security": "wpa3_sae",
        "fallback_channel_2g": 6,
        "ap_ready_timeout_s": 0.1,
    }
    state, calls = _stubbed_env(
        monkeypatch,
        cfg,
        [
            None,
            None,
            lifecycle.APReadyInfo(
                ifname="ap0",
                phy="phy0",
                ssid="Test",
                freq_mhz=2412,
                channel=1,
                channel_width_mhz=None,
            ),
        ],
    )

    res = lifecycle._start_hotspot_impl(correlation_id="t1")

    assert res.code == "started_with_fallback"
    bands = _bands_from_calls(calls)
    assert bands == ["6ghz", "5ghz", "2.4ghz"]
    assert _channel_width_for_band(calls, "2.4ghz") == "20"
    assert state["band"] == "2.4ghz"
    assert "fallback_to_5ghz" in state["warnings"]
    assert "fallback_to_2_4ghz" in state["warnings"]


def test_fallback_chain_5_to_2_4(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "5ghz",
        "fallback_channel_2g": 6,
        "ap_ready_timeout_s": 0.1,
    }
    state, calls = _stubbed_env(
        monkeypatch,
        cfg,
        [
            None,
            lifecycle.APReadyInfo(
                ifname="ap0",
                phy="phy0",
                ssid="Test",
                freq_mhz=2412,
                channel=1,
                channel_width_mhz=None,
            ),
        ],
    )

    res = lifecycle._start_hotspot_impl(correlation_id="t2")

    assert res.code == "started_with_fallback"
    bands = _bands_from_calls(calls)
    assert bands == ["5ghz", "2.4ghz"]
    assert _channel_width_for_band(calls, "2.4ghz") == "20"
    assert state["band"] == "2.4ghz"
    assert "fallback_to_2_4ghz" in state["warnings"]


def test_no_fallback_for_2_4(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "2.4ghz",
        "ap_ready_timeout_s": 0.1,
    }
    state, calls = _stubbed_env(monkeypatch, cfg, [None])

    res = lifecycle._start_hotspot_impl(correlation_id="t3")

    assert res.code == "start_failed"
    bands = _bands_from_calls(calls)
    assert bands == ["2.4ghz"]
    assert state["phase"] == "error"
    assert state["last_error"] == "ap_ready_timeout"
