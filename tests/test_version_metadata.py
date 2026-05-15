from pathlib import Path
import re
import unittest
from unittest.mock import patch

from vr_hotspotd import __version__
from vr_hotspotd.api import APIHandler, APP_VERSION, SERVER_VERSION

PACKAGE_VERSION = "1.1.0rc2"
DISPLAY_VERSION = "v1.1.0-rc2"


class TestVersionMetadata(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version = "([^"]+)"$', text)

        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), PACKAGE_VERSION)
        self.assertEqual(__version__, match.group(1))

    def test_api_version_constants_match_package_version(self):
        self.assertEqual(APP_VERSION, PACKAGE_VERSION)
        self.assertEqual(APP_VERSION, __version__)
        self.assertEqual(SERVER_VERSION, f"vr-hotspotd/{PACKAGE_VERSION}")

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

        self.assertEqual(out["version"], PACKAGE_VERSION)
        self.assertEqual(out["server_version"], f"vr-hotspotd/{PACKAGE_VERSION}")

    def test_ui_fallback_version_matches_current_release(self):
        html = (Path(__file__).resolve().parents[1] / "assets" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn(f'<span id="uiVersion">{DISPLAY_VERSION}</span>', html)
        self.assertNotIn('<span id="uiVersion">v1.1.0</span>', html)
        self.assertNotIn("v0.4", html)
