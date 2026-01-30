import unittest
from unittest.mock import patch

import vr_hotspotd.lifecycle as lifecycle


class TestApReadyFallback(unittest.TestCase):
    def test_stdout_ap_enabled_marks_ready(self):
        with (
            patch.object(lifecycle, "_iw_dev_dump", return_value=""),
            patch.object(lifecycle, "_select_ap_from_iw", return_value=None),
            patch.object(lifecycle, "_hostapd_ready", return_value=False),
            patch.object(lifecycle, "get_tails", return_value=(["wlan0: AP-ENABLED"], "")),
            patch.object(lifecycle, "_iw_dev_info", return_value=""),
            patch.object(lifecycle, "_infer_ap_ifname_from_conf", return_value=None),
            patch.object(lifecycle, "update_state", return_value={}),
        ):
            ap = lifecycle._wait_for_ap_ready(
                target_phy="phy0",
                timeout_s=0.1,
                poll_s=0.01,
                ssid="TestNet",
                adapter_ifname="wlan0",
                expected_ap_ifname=None,
                capture=None,
            )
            self.assertIsNotNone(ap)
            self.assertEqual(ap.ifname, "wlan0")

    def test_stdout_created_sets_ap_interface(self):
        with (
            patch.object(lifecycle, "_iw_dev_dump", return_value=""),
            patch.object(lifecycle, "_select_ap_from_iw", return_value=None),
            patch.object(lifecycle, "_hostapd_ready", return_value=False),
            patch.object(lifecycle, "get_tails", return_value=(["x0wlan1 created"], "")),
            patch.object(lifecycle, "_iw_dev_info", return_value=""),
            patch.object(lifecycle, "_infer_ap_ifname_from_conf", return_value=None),
            patch.object(lifecycle.time, "sleep", return_value=None),
            patch.object(lifecycle, "update_state") as update_state,
        ):
            ap = lifecycle._wait_for_ap_ready(
                target_phy="phy0",
                timeout_s=0.05,
                poll_s=0.01,
                ssid="TestNet",
                adapter_ifname="wlan0",
                expected_ap_ifname=None,
                capture=None,
            )
            self.assertIsNone(ap)
            self.assertTrue(
                any(call.kwargs.get("ap_interface") == "x0wlan1" for call in update_state.call_args_list)
            )
