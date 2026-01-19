
import os
import sys
from unittest.mock import MagicMock

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from vr_hotspotd.engine import hostapd_nat_engine

def test_gen(name, band, channel, width):
    print(f"--- Generating {name} ({band}, ch={channel}, w={width}) ---")
    path = f"./debug_{name}.conf"
    hostapd_nat_engine._write_hostapd_conf(
        path=path,
        ifname="wlan1",
        ssid="Test",
        passphrase="password",
        country="US",
        band=band,
        channel=channel,
        ap_security="wpa2",
        wifi6=False,
        channel_width=width,
        mode="full"
    )
    with open(path, "r") as f:
        print(f.read())
    os.remove(path)
    print("------------------------------------------------\n")

if __name__ == "__main__":
    # Test 1: The Enforced 5GHz Config
    # width="80", channel=36
    test_gen("Enforced_5GHz", "5ghz", 36, "80")

    # Test 2: The Polluted Fallback 2.4GHz Config
    # width="80" (polluted), channel=6
    test_gen("Fallback_2.4GHz_Polluted", "2.4ghz", 6, "80")
