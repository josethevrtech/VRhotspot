import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from vr_hotspotd.api import APIHandler


class TestStatusLogs(unittest.TestCase):
    @patch("vr_hotspotd.api.reconcile_state_with_engine")
    @patch("vr_hotspotd.api.load_state")
    @patch("vr_hotspotd.api.load_config")
    @patch("vr_hotspotd.api.get_tails")
    def test_status_view_uses_live_tails_when_include_logs(
        self,
        mock_get_tails,
        mock_load_config,
        mock_load_state,
        mock_reconcile,
    ):
        handler = APIHandler.__new__(APIHandler)
        mock_reconcile.return_value = None
        mock_load_state.return_value = {
            "engine": {"stdout_tail": ["old-out"], "stderr_tail": ["old-err"]},
        }
        mock_load_config.return_value = {"wpa2_passphrase": ""}
        mock_get_tails.return_value = (["live-out"], ["live-err"])

        out = handler._status_view(include_logs=True)

        self.assertEqual(out["engine"]["stdout_tail"], ["live-out"])
        self.assertEqual(out["engine"]["stderr_tail"], ["live-err"])
