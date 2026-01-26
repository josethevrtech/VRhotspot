
import sys
import os
import unittest
from unittest.mock import MagicMock, patch


# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

# Ensure fresh start
if "vr_hotspotd.lifecycle" in sys.modules:
    del sys.modules["vr_hotspotd.lifecycle"]

class Test80MHzEnforcement(unittest.TestCase):

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle.build_cmd")
    @patch("vr_hotspotd.lifecycle.build_cmd_nat")
    @patch("vr_hotspotd.lifecycle.start_engine")
    @patch("vr_hotspotd.lifecycle._wait_for_ap_ready")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    @patch("vr_hotspotd.config.write_config_file")
    def test_usb_adapter_enforces_channel_36(
        self,
        mock_write_config,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_wait_ap,
        mock_start_engine,
        mock_build_cmd_nat,
        mock_build_cmd,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        # Setup Mocks
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan0",
                    "phy": "phy0",
                    "bus": "pci",
                    "supports_ap": False,
                    "supports_5ghz": True,
                    "supports_2ghz": True
                },
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True, # Supports VHT80 implicitly via band support
                    "supports_2ghz": True,
                    "supports_6ghz": False,
                    "supports_80mhz": True
                }
            ],
            "recommended": "wlan1"
        }

        mock_load_config.return_value = {"wpa2_passphrase": "password123"}
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"} # Static return, but inspecting calls works
        
        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.exit_code = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []
        mock_res.error = None
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_start_engine.return_value = mock_res
        
        # Mock AP Ready
        mock_wait_ap.return_value = MagicMock(
            ifname="wlan1",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )

        from vr_hotspotd import lifecycle
        probe_payload = {
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
        }

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=True), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle._nm_interference_reason", return_value=None), \
             patch("vr_hotspotd.lifecycle._nm_gate_check", return_value=None), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            try:
                res = lifecycle.start_hotspot()
                if res.code != "started":
                     self.fail(f"start_hotspot failed with code {res.code}")
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.fail(f"start_hotspot failed with exception: {e}")

        # Verify build_cmd was called with channel 36
        current_call = mock_build_cmd.call_args
        if not current_call:
             self.fail("build_cmd was not called")
        
        kwargs = current_call.kwargs
        
        self.assertEqual(kwargs.get("ap_ifname"), "wlan1", "Should have selected USB adapter wlan1")
        self.assertEqual(kwargs.get("channel"), 36, "Should have enforced Channel 36")
        self.assertEqual(kwargs.get("band_preference"), "5ghz", "Should be 5GHz band")

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle.build_cmd")
    @patch("vr_hotspotd.lifecycle.start_engine")
    @patch("vr_hotspotd.lifecycle._wait_for_ap_ready")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    @patch("vr_hotspotd.config.write_config_file")
    def test_pci_adapter_does_not_enforce_channel_36(
        self,
        mock_write_config,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_wait_ap,
        mock_start_engine,
        mock_build_cmd,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        # Setup Mocks
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan0",
                    "phy": "phy0",
                    "bus": "pci",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_2ghz": True,
                    "supports_80mhz": True
                }
            ],
            "recommended": "wlan0"
        }

        mock_load_config.return_value = {"wpa2_passphrase": "password123"}
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}
        mock_res = MagicMock()
        mock_res.ok = True
        mock_start_engine.return_value = mock_res
        mock_res.pid = 123
        mock_res.exit_code = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []
        mock_res.error = None
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_wait_ap.return_value = MagicMock(
            ifname="wlan0",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )
        
        from vr_hotspotd import lifecycle

        probe_payload = {
            "wifi": {
                "errors": [],
                "warnings": [],
                "counts": {"dfs": 0},
                "candidates": [
                    {
                        "band": 5,
                        "width": 80,
                        "primary_channel": 40,
                        "center_channel": 46,
                        "country": "US",
                        "flags": ["non_dfs"],
                        "rationale": "test",
                    }
                ],
            }
        }

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=True), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle._nm_interference_reason", return_value=None), \
             patch("vr_hotspotd.lifecycle._nm_gate_check", return_value=None), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            try:
                lifecycle.start_hotspot()
            except:
                pass

        current_call = mock_build_cmd.call_args
        if not current_call:
             self.fail("build_cmd was not called")
        
        kwargs = current_call.kwargs
        
        self.assertEqual(kwargs.get("ap_ifname"), "wlan0")
        self.assertEqual(kwargs.get("channel"), 40)


    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle.build_cmd")
    @patch("vr_hotspotd.lifecycle.start_engine")
    @patch("vr_hotspotd.lifecycle._wait_for_ap_ready")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    @patch("vr_hotspotd.config.write_config_file")
    def test_manual_usb_selection_enforces_channel_36(
        self,
        mock_write_config,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_wait_ap,
        mock_start_engine,
        mock_build_cmd,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        # Setup Mocks
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan0",
                    "phy": "phy0",
                    "bus": "pci",
                    "supports_ap": False,
                    "supports_5ghz": True
                },
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": True
                }
            ],
            "recommended": "wlan1"
        }

        # User MANUAL selection of wlan1
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "ap_adapter": "wlan1" 
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}
        
        mock_res = MagicMock()
        mock_res.ok = True
        mock_start_engine.return_value = mock_res
        mock_res.pid = 123
        mock_res.exit_code = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []
        mock_res.error = None
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_wait_ap.return_value = MagicMock(
            ifname="wlan1",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )

        from vr_hotspotd import lifecycle
        probe_payload = {
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
        }

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=True), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle._nm_interference_reason", return_value=None), \
             patch("vr_hotspotd.lifecycle._nm_gate_check", return_value=None), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            try:
                lifecycle.start_hotspot()
            except:
                pass

        # Verify build_cmd was called with channel 36
        current_call = mock_build_cmd.call_args
        if not current_call:
             self.fail("build_cmd was not called")
        
        kwargs = current_call.kwargs
        
        self.assertEqual(kwargs.get("ap_ifname"), "wlan1")
        self.assertEqual(kwargs.get("channel"), 36, "Manual USB selection should still enforce Channel 36")

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle.build_cmd")
    @patch("vr_hotspotd.lifecycle.start_engine")
    @patch("vr_hotspotd.lifecycle._wait_for_ap_ready")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    @patch("vr_hotspotd.config.write_config_file")
    def test_adapter_without_80mhz_is_rejected(
        self,
        mock_write_config,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_wait_ap,
        mock_start_engine,
        mock_build_cmd,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        
        # Mock adapter without 80MHz support
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan_old",
                    "phy": "phy0",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": False
                }
            ],
            "recommended": "wlan_old"
        }

        mock_load_config.return_value = {"wpa2_passphrase": "password123", "ap_adapter": "wlan_old"}
        mock_load_state.return_value = {"phase": "stopped"}
        
        # Expect failure
        # Expect result code 'start_failed' because start_hotspot catches exceptions
        from vr_hotspotd import lifecycle
        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}):
            res = lifecycle.start_hotspot()
        self.assertEqual(res.code, "start_failed")
        
        # Verify update_state was called with the error
        # Expected call: update_state(phase='failed', last_error='adapter_lacks_80mhz_support...')
        found_error = False
        for call in mock_update_state.call_args_list:
            # phase can be 'error' or 'failed' depending on implementation details, check both or loose check
            p = call.kwargs.get("phase")
            if p in ("failed", "error"):
                err = call.kwargs.get("last_error", "")
                if "adapter_lacks_80mhz_support" in err:
                    found_error = True
                    break
        
        if not found_error:
            self.fail(f"Did not find update_state call with expected error. Calls: {mock_update_state.call_args_list}")
        
        # Verify engine was NOT started
        self.assertFalse(mock_start_engine.called)

if __name__ == "__main__":
    unittest.main()
