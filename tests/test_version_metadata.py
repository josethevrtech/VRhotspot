from pathlib import Path
import re
import unittest
from unittest.mock import patch

from vr_hotspotd import __version__
from vr_hotspotd.api import APIHandler, APP_VERSION, SERVER_VERSION


class TestVersionMetadata(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version = "([^"]+)"$', text)

        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1.0.4")
        self.assertEqual(__version__, match.group(1))

    def test_api_version_constants_match_package_version(self):
        self.assertEqual(APP_VERSION, "1.0.4")
        self.assertEqual(APP_VERSION, __version__)
        self.assertEqual(SERVER_VERSION, "vr-hotspotd/1.0.4")

    @patch("vr_hotspotd.api.reconcile_state_with_engine")
    @patch("vr_hotspotd.api.load_state")
    @patch("vr_hotspotd.api.load_config")
    @patch("vr_hotspotd.api.collect_platform_matrix")
    def test_status_view_reports_current_version(
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
        mock_collect_platform_matrix.return_value = {}

        out = handler._status_view(include_logs=False)

        self.assertEqual(out["version"], "1.0.4")
        self.assertEqual(out["server_version"], "vr-hotspotd/1.0.4")

    def test_ui_fallback_version_matches_current_release(self):
        html = (Path(__file__).resolve().parents[1] / "assets" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<span id="uiVersion">v1.0.4</span>', html)
        self.assertNotIn("v0.4", html)
