from copy import deepcopy
from types import SimpleNamespace

import vr_hotspotd.lifecycle as lifecycle
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

    monkeypatch.setattr(lifecycle, "build_cmd_6ghz", lambda **_kwargs: ["cmd", "6ghz"])
    monkeypatch.setattr(
        lifecycle,
        "build_cmd",
        lambda **kwargs: ["cmd", kwargs.get("band_preference")],
    )

    return state, calls


def test_fallback_chain_6_to_5_to_2_4(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "6ghz",
        "ap_security": "wpa3_sae",
        "fallback_channel_2g": 6,
        "ap_ready_timeout_s": 0.1,
    }
    state, calls = _stubbed_env(monkeypatch, cfg, [None, None, "ap0"])

    res = lifecycle._start_hotspot_impl(correlation_id="t1")

    assert res.code == "started_with_fallback"
    assert calls == [["cmd", "6ghz"], ["cmd", "5ghz"], ["cmd", "2.4ghz"]]
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
    state, calls = _stubbed_env(monkeypatch, cfg, [None, "ap0"])

    res = lifecycle._start_hotspot_impl(correlation_id="t2")

    assert res.code == "started_with_fallback"
    assert calls == [["cmd", "5ghz"], ["cmd", "2.4ghz"]]
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
    assert calls == [["cmd", "2.4ghz"]]
    assert state["phase"] == "error"
    assert state["last_error"] == "ap_ready_timeout"
