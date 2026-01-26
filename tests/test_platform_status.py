import unittest
from unittest.mock import patch

from vr_hotspotd.api import APIHandler


class TestPlatformStatus(unittest.TestCase):
    @patch("vr_hotspotd.api.reconcile_state_with_engine")
    @patch("vr_hotspotd.api.load_state")
    @patch("vr_hotspotd.api.load_config")
    @patch("vr_hotspotd.api.collect_platform_matrix")
    def test_status_includes_platform_matrix(
        self,
        mock_collect_platform_matrix,
        mock_load_config,
        mock_load_state,
        mock_reconcile,
    ):
        handler = APIHandler.__new__(APIHandler)
        mock_reconcile.return_value = None
        mock_load_state.return_value = {}
        mock_load_config.return_value = {"wpa2_passphrase": "", "telemetry_enable": False}
        mock_collect_platform_matrix.return_value = {
            "os": {},
            "immutability": {},
            "integration": {},
            "session": {},
            "notes": [],
        }

        out = handler._status_view(include_logs=False)

        self.assertIn("platform", out)
        platform = out["platform"]
        self.assertIsInstance(platform, dict)
        for key in ("os", "immutability", "integration", "session", "notes"):
            self.assertIn(key, platform)
