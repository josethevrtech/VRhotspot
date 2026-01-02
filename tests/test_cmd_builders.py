import sys

from vr_hotspotd.engine.hostapd6_cmd import build_cmd_6ghz
from vr_hotspotd.engine.lnxrouter_cmd import build_cmd


def test_lnxrouter_cmd_builds_5ghz():
    cmd = build_cmd(
        ap_ifname="wlan0",
        ssid="TestSSID",
        passphrase="password123",
        band_preference="5ghz",
        country="US",
        channel=36,
        no_virt=True,
    )
    assert cmd[0].endswith("vendor/bin/lnxrouter")
    assert "--ap" in cmd
    assert "wlan0" in cmd
    assert "TestSSID" in cmd
    assert "-p" in cmd
    assert "password123" in cmd
    assert cmd[cmd.index("--freq-band") + 1] == "5"
    assert cmd[cmd.index("-c") + 1] == "36"
    assert cmd[cmd.index("--country") + 1] == "US"
    assert "--no-virt" in cmd


def test_lnxrouter_cmd_builds_2_4ghz():
    cmd = build_cmd(
        ap_ifname="wlan0",
        ssid="TestSSID",
        passphrase="password123",
        band_preference="2.4",
        country=None,
        channel=None,
        no_virt=False,
    )
    assert cmd[cmd.index("--freq-band") + 1] == "2.4"


def test_hostapd6_cmd_builds_flags():
    cmd = build_cmd_6ghz(
        ap_ifname="wlan1",
        ssid="SixG",
        passphrase="password123",
        country="JP",
        channel=5,
        no_virt=True,
        debug=True,
    )
    assert cmd[:3] == [sys.executable, "-m", "vr_hotspotd.engine.hostapd6_engine"]
    assert "--ap-ifname" in cmd
    assert cmd[cmd.index("--ap-ifname") + 1] == "wlan1"
    assert cmd[cmd.index("--ssid") + 1] == "SixG"
    assert cmd[cmd.index("--passphrase") + 1] == "password123"
    assert cmd[cmd.index("--country") + 1] == "JP"
    assert cmd[cmd.index("--channel") + 1] == "5"
    assert "--no-virt" in cmd
    assert "--debug" in cmd
