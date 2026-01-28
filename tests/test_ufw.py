import os
import sys
import unittest
from unittest.mock import patch

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from vr_hotspotd.engine import ufw


class TestUfwRevert(unittest.TestCase):
    def test_route_delete_order(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return True, ""

        state = {
            "ap_ifname": "wlan1",
            "uplink_ifname": "eth0",
            "rules": ["route_allow:wlan1:eth0"],
        }

        with patch("shutil.which", return_value="/usr/sbin/ufw"), patch(
            "vr_hotspotd.engine.ufw._run", side_effect=fake_run
        ):
            warnings = ufw.revert(state)

        self.assertEqual(warnings, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:4], ["ufw", "route", "delete", "allow"])

    def test_route_delete_missing_rule_is_not_warning(self):
        state = {
            "ap_ifname": "wlan1",
            "uplink_ifname": "eth0",
            "rules": ["route_allow:wlan1:eth0"],
        }

        with patch("shutil.which", return_value="/usr/sbin/ufw"), patch(
            "vr_hotspotd.engine.ufw._run", return_value=(False, "Skipping (rule not found)")
        ):
            warnings = ufw.revert(state)

        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
