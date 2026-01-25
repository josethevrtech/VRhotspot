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


def _stubbed_env(monkeypatch, cfg, ap_ready_returns, fallback_candidates=None):
    state, load_state, update_state = _state_helpers()

    monkeypatch.setattr(lifecycle, "load_state", load_state)
    monkeypatch.setattr(lifecycle, "update_state", update_state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "_repair_impl", lambda correlation_id="repair": state)
    monkeypatch.setattr(lifecycle, "_maybe_set_regdom", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(lifecycle, "_iw_dev_info", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(lifecycle, "_nm_interference_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "is_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle.system_tuning, "apply_pre", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.os_release, "read_os_release", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(lifecycle.os_release, "apply_platform_overrides", lambda cfg, _info: (cfg, []))
    monkeypatch.setattr(lifecycle.os_release, "is_bazzite", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        lifecycle.wifi_probe,
        "detect_firewall_backends",
        lambda: {"selected_backend": "nftables"},
    )
    monkeypatch.setattr(
        lifecycle.wifi_probe,
        "probe",
        lambda *_args, **_kwargs: {
            "wifi": {
                "errors": [],
                "warnings": [],
                "counts": {"dfs": 0},
                "candidates": [
                    {
                        "band": 5,
                        "width": 80,
                        "primary_channel": 36,
                        "center_channel": 42,
                        "country": "US",
                        "flags": ["non_dfs"],
                        "rationale": "test",
                    }
                ],
            }
        },
    )
    if fallback_candidates is not None:
        monkeypatch.setattr(
            lifecycle.wifi_probe,
            "probe_5ghz_40",
            lambda *_args, **_kwargs: {"candidates": fallback_candidates},
        )

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
                    "supports_5ghz": True,
                    "supports_80mhz": True,
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
    monkeypatch.setattr(lifecycle, "build_cmd", lambda **_kwargs: ["cmd", "5ghz"])

    ap_iter = iter(ap_ready_returns)
    monkeypatch.setattr(lifecycle, "_wait_for_ap_ready", lambda *_args, **_kwargs: next(ap_iter))

    return state, calls


def test_fail_closed_no_fallback_for_5ghz(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "5ghz",
        "ap_ready_timeout_s": 0.1,
        "allow_fallback_40mhz": False,
    }
    state, calls = _stubbed_env(monkeypatch, cfg, [None])

    res = lifecycle._start_hotspot_impl(correlation_id="t1")

    assert res.code == "start_failed"
    assert len(calls) == 1
    assert state["phase"] == "error"
    assert state["last_error"] == "ap_start_timed_out"
    assert len(state["attempts"]) == 1
    assert state["attempts"][0]["failure_reason"] == "ap_start_timed_out"


def test_pro_mode_allows_40mhz_fallback(monkeypatch):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "5ghz",
        "ap_ready_timeout_s": 0.1,
        "allow_fallback_40mhz": True,
    }
    fallback_candidates = [
        {
            "band": 5,
            "width": 40,
            "primary_channel": 36,
            "center_channel": 38,
            "country": "US",
            "flags": ["non_dfs"],
            "rationale": "test40",
        }
    ]
    ap_ready = [
        None,
        lifecycle.APReadyInfo(
            ifname="ap0",
            phy="phy0",
            ssid="Test",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=40,
        ),
    ]
    state, calls = _stubbed_env(monkeypatch, cfg, ap_ready, fallback_candidates=fallback_candidates)

    res = lifecycle._start_hotspot_impl(correlation_id="t2")

    assert res.code == "started_with_fallback"
    assert len(calls) == 2
    assert state["mode"] == "fallback"
    assert state["fallback_reason"] == "pro_mode_40mhz"
    assert state["channel_width_mhz"] == 40
