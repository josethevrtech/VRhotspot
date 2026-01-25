from copy import deepcopy
from types import SimpleNamespace

import pytest

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


def _stubbed_env(monkeypatch, cfg, supports_wifi6):
    state, load_state, update_state = _state_helpers()

    monkeypatch.setattr(lifecycle, "load_state", load_state)
    monkeypatch.setattr(lifecycle, "update_state", update_state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "_repair_impl", lambda correlation_id="repair": state)
    monkeypatch.setattr(lifecycle, "_maybe_set_regdom", lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(lifecycle, "_iw_dev_info", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(lifecycle, "_nm_interference_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "is_running", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "get_adapters",
        lambda: {
            "recommended": "wlan0",
            "adapters": [
                {
                    "ifname": "wlan0",
                    "supports_ap": True,
                    "supports_6ghz": False,
                    "supports_5ghz": True,
                    "supports_80mhz": True,
                    "supports_wifi6": supports_wifi6,
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
    monkeypatch.setattr(
        lifecycle,
        "_wait_for_ap_ready",
        lambda *_args, **_kwargs: lifecycle.APReadyInfo(
            ifname="ap0",
            phy="phy0",
            ssid="Test",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        ),
    )
    monkeypatch.setattr(
        lifecycle.preflight,
        "run",
        lambda *_args, **_kwargs: {"errors": [], "warnings": [], "details": {"hostapd": {"he": None}}},
    )

    return state, calls


@pytest.mark.parametrize(
    "supports_wifi6,wifi6_setting,expect_flag,expect_warning",
    [
        (False, "auto", False, False),
        (False, True, False, True),
        (True, "auto", True, False),
        (True, True, True, False),
    ],
)
def test_wifi6_flag_gated_by_adapter(monkeypatch, supports_wifi6, wifi6_setting, expect_flag, expect_warning):
    cfg = {
        "ssid": "Test",
        "wpa2_passphrase": "password123",
        "band_preference": "5ghz",
        "ap_ready_timeout_s": 0.1,
        "wifi6": wifi6_setting,
    }
    state, calls = _stubbed_env(monkeypatch, cfg, supports_wifi6)

    res = lifecycle._start_hotspot_impl(correlation_id="t1")

    assert res.code == "started"
    assert len(calls) == 1
    if expect_flag:
        assert "--wifi6" in calls[0]
    else:
        assert "--wifi6" not in calls[0]
    if expect_warning:
        assert "wifi6_not_supported_on_adapter" in state["warnings"]
    else:
        assert "wifi6_not_supported_on_adapter" not in state["warnings"]
