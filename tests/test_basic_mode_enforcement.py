"""
Test Basic Mode 5GHz 80MHz enforcement and NM pre-start gate.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


class TestBasicModeEnforcement(unittest.TestCase):
    """Test Basic Mode VR enforcement: requires 5GHz and 80MHz adapter."""

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")  # Mock the NM gate
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_basic_mode_rejects_2_4ghz_band(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Basic Mode should reject 2.4GHz band selection."""
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = None  # NM gate passes
        
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_2ghz": True,
                    "supports_80mhz": True
                }
            ],
            "recommended": "wlan1"
        }

        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "2.4ghz"  # Trying to use 2.4GHz in Basic Mode
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}):
            res = lifecycle.start_hotspot(basic_mode=True)

        # Should fail with basic_mode_requires_5ghz
        self.assertEqual(res.code, "start_failed")
        
        # Verify update_state was called with the correct error
        found_error = False
        for call in mock_update_state.call_args_list:
            err = call.kwargs.get("last_error", "")
            if "basic_mode_requires_5ghz" in str(err):
                found_error = True
                break
        
        self.assertTrue(found_error, f"Expected basic_mode_requires_5ghz error. Calls: {mock_update_state.call_args_list}")

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_set_unmanaged")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_basic_mode_rejects_no_80mhz_adapter(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_nm_gate,
        mock_nm_set_unmanaged,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Basic Mode should reject adapters without 80MHz support."""
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = None  # NM gate passes
        
        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan_old",
                    "phy": "phy0",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": False  # No 80MHz support
                }
            ],
            "recommended": "wlan_old"
        }

        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz"
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}):
            res = lifecycle.start_hotspot(basic_mode=True)

        # Should fail
        self.assertEqual(res.code, "start_failed")
        
        # Verify update_state was called with 80MHz related error
        found_error = False
        for call in mock_update_state.call_args_list:
            err = call.kwargs.get("last_error", "")
            if "80mhz" in str(err).lower():
                found_error = True
                break
        
        self.assertTrue(found_error, f"Expected 80MHz requirement error. Calls: {mock_update_state.call_args_list}")


class TestNmPreStartGate(unittest.TestCase):
    """Test NetworkManager pre-start gate functionality."""

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_set_unmanaged")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_nm_gate_blocks_managed_interface(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_nm_gate,
        mock_nm_set_unmanaged,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """NM gate should block start if interface is managed by NetworkManager."""
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = "nm_interface_managed"  # NM gate fails
        mock_nm_set_unmanaged.return_value = (False, "not_root")
        
        mock_get_adapters.return_value = {
            "adapters": [
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

        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz"
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}):
            res = lifecycle.start_hotspot()

        # Should fail with nm_interface_managed
        self.assertEqual(res.code, "start_failed")
        
        # Verify update_state was called with the NM error
        found_error = False
        for call in mock_update_state.call_args_list:
            err = call.kwargs.get("last_error", "")
            if "nm_interface_managed" in str(err):
                found_error = True
                break
        
        self.assertTrue(found_error, f"Expected nm_interface_managed error. Calls: {mock_update_state.call_args_list}")

        from vr_hotspotd import wifi_probe

        detail = None
        for call in mock_update_state.call_args_list:
            if call.kwargs.get("last_error") == "nm_interface_managed":
                detail = call.kwargs.get("last_error_detail")
                break

        self.assertIsInstance(detail, dict, f"Expected last_error_detail dict. Calls: {mock_update_state.call_args_list}")
        self.assertEqual(detail.get("code"), "nm_interface_managed")
        self.assertEqual(detail.get("remediation"), wifi_probe.ERROR_REMEDIATIONS["nm_interface_managed"])
        self.assertEqual(detail.get("context", {}).get("interface"), "wlan1")
        self.assertTrue(detail.get("remediation_attempted"), f"Expected remediation_attempted=True, got {detail}")
        self.assertEqual(detail.get("remediation_error"), "not_root")
        mock_nm_set_unmanaged.assert_called_with("wlan1")

    @patch("vr_hotspotd.lifecycle._nm_is_running")
    @patch("vr_hotspotd.lifecycle._nm_device_state")
    def test_nm_gate_check_returns_none_when_unmanaged(
        self,
        mock_device_state,
        mock_nm_running
    ):
        """NM gate should return None when interface is unmanaged."""
        mock_nm_running.return_value = True
        mock_device_state.return_value = "unmanaged"

        from vr_hotspotd.lifecycle import _nm_gate_check
        
        result = _nm_gate_check("wlan1")
        self.assertIsNone(result)

    @patch("vr_hotspotd.lifecycle._nm_is_running")
    @patch("vr_hotspotd.lifecycle._nm_device_state")
    def test_nm_gate_check_returns_none_when_disconnected(
        self,
        mock_device_state,
        mock_nm_running
    ):
        """NM gate should not block disconnected interfaces."""
        mock_nm_running.return_value = True
        mock_device_state.return_value = "disconnected"

        from vr_hotspotd.lifecycle import _nm_gate_check

        result = _nm_gate_check("wlan1")
        self.assertIsNone(result)

    @patch("vr_hotspotd.lifecycle._nm_is_running")
    @patch("vr_hotspotd.lifecycle._nm_device_state")
    def test_nm_gate_check_returns_error_when_connected(
        self,
        mock_device_state,
        mock_nm_running
    ):
        """NM gate should return error when interface is connected/managed."""
        mock_nm_running.return_value = True
        mock_device_state.return_value = "connected"

        from vr_hotspotd.lifecycle import _nm_gate_check
        
        result = _nm_gate_check("wlan1")
        self.assertEqual(result, "nm_interface_managed")


class TestNmAutoRemediation(unittest.TestCase):
    """Test NetworkManager auto-remediation on start."""

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.lifecycle._nm_set_unmanaged")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_nm_auto_remediation_applies_to_basic_and_non_basic(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_nm_gate,
        mock_nm_set_unmanaged,
        mock_start_5ghz_strict,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config,
    ):
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}

        gate_calls = {"count": 0}

        def gate_side_effect(*_args, **_kwargs):
            gate_calls["count"] += 1
            return "nm_interface_managed" if gate_calls["count"] % 2 == 1 else None

        mock_nm_gate.side_effect = gate_side_effect
        mock_nm_set_unmanaged.return_value = (True, None)

        from vr_hotspotd.lifecycle import LifecycleResult

        mock_start_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})

        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": True,
                }
            ],
            "recommended": "wlan1",
        }

        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            res = lifecycle.start_hotspot()
            self.assertEqual(res.code, "started")
            res_basic = lifecycle.start_hotspot(basic_mode=True)
            self.assertEqual(res_basic.code, "started")

        self.assertEqual(mock_nm_set_unmanaged.call_count, 2)
        for call in mock_nm_set_unmanaged.call_args_list:
            self.assertEqual(call.args[0], "wlan1")


class TestBasicModeFallbackBlocking(unittest.TestCase):
    """Test that Basic Mode blocks 40MHz fallback."""

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_pop_os")
    @patch("vr_hotspotd.os_release.is_cachyos")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_basic_mode_disables_fallback_40mhz(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_is_cachyos,
        mock_is_pop_os,
        mock_read_os_release,
        mock_5ghz_strict,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Basic Mode should disable 40MHz fallback even if config allows it."""
        mock_is_bazzite.return_value = False
        mock_is_pop_os.return_value = False
        mock_is_cachyos.return_value = True
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = None  # NM gate passes
        
        # Mock successful start via strict path
        from vr_hotspotd.lifecycle import LifecycleResult
        mock_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})
        
        mock_get_adapters.return_value = {
            "adapters": [
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

        # Config HAS allow_fallback_40mhz=True, but Basic Mode should override it
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
            "allow_fallback_40mhz": True  # This should be disabled in Basic Mode
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            lifecycle.start_hotspot(basic_mode=True)

        # Verify _start_hotspot_5ghz_strict was called with allow_fallback_40mhz=False
        self.assertTrue(mock_5ghz_strict.called, "_start_hotspot_5ghz_strict should have been called")
        call_kwargs = mock_5ghz_strict.call_args.kwargs
        self.assertFalse(
            call_kwargs.get("allow_fallback_40mhz", True),
            f"Basic Mode should have set allow_fallback_40mhz=False, got: {call_kwargs}"
        )
        self.assertGreater(
            float(call_kwargs.get("iface_up_grace_s", 0.0)),
            0.0,
            f"CachyOS should enable iface_up_grace_s, got: {call_kwargs}",
        )

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_pop_os")
    @patch("vr_hotspotd.os_release.is_cachyos")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_non_basic_mode_preserves_fallback_40mhz(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_is_cachyos,
        mock_is_pop_os,
        mock_read_os_release,
        mock_5ghz_strict,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Non-Basic Mode (basic_mode=False) should preserve allow_fallback_40mhz=True from config."""
        mock_is_bazzite.return_value = False
        mock_is_pop_os.return_value = False
        mock_is_cachyos.return_value = True
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = None
        
        from vr_hotspotd.lifecycle import LifecycleResult
        mock_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})
        
        mock_get_adapters.return_value = {
            "adapters": [
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

        # Config has allow_fallback_40mhz=True - should be preserved in non-Basic mode
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
            "allow_fallback_40mhz": True
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            # Call WITHOUT basic_mode (defaults to False)
            lifecycle.start_hotspot()

        # Verify _start_hotspot_5ghz_strict was called with allow_fallback_40mhz=True (preserved)
        self.assertTrue(mock_5ghz_strict.called, "_start_hotspot_5ghz_strict should have been called")
        call_kwargs = mock_5ghz_strict.call_args.kwargs
        self.assertTrue(
            call_kwargs.get("allow_fallback_40mhz", False),
            f"Non-Basic Mode should preserve allow_fallback_40mhz=True, got: {call_kwargs}"
        )
        self.assertGreater(
            float(call_kwargs.get("iface_up_grace_s", 0.0)),
            0.0,
            f"CachyOS should enable iface_up_grace_s, got: {call_kwargs}",
        )

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_pop_os")
    @patch("vr_hotspotd.os_release.is_cachyos")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_non_cachyos_keeps_iface_up_grace_disabled(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_is_cachyos,
        mock_is_pop_os,
        mock_read_os_release,
        mock_5ghz_strict,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Non-CachyOS should keep interface-up grace disabled."""
        mock_is_bazzite.return_value = False
        mock_is_pop_os.return_value = False
        mock_is_cachyos.return_value = False
        mock_read_os_release.return_value = {"ID": "arch"}
        mock_nm_gate.return_value = None

        from vr_hotspotd.lifecycle import LifecycleResult
        mock_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})

        mock_get_adapters.return_value = {
            "adapters": [
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
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            lifecycle.start_hotspot()

        self.assertTrue(mock_5ghz_strict.called, "_start_hotspot_5ghz_strict should have been called")
        call_kwargs = mock_5ghz_strict.call_args.kwargs
        self.assertEqual(
            float(call_kwargs.get("iface_up_grace_s", 0.0)),
            0.0,
            f"Non-CachyOS should keep iface_up_grace_s=0.0, got: {call_kwargs}",
        )
        self.assertEqual(
            float(call_kwargs.get("ap_ready_nohint_retry_s", 0.0)),
            0.0,
            f"Non-Pop should keep ap_ready_nohint_retry_s=0.0, got: {call_kwargs}",
        )
        self.assertFalse(
            bool(call_kwargs.get("pop_timeout_retry_no_virt", False)),
            f"Non-Pop should keep pop_timeout_retry_no_virt disabled, got: {call_kwargs}",
        )

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_pop_os")
    @patch("vr_hotspotd.os_release.is_cachyos")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_pop_os_enables_ap_ready_nohint_retry(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_is_cachyos,
        mock_is_pop_os,
        mock_read_os_release,
        mock_5ghz_strict,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Pop!_OS should enable no-hint AP-ready retry."""
        mock_is_bazzite.return_value = False
        mock_is_cachyos.return_value = False
        mock_is_pop_os.return_value = True
        mock_read_os_release.return_value = {"id": "pop", "id_like": "ubuntu debian"}
        mock_nm_gate.return_value = None

        from vr_hotspotd.lifecycle import LifecycleResult
        mock_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})

        mock_get_adapters.return_value = {
            "adapters": [
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
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
            "ap_ready_timeout_s": 14.0,
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            lifecycle.start_hotspot()

        self.assertTrue(mock_5ghz_strict.called, "_start_hotspot_5ghz_strict should have been called")
        call_kwargs = mock_5ghz_strict.call_args.kwargs
        self.assertGreater(
            float(call_kwargs.get("ap_ready_nohint_retry_s", 0.0)),
            0.0,
            f"Pop should set ap_ready_nohint_retry_s>0, got: {call_kwargs}",
        )
        self.assertTrue(
            bool(call_kwargs.get("pop_timeout_retry_no_virt", False)),
            f"Pop should enable pop_timeout_retry_no_virt, got: {call_kwargs}",
        )

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle._start_hotspot_5ghz_strict")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_pop_os")
    @patch("vr_hotspotd.os_release.is_cachyos")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    def test_pop_os_switches_to_hostapd_nat_when_iface_busy_prestart(
        self,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_is_cachyos,
        mock_is_pop_os,
        mock_read_os_release,
        mock_5ghz_strict,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config,
    ):
        """Pop!_OS should force hostapd_nat when prestart reports busy iface."""
        mock_is_bazzite.return_value = False
        mock_is_cachyos.return_value = False
        mock_is_pop_os.return_value = True
        mock_read_os_release.return_value = {"id": "pop", "id_like": "ubuntu debian"}
        mock_nm_gate.return_value = None

        from vr_hotspotd.lifecycle import LifecycleResult
        mock_5ghz_strict.return_value = LifecycleResult("started", {"phase": "running"})

        mock_get_adapters.return_value = {
            "adapters": [
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": True,
                }
            ],
            "recommended": "wlan1",
        }
        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}

        from vr_hotspotd import lifecycle

        with patch("vr_hotspotd.lifecycle._prepare_ap_interface", return_value=["ap_iface_not_up_prestart"]), \
             patch("vr_hotspotd.lifecycle._ensure_iface_up", return_value=False), \
             patch("vr_hotspotd.lifecycle.wifi_probe.detect_firewall_backends", return_value={"selected_backend": "nftables"}), \
             patch("vr_hotspotd.lifecycle.preflight.run", return_value={"errors": [], "warnings": [], "details": {}}), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_pre", return_value=({}, [])):
            lifecycle.start_hotspot()

        self.assertTrue(mock_5ghz_strict.called, "_start_hotspot_5ghz_strict should have been called")
        call_kwargs = mock_5ghz_strict.call_args.kwargs
        self.assertTrue(
            bool(call_kwargs.get("use_hostapd_nat", False)),
            f"Pop busy prestart should force use_hostapd_nat, got: {call_kwargs}",
        )


class TestPostStartWidthCheck(unittest.TestCase):
    """Test post-start width validation."""

    def test_attempt_start_candidate_fails_on_wrong_width(self):
        """_attempt_start_candidate should fail if width != required width."""
        from vr_hotspotd.lifecycle import _attempt_start_candidate, APReadyInfo
        from unittest.mock import MagicMock, patch
        
        # Mock start_engine to return success
        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_res.exit_code = None
        mock_res.error = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []
        
        # Mock AP info with width=40 (wrong width)
        mock_ap = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="TestSSID",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=40,  # Wrong width - should be 80!
        )
        
        with patch("vr_hotspotd.lifecycle.start_engine", return_value=mock_res), \
             patch("vr_hotspotd.lifecycle.update_state"), \
             patch("vr_hotspotd.lifecycle._wait_for_ap_ready", return_value=mock_ap), \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=True), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle._parse_iw_dev_info", return_value={"channel_width_mhz": 40}), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle.time.sleep"):
            
            ap_info, res, failure_code, failure_detail, _, _ = _attempt_start_candidate(
                cmd=["test"],
                firewalld_cfg={},
                target_phy="phy1",
                ap_ready_timeout_s=5.0,
                ssid="TestSSID",
                adapter_ifname="wlan1",
                expected_ap_ifname="wlan1",
                require_band="5ghz",
                require_width_mhz=80,  # Require 80MHz
            )
            
            # Should fail with width mismatch error
            self.assertIsNone(ap_info, "ap_info should be None when width check fails")
            self.assertEqual(failure_code, "hostapd_started_but_width_not_80")
            self.assertIn("width_mismatch:40", failure_detail)

    def test_attempt_start_candidate_iface_up_grace_allows_recovery(self):
        """iface_up_grace_s should allow transient not-up AP interfaces to recover."""
        from vr_hotspotd.lifecycle import _attempt_start_candidate, APReadyInfo
        from unittest.mock import MagicMock, patch

        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_res.exit_code = None
        mock_res.error = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []

        mock_ap = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="TestSSID",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )

        with patch("vr_hotspotd.lifecycle.start_engine", return_value=mock_res), \
             patch("vr_hotspotd.lifecycle.update_state"), \
             patch("vr_hotspotd.lifecycle._wait_for_ap_ready", return_value=mock_ap), \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=False), \
             patch("vr_hotspotd.lifecycle._ensure_iface_up_with_grace", return_value=True) as mock_iface_grace, \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle._parse_iw_dev_info", return_value={"channel_width_mhz": 80}), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle._nm_interference_reason", return_value=None), \
             patch("vr_hotspotd.lifecycle.time.sleep"):

            ap_info, _, failure_code, failure_detail, _, _ = _attempt_start_candidate(
                cmd=["test"],
                firewalld_cfg={},
                target_phy="phy1",
                ap_ready_timeout_s=5.0,
                ssid="TestSSID",
                adapter_ifname="wlan1",
                expected_ap_ifname="wlan1",
                require_band="5ghz",
                require_width_mhz=80,
                iface_up_grace_s=3.0,
            )

            self.assertIsNotNone(ap_info, "ap_info should be available after iface_up_grace recovery")
            self.assertIsNone(failure_code)
            self.assertIsNone(failure_detail)
            mock_iface_grace.assert_called_once_with("wlan1", grace_s=3.0)

    def test_attempt_start_candidate_nohint_retry_allows_recovery(self):
        """ap_ready_nohint_retry_s should retry AP-ready even without stdout hints."""
        from vr_hotspotd.lifecycle import _attempt_start_candidate, APReadyInfo
        from unittest.mock import MagicMock, patch

        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_res.exit_code = None
        mock_res.error = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []

        mock_ap = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="TestSSID",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )

        with patch("vr_hotspotd.lifecycle.start_engine", return_value=mock_res), \
             patch("vr_hotspotd.lifecycle.update_state"), \
             patch("vr_hotspotd.lifecycle.get_tails", return_value=([], [])), \
             patch("vr_hotspotd.lifecycle._stdout_has_ap_ready", return_value=False), \
             patch("vr_hotspotd.lifecycle._stdout_extract_ap_ifname", return_value=None), \
             patch("vr_hotspotd.lifecycle._wait_for_ap_ready", side_effect=[None, mock_ap]) as mock_wait, \
             patch("vr_hotspotd.lifecycle._iface_is_up", return_value=True), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=True), \
             patch("vr_hotspotd.lifecycle._parse_iw_dev_info", return_value={"channel_width_mhz": 80}), \
             patch("vr_hotspotd.lifecycle._iw_dev_info", return_value=""), \
             patch("vr_hotspotd.lifecycle._nm_interference_reason", return_value=None), \
             patch("vr_hotspotd.lifecycle.time.sleep"):

            ap_info, _, failure_code, failure_detail, _, _ = _attempt_start_candidate(
                cmd=["test"],
                firewalld_cfg={},
                target_phy="phy1",
                ap_ready_timeout_s=5.0,
                ssid="TestSSID",
                adapter_ifname="wlan1",
                expected_ap_ifname="wlan1",
                require_band="5ghz",
                require_width_mhz=80,
                ap_ready_nohint_retry_s=3.0,
            )

            self.assertIsNotNone(ap_info, "ap_info should be available after no-hint retry recovery")
            self.assertIsNone(failure_code)
            self.assertIsNone(failure_detail)
            self.assertEqual(mock_wait.call_count, 2)

    def test_attempt_start_candidate_classifies_iface_busy_from_tails(self):
        """Busy RTNETLINK tails should classify as hostapd_failed/iface_busy."""
        from vr_hotspotd.lifecycle import _attempt_start_candidate
        from unittest.mock import MagicMock, patch

        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_res.exit_code = None
        mock_res.error = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []

        busy_stderr = [
            "RTNETLINK answers: Device or resource busy",
            "ERROR: Failed bringing wlan1 up",
        ]

        with patch("vr_hotspotd.lifecycle.start_engine", return_value=mock_res), \
             patch("vr_hotspotd.lifecycle.update_state"), \
             patch("vr_hotspotd.lifecycle._wait_for_ap_ready", return_value=None), \
             patch("vr_hotspotd.lifecycle.get_tails", return_value=([], busy_stderr)), \
             patch("vr_hotspotd.lifecycle._stdout_has_ap_ready", return_value=False), \
             patch("vr_hotspotd.lifecycle._stdout_extract_ap_ifname", return_value=None), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=False):

            ap_info, _, failure_code, failure_detail, _, _ = _attempt_start_candidate(
                cmd=["test"],
                firewalld_cfg={},
                target_phy="phy1",
                ap_ready_timeout_s=5.0,
                ssid="TestSSID",
                adapter_ifname="wlan1",
                expected_ap_ifname="wlan1",
                require_band="5ghz",
                require_width_mhz=80,
            )

        self.assertIsNone(ap_info)
        self.assertEqual(failure_code, "hostapd_failed")
        self.assertEqual(failure_detail, "iface_busy")

    def test_attempt_start_candidate_classifies_open_files_error_as_iface_busy(self):
        """Virtual iface ENFILE path should classify as hostapd_failed/iface_busy."""
        from vr_hotspotd.lifecycle import _attempt_start_candidate
        from unittest.mock import MagicMock, patch

        mock_res = MagicMock()
        mock_res.ok = True
        mock_res.pid = 123
        mock_res.cmd = ["cmd"]
        mock_res.started_ts = 123456
        mock_res.exit_code = None
        mock_res.error = None
        mock_res.stdout_tail = []
        mock_res.stderr_tail = []

        with patch("vr_hotspotd.lifecycle.start_engine", return_value=mock_res), \
             patch("vr_hotspotd.lifecycle.update_state"), \
             patch("vr_hotspotd.lifecycle._wait_for_ap_ready", return_value=None), \
             patch(
                 "vr_hotspotd.lifecycle.get_tails",
                 return_value=(
                     [],
                     ["command failed: Too many open files in system (-23)"],
                 ),
             ), \
             patch("vr_hotspotd.lifecycle._stdout_has_ap_ready", return_value=False), \
             patch("vr_hotspotd.lifecycle._stdout_extract_ap_ifname", return_value=None), \
             patch("vr_hotspotd.lifecycle.is_running", return_value=False):

            ap_info, _, failure_code, failure_detail, _, _ = _attempt_start_candidate(
                cmd=["test"],
                firewalld_cfg={},
                target_phy="phy1",
                ap_ready_timeout_s=5.0,
                ssid="TestSSID",
                adapter_ifname="wlan1",
                expected_ap_ifname="wlan1",
                require_band="5ghz",
                require_width_mhz=80,
            )

        self.assertIsNone(ap_info)
        self.assertEqual(failure_code, "hostapd_failed")
        self.assertEqual(failure_detail, "iface_busy")

    def test_start_5ghz_strict_retries_no_virt_when_hostapd_nat_iface_busy(self):
        """When hostapd_nat busy-signals on virt iface, strict mode should retry with --no-virt."""
        from vr_hotspotd import lifecycle
        from vr_hotspotd.lifecycle import APReadyInfo

        candidate = {
            "band": 5,
            "width": 80,
            "primary_channel": 36,
            "center_channel": 42,
            "country": "US",
            "flags": ["non_dfs"],
            "rationale": "test",
        }
        probe_payload = {
            "wifi": {
                "errors": [],
                "warnings": [],
                "counts": {"dfs": 0},
                "candidates": [candidate],
            }
        }

        state = {}

        def fake_update_state(**kwargs):
            state.update(kwargs)
            return dict(state)

        first_res = MagicMock()
        first_res.pid = 1001
        first_res.cmd = ["cmd-virt"]
        first_res.started_ts = 123
        first_res.exit_code = 1
        first_res.error = "engine_exited_early: rc=1"
        first_res.stdout_tail = []
        first_res.stderr_tail = ["RTNETLINK answers: Device or resource busy"]

        second_res = MagicMock()
        second_res.pid = 1002
        second_res.cmd = ["cmd-no-virt"]
        second_res.started_ts = 124
        second_res.exit_code = None
        second_res.error = None
        second_res.stdout_tail = []
        second_res.stderr_tail = []

        ap_ready = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="VR-Hotspot",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        )

        call_count = {"n": 0}

        def fake_attempt_start_candidate(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (
                    None,
                    first_res,
                    "hostapd_failed",
                    "engine_exited_early: rc=1",
                    [],
                    ["command failed: Too many open files in system (-23)"],
                )
            return ap_ready, second_res, None, None, [], []

        with patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._attempt_start_candidate", side_effect=fake_attempt_start_candidate), \
             patch("vr_hotspotd.lifecycle.build_cmd_nat", side_effect=lambda **kwargs: [f"no_virt={kwargs.get('no_virt')}"]) as mock_build_nat, \
             patch("vr_hotspotd.lifecycle._prepare_ap_interface", return_value=[]), \
             patch("vr_hotspotd.lifecycle._kill_runtime_processes"), \
             patch("vr_hotspotd.lifecycle._remove_conf_dirs", return_value=[]), \
             patch("vr_hotspotd.lifecycle._cleanup_virtual_ap_ifaces", return_value=[]), \
             patch("vr_hotspotd.lifecycle.update_state", side_effect=fake_update_state), \
             patch("vr_hotspotd.lifecycle._collect_affinity_pids", return_value=[]), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_runtime", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle.network_tuning.apply", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle._watchdog_enabled", return_value=False), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            res = lifecycle._start_hotspot_5ghz_strict(
                cfg={"watchdog_enable": False},
                inv={"adapters": []},
                ap_ifname="wlan1",
                target_phy="phy1",
                ssid="VR-Hotspot",
                passphrase="password123",
                country="US",
                ap_security="wpa2",
                ap_ready_timeout_s=5.0,
                optimized_no_virt=False,
                debug=False,
                enable_internet=True,
                bridge_mode=False,
                bridge_name=None,
                bridge_uplink=None,
                gateway_ip="192.168.68.1",
                dhcp_start_ip="192.168.68.10",
                dhcp_end_ip="192.168.68.250",
                dhcp_dns="gateway",
                effective_wifi6=False,
                tuning_state={},
                start_warnings=[],
                fw_cfg={},
                firewall_backend="nftables",
                use_hostapd_nat=True,
                correlation_id="test-cid",
                enforced_channel_5g=None,
                allow_fallback_40mhz=False,
                allow_dfs_channels=False,
            )

        self.assertEqual(res.code, "started")
        self.assertGreaterEqual(mock_build_nat.call_count, 2)
        self.assertFalse(bool(mock_build_nat.call_args_list[0].kwargs.get("no_virt", True)))
        self.assertTrue(bool(mock_build_nat.call_args_list[1].kwargs.get("no_virt", False)))

    def test_start_5ghz_strict_bazzite_no_virt_driver_mode_error_retries_with_virt(self):
        """Bazzite no-virt driver-mode failures should retry the same candidate with a virtual AP iface."""
        from vr_hotspotd import lifecycle
        from vr_hotspotd.lifecycle import APReadyInfo

        candidate = {
            "band": 5,
            "width": 80,
            "primary_channel": 149,
            "center_channel": 155,
            "country": "US",
            "flags": ["non_dfs"],
            "rationale": "test",
        }
        probe_payload = {
            "wifi": {
                "errors": [],
                "warnings": [],
                "counts": {"dfs": 0},
                "candidates": [candidate],
            }
        }

        state = {}

        def fake_update_state(**kwargs):
            state.update(kwargs)
            return dict(state)

        first_res = MagicMock()
        first_res.pid = 3001
        first_res.cmd = ["cmd-no-virt"]
        first_res.started_ts = 211
        first_res.exit_code = 245
        first_res.error = "engine_exited_early: rc=245"
        first_res.stdout_tail = []
        first_res.stderr_tail = []

        second_res = MagicMock()
        second_res.pid = 3002
        second_res.cmd = ["cmd-virt"]
        second_res.started_ts = 212
        second_res.exit_code = None
        second_res.error = None
        second_res.stdout_tail = []
        second_res.stderr_tail = []

        ap_ready = APReadyInfo(
            ifname="x0wlan1",
            phy="phy1",
            ssid="VR-Hotspot",
            freq_mhz=5745,
            channel=149,
            channel_width_mhz=80,
        )

        call_count = {"n": 0}

        def fake_attempt_start_candidate(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (
                    None,
                    first_res,
                    "hostapd_failed",
                    "engine_exited_early: rc=245",
                    [],
                    [
                        "nl80211: kernel reports: Registration to specific type not supported",
                        "nl80211: Could not configure driver mode",
                    ],
                )
            return ap_ready, second_res, None, None, [], []

        build_nat_no_virt = []

        def fake_build_cmd_nat(**kwargs):
            build_nat_no_virt.append(bool(kwargs.get("no_virt")))
            return [f"no_virt={kwargs.get('no_virt')}"]

        with patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._attempt_start_candidate", side_effect=fake_attempt_start_candidate), \
             patch("vr_hotspotd.lifecycle.build_cmd_nat", side_effect=fake_build_cmd_nat), \
             patch("vr_hotspotd.lifecycle._prepare_ap_interface", return_value=[]), \
             patch("vr_hotspotd.lifecycle._kill_runtime_processes"), \
             patch("vr_hotspotd.lifecycle._remove_conf_dirs", return_value=[]), \
             patch("vr_hotspotd.lifecycle._cleanup_virtual_ap_ifaces", return_value=[]), \
             patch("vr_hotspotd.lifecycle.update_state", side_effect=fake_update_state), \
             patch("vr_hotspotd.lifecycle._collect_affinity_pids", return_value=[]), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_runtime", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle.network_tuning.apply", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle._watchdog_enabled", return_value=False), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            res = lifecycle._start_hotspot_5ghz_strict(
                cfg={"watchdog_enable": False},
                inv={"adapters": []},
                ap_ifname="wlan1",
                target_phy="phy1",
                ssid="VR-Hotspot",
                passphrase="password123",
                country="US",
                ap_security="wpa2",
                ap_ready_timeout_s=5.0,
                optimized_no_virt=True,
                debug=False,
                enable_internet=True,
                bridge_mode=False,
                bridge_name=None,
                bridge_uplink=None,
                gateway_ip="192.168.68.1",
                dhcp_start_ip="192.168.68.10",
                dhcp_end_ip="192.168.68.250",
                dhcp_dns="gateway",
                effective_wifi6=False,
                tuning_state={},
                start_warnings=[],
                fw_cfg={},
                firewall_backend="nftables",
                use_hostapd_nat=True,
                correlation_id="test-cid-bazzite-driver-retry",
                enforced_channel_5g=None,
                allow_fallback_40mhz=False,
                allow_dfs_channels=False,
            )

        self.assertEqual(res.code, "started")
        self.assertEqual(build_nat_no_virt[:2], [True, False])
        self.assertIn("optimized_no_virt_retry_with_virt", state.get("warnings", []))

    def test_start_5ghz_strict_pop_busy_hostapd_nat_fallback_uses_no_virt(self):
        """On Pop!_OS busy fallback from lnxrouter->hostapd_nat should use --no-virt first."""
        from vr_hotspotd import lifecycle
        from vr_hotspotd.lifecycle import APReadyInfo

        candidate = {
            "band": 5,
            "width": 80,
            "primary_channel": 149,
            "center_channel": 155,
            "country": "US",
            "flags": ["non_dfs"],
            "rationale": "test",
        }
        probe_payload = {
            "wifi": {
                "errors": [],
                "warnings": [],
                "counts": {"dfs": 0},
                "candidates": [candidate],
            }
        }

        state = {}

        def fake_update_state(**kwargs):
            state.update(kwargs)
            return dict(state)

        first_res = MagicMock()
        first_res.pid = 2001
        first_res.cmd = ["cmd-lnxrouter"]
        first_res.started_ts = 111
        first_res.exit_code = 1
        first_res.error = "engine_exited_early: rc=1"
        first_res.stdout_tail = []
        first_res.stderr_tail = []

        second_res = MagicMock()
        second_res.pid = 2002
        second_res.cmd = ["cmd-hostapd-nat-no-virt"]
        second_res.started_ts = 112
        second_res.exit_code = None
        second_res.error = None
        second_res.stdout_tail = []
        second_res.stderr_tail = []

        ap_ready = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="VR-Hotspot",
            freq_mhz=5745,
            channel=149,
            channel_width_mhz=80,
        )

        call_count = {"n": 0}

        def fake_attempt_start_candidate(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (
                    None,
                    first_res,
                    "hostapd_failed",
                    "iface_busy",
                    [],
                    ["RTNETLINK answers: Device or resource busy"],
                )
            return ap_ready, second_res, None, None, [], []

        build_nat_no_virt = []

        def fake_build_cmd_nat(**kwargs):
            build_nat_no_virt.append(bool(kwargs.get("no_virt")))
            return [f"no_virt={kwargs.get('no_virt')}"]

        with patch("vr_hotspotd.lifecycle.wifi_probe.probe", return_value=probe_payload), \
             patch("vr_hotspotd.lifecycle._attempt_start_candidate", side_effect=fake_attempt_start_candidate), \
             patch("vr_hotspotd.lifecycle.build_cmd", return_value=["lnxrouter"]), \
             patch("vr_hotspotd.lifecycle.build_cmd_nat", side_effect=fake_build_cmd_nat), \
             patch("vr_hotspotd.lifecycle._prepare_ap_interface", return_value=[]), \
             patch("vr_hotspotd.lifecycle._kill_runtime_processes"), \
             patch("vr_hotspotd.lifecycle._remove_conf_dirs", return_value=[]), \
             patch("vr_hotspotd.lifecycle._cleanup_virtual_ap_ifaces", return_value=[]), \
             patch("vr_hotspotd.lifecycle.update_state", side_effect=fake_update_state), \
             patch("vr_hotspotd.lifecycle._collect_affinity_pids", return_value=[]), \
             patch("vr_hotspotd.lifecycle.system_tuning.apply_runtime", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle.network_tuning.apply", return_value=({}, [])), \
             patch("vr_hotspotd.lifecycle._watchdog_enabled", return_value=False), \
             patch("vr_hotspotd.lifecycle.time.sleep", return_value=None):
            res = lifecycle._start_hotspot_5ghz_strict(
                cfg={"watchdog_enable": False},
                inv={"adapters": []},
                ap_ifname="wlan1",
                target_phy="phy1",
                ssid="VR-Hotspot",
                passphrase="password123",
                country="US",
                ap_security="wpa2",
                ap_ready_timeout_s=5.0,
                optimized_no_virt=False,
                debug=False,
                enable_internet=True,
                bridge_mode=False,
                bridge_name=None,
                bridge_uplink=None,
                gateway_ip="192.168.68.1",
                dhcp_start_ip="192.168.68.10",
                dhcp_end_ip="192.168.68.250",
                dhcp_dns="gateway",
                effective_wifi6=False,
                tuning_state={},
                start_warnings=[],
                fw_cfg={},
                firewall_backend="nftables",
                use_hostapd_nat=False,
                correlation_id="test-cid-pop-busy-fallback",
                enforced_channel_5g=None,
                allow_fallback_40mhz=False,
                allow_dfs_channels=False,
                pop_timeout_retry_no_virt=True,
            )

        self.assertEqual(res.code, "started")
        self.assertEqual(build_nat_no_virt[:1], [True])
        self.assertIn("iface_busy_retry_hostapd_nat_no_virt", state.get("warnings", []))


class TestWidthRegexParsing(unittest.TestCase):
    """Test width regex parsing handles common iw output formats."""

    def test_width_parsing_standard_format(self):
        """Test standard 'width: 80 MHz' format."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("channel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz")
        self.assertEqual(result["channel_width_mhz"], 80)

    def test_width_parsing_no_space(self):
        """Test 'width:80MHz' (no spaces) format."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("width:80MHz")
        self.assertEqual(result["channel_width_mhz"], 80)

    def test_width_parsing_uppercase(self):
        """Test uppercase 'WIDTH: 80 MHZ' format."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("WIDTH: 80 MHZ")
        self.assertEqual(result["channel_width_mhz"], 80)

    def test_width_parsing_40mhz(self):
        """Test 40MHz width parsing."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("width: 40 MHz")
        self.assertEqual(result["channel_width_mhz"], 40)

    def test_width_parsing_20mhz(self):
        """Test 20MHz width parsing."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("width: 20 MHz")
        self.assertEqual(result["channel_width_mhz"], 20)

    def test_width_parsing_no_width_field(self):
        """Test handling of iw output without width field."""
        from vr_hotspotd.lifecycle import _parse_iw_dev_info
        
        result = _parse_iw_dev_info("channel 36 (5180 MHz)")
        self.assertIsNone(result["channel_width_mhz"])


class TestStrictPathWidthFailure(unittest.TestCase):
    """Integration-style test: strict 5GHz start with width mismatch."""

    @patch("vr_hotspotd.lifecycle.load_config")
    @patch("vr_hotspotd.lifecycle.load_state")
    @patch("vr_hotspotd.lifecycle.update_state")
    @patch("vr_hotspotd.lifecycle.ensure_config_file")
    @patch("vr_hotspotd.lifecycle._repair_impl")
    @patch("vr_hotspotd.lifecycle.get_adapters")
    @patch("vr_hotspotd.lifecycle._nm_gate_check")
    @patch("vr_hotspotd.lifecycle.wifi_probe")
    @patch("vr_hotspotd.lifecycle.preflight")
    @patch("vr_hotspotd.lifecycle.system_tuning")
    @patch("vr_hotspotd.lifecycle.start_engine")
    @patch("vr_hotspotd.lifecycle._wait_for_ap_ready")
    @patch("vr_hotspotd.lifecycle._iw_dev_info")
    @patch("vr_hotspotd.lifecycle._iface_is_up")
    @patch("vr_hotspotd.lifecycle.is_running")
    @patch("vr_hotspotd.lifecycle._kill_runtime_processes")
    @patch("vr_hotspotd.lifecycle._remove_conf_dirs")
    @patch("vr_hotspotd.lifecycle._cleanup_virtual_ap_ifaces")
    @patch("vr_hotspotd.os_release.read_os_release")
    @patch("vr_hotspotd.os_release.is_bazzite")
    @patch("os.makedirs")
    @patch("pathlib.Path.mkdir")
    @patch("shutil.chown")
    @patch("time.sleep")
    def test_strict_start_fails_on_width_40_and_cleans_up(
        self,
        mock_sleep,
        mock_chown,
        mock_mkdir,
        mock_makedirs,
        mock_is_bazzite,
        mock_read_os_release,
        mock_cleanup_virt,
        mock_remove_conf,
        mock_kill_runtime,
        mock_is_running,
        mock_iface_up,
        mock_iw_dev,
        mock_wait_ready,
        mock_start_engine,
        mock_sys_tuning,
        mock_preflight,
        mock_wifi_probe,
        mock_nm_gate,
        mock_get_adapters,
        mock_repair_impl,
        mock_ensure_config,
        mock_update_state,
        mock_load_state,
        mock_load_config
    ):
        """Strict 5GHz start should fail when iw reports width=40MHz and cleanup engine."""
        mock_is_bazzite.return_value = False
        mock_read_os_release.return_value = {"ID": "cachyos"}
        mock_nm_gate.return_value = None
        
        mock_get_adapters.return_value = {
            "adapters": [
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

        mock_load_config.return_value = {
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz"
        }
        mock_load_state.return_value = {"phase": "stopped"}
        mock_update_state.return_value = {"phase": "starting"}
        
        # Wifi probe returns one candidate
        mock_wifi_probe.detect_firewall_backends.return_value = {"selected_backend": "nftables"}
        mock_wifi_probe.probe.return_value = {
            "wifi": {
                "candidates": [{"primary_channel": 36, "band": "5ghz", "width_mhz": 80}],
                "errors": [],
                "warnings": []
            }
        }
        
        mock_preflight.run.return_value = {"errors": [], "warnings": [], "details": {}}
        mock_sys_tuning.apply_pre.return_value = ({}, [])
        
        # Engine starts successfully
        mock_engine_res = MagicMock()
        mock_engine_res.ok = True
        mock_engine_res.pid = 12345
        mock_engine_res.cmd = ["hostapd"]
        mock_engine_res.started_ts = 1000
        mock_engine_res.exit_code = None
        mock_engine_res.error = None
        mock_engine_res.stdout_tail = []
        mock_engine_res.stderr_tail = []
        mock_start_engine.return_value = mock_engine_res
        
        # AP appears ready but...
        from vr_hotspotd.lifecycle import APReadyInfo
        mock_ap = APReadyInfo(
            ifname="wlan1",
            phy="phy1",
            ssid="TestSSID",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=40,  # WIDTH IS 40, NOT 80!
        )
        mock_wait_ready.return_value = mock_ap
        mock_iface_up.return_value = True
        mock_is_running.return_value = True
        
        # iw dev info also says 40MHz
        mock_iw_dev.return_value = "channel 36 (5180 MHz), width: 40 MHz"
        
        from vr_hotspotd import lifecycle
        
        res = lifecycle.start_hotspot(basic_mode=True)
        
        # Should fail with specific error code
        self.assertEqual(res.code, "start_failed")
        
        # Verify engine was killed (cleanup invoked)
        self.assertTrue(
            mock_kill_runtime.called,
            "Engine should be killed on width mismatch failure"
        )


if __name__ == "__main__":
    unittest.main()
