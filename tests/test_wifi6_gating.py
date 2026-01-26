from copy import deepcopy
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


@contextmanager
def _stubbed_env(cfg, supports_wifi6):
    state, load_state, update_state = _state_helpers()
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

    with ExitStack() as stack:
        stack.enter_context(patch.object(lifecycle, "load_state", load_state))
        stack.enter_context(patch.object(lifecycle, "update_state", update_state))
        stack.enter_context(patch.object(lifecycle, "load_config", lambda: cfg))
        stack.enter_context(patch.object(lifecycle, "ensure_config_file", lambda: None))
        stack.enter_context(patch.object(lifecycle, "_repair_impl", lambda correlation_id="repair": state))
        stack.enter_context(patch.object(lifecycle, "_maybe_set_regdom", lambda *_args, **_kwargs: None))
        stack.enter_context(
            patch.object(
                lifecycle.wifi_probe,
                "detect_firewall_backends",
                lambda: {"selected_backend": "nftables"},
            )
        )
        stack.enter_context(
            patch.object(
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
        )
        stack.enter_context(patch.object(lifecycle, "_iface_is_up", lambda *_args, **_kwargs: True))
        stack.enter_context(patch.object(lifecycle, "_iw_dev_info", lambda *_args, **_kwargs: ""))
        stack.enter_context(
            patch.object(lifecycle, "_nm_interference_reason", lambda *_args, **_kwargs: None)
        )
        stack.enter_context(patch.object(lifecycle, "_nm_gate_check", lambda *_args, **_kwargs: None))
        stack.enter_context(patch.object(lifecycle, "is_running", lambda *_args, **_kwargs: True))
        stack.enter_context(patch.object(lifecycle.time, "sleep", lambda *_args, **_kwargs: None))
        stack.enter_context(
            patch.object(
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
        )
        stack.enter_context(patch.object(lifecycle, "start_engine", start_engine))
        stack.enter_context(patch.object(lifecycle, "stop_engine", lambda **_kwargs: (True, 0, [], [], None)))
        stack.enter_context(
            patch.object(
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
        )
        stack.enter_context(
            patch.object(
                lifecycle.preflight,
                "run",
                lambda *_args, **_kwargs: {
                    "errors": [],
                    "warnings": [],
                    "details": {"hostapd": {"he": None}},
                },
            )
        )
        yield state, calls


class TestWifi6Gating(unittest.TestCase):
    def test_wifi6_flag_gated_by_adapter(self):
        cases = [
            (False, "auto", False, False),
            (False, True, False, True),
            (True, "auto", True, False),
            (True, True, True, False),
        ]
        for supports_wifi6, wifi6_setting, expect_flag, expect_warning in cases:
            with self.subTest(
                supports_wifi6=supports_wifi6,
                wifi6_setting=wifi6_setting,
                expect_flag=expect_flag,
                expect_warning=expect_warning,
            ):
                cfg = {
                    "ssid": "Test",
                    "wpa2_passphrase": "password123",
                    "band_preference": "5ghz",
                    "ap_ready_timeout_s": 0.1,
                    "wifi6": wifi6_setting,
                }
                with _stubbed_env(cfg, supports_wifi6) as (state, calls):
                    res = lifecycle._start_hotspot_impl(correlation_id="t1")

                self.assertEqual(res.code, "started")
                self.assertEqual(len(calls), 1)
                if expect_flag:
                    self.assertIn("--wifi6", calls[0])
                else:
                    self.assertNotIn("--wifi6", calls[0])
                if expect_warning:
                    self.assertIn("wifi6_not_supported_on_adapter", state["warnings"])
                else:
                    self.assertNotIn("wifi6_not_supported_on_adapter", state["warnings"])
