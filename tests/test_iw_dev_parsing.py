import vr_hotspotd.lifecycle as lifecycle


def test_parse_iw_dev_ap_info_channel_freq():
    iw_text = """
phy#0
    Interface wlan0
        ifindex 3
        wdev 0x1
        addr 00:11:22:33:44:55
        ssid TestNet
        type AP
        channel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
    Interface wlan1
        ifindex 4
        wdev 0x2
        addr 00:11:22:33:44:56
        ssid OtherNet
        type managed
        channel 1 (2412 MHz), width: 20 MHz
phy#1
    Interface x0wlan0
        ifindex 5
        wdev 0x3
        addr 00:11:22:33:44:57
        ssid TestNet
        type AP
        channel 1 (2412 MHz), width: 20 MHz
"""
    aps = lifecycle._parse_iw_dev_ap_info(iw_text)
    assert len(aps) == 2

    ap0 = aps[0]
    assert ap0.ifname == "wlan0"
    assert ap0.phy == "phy0"
    assert ap0.ssid == "TestNet"
    assert ap0.channel == 36
    assert ap0.freq_mhz == 5180

    ap1 = aps[1]
    assert ap1.ifname == "x0wlan0"
    assert ap1.phy == "phy1"
    assert ap1.ssid == "TestNet"
    assert ap1.channel == 1
    assert ap1.freq_mhz == 2412


def test_parse_iw_dev_ap_info_freq_line():
    iw_text = """
phy#0
    Interface wlan0
        ifindex 3
        wdev 0x1
        addr 00:11:22:33:44:55
        ssid TestNet
        type AP
        freq: 2462
"""
    aps = lifecycle._parse_iw_dev_ap_info(iw_text)
    assert len(aps) == 1
    assert aps[0].freq_mhz == 2462
    assert aps[0].channel is None


def test_band_from_freq_mhz():
    assert lifecycle._band_from_freq_mhz(2412) == "2.4ghz"
    assert lifecycle._band_from_freq_mhz(5180) == "5ghz"
    assert lifecycle._band_from_freq_mhz(5925) == "6ghz"
    assert lifecycle._band_from_freq_mhz(None) is None


def test_select_ap_from_iw_prefers_phy_and_ssid():
    iw_text = """
phy#0
    Interface x0wlan0
        ifindex 5
        wdev 0x3
        addr 00:11:22:33:44:57
        ssid TestNet
        type AP
        channel 1 (2412 MHz), width: 20 MHz
phy#1
    Interface wlan1
        ifindex 6
        wdev 0x4
        addr 00:11:22:33:44:58
        ssid TestNet
        type AP
        channel 36 (5180 MHz), width: 80 MHz
"""
    ap = lifecycle._select_ap_from_iw(iw_text, target_phy="phy1", ssid="TestNet")
    assert ap is not None
    assert ap.ifname == "wlan1"


def test_select_ap_by_ifname():
    iw_text = """
phy#0
    Interface vrhs_ap_wlan0
        ifindex 7
        wdev 0x5
        addr 00:11:22:33:44:59
        ssid TestNet
        type AP
        channel 1 (2412 MHz), width: 20 MHz
    Interface x0wlan0
        ifindex 5
        wdev 0x3
        addr 00:11:22:33:44:57
        ssid TestNet
        type AP
        channel 36 (5180 MHz), width: 80 MHz
"""
    ap = lifecycle._select_ap_by_ifname(iw_text, "vrhs_ap_wlan0")
    assert ap is not None
    assert ap.ifname == "vrhs_ap_wlan0"


def test_wait_for_ap_ready_prefers_ifname():
    iw_text = """
phy#0
    Interface vrhs_ap_wlan0
        ifindex 7
        wdev 0x5
        addr 00:11:22:33:44:59
        ssid TestNet
        type AP
        channel 1 (2412 MHz), width: 20 MHz
    Interface x0wlan0
        ifindex 5
        wdev 0x3
        addr 00:11:22:33:44:57
        ssid TestNet
        type AP
        channel 36 (5180 MHz), width: 80 MHz
"""
    calls = {"hostapd": []}

    def fake_iw_dev_dump():
        return iw_text

    def fake_hostapd_ready(ap_interface, *, adapter_ifname):
        calls["hostapd"].append((ap_interface, adapter_ifname))
        return ap_interface == "vrhs_ap_wlan0"

    orig_iw = lifecycle._iw_dev_dump
    orig_ready = lifecycle._hostapd_ready
    try:
        lifecycle._iw_dev_dump = fake_iw_dev_dump
        lifecycle._hostapd_ready = fake_hostapd_ready
        ap = lifecycle._wait_for_ap_ready(
            target_phy=None,
            timeout_s=0.1,
            poll_s=0.01,
            ssid=None,
            adapter_ifname="vrhs_ap_wlan0",
            capture=None,
        )
        assert ap is not None
        assert ap.ifname == "vrhs_ap_wlan0"
        assert ("vrhs_ap_wlan0", "vrhs_ap_wlan0") in calls["hostapd"]
    finally:
        lifecycle._iw_dev_dump = orig_iw
        lifecycle._hostapd_ready = orig_ready


def test_wait_for_ap_ready_with_expected_ifname_and_log():
    calls = {"hostapd": [], "get_tails": []}

    def fake_iw_dev_dump():
        return ""

    def fake_hostapd_ready(ap_interface, *, adapter_ifname):
        calls["hostapd"].append((ap_interface, adapter_ifname))
        return False

    def fake_get_tails():
        calls["get_tails"].append(1)
        return [
            "wlan0: AP-ENABLED ",
        ], ""

    orig_iw = lifecycle._iw_dev_dump
    orig_ready = lifecycle._hostapd_ready
    orig_tails = lifecycle.get_tails
    try:
        lifecycle._iw_dev_dump = fake_iw_dev_dump
        lifecycle._hostapd_ready = fake_hostapd_ready
        lifecycle.get_tails = fake_get_tails
        ap = lifecycle._wait_for_ap_ready(
            target_phy="phy0",
            timeout_s=0.1,
            poll_s=0.01,
            ssid="TestNet",
            adapter_ifname="wlan0",
            expected_ap_ifname="wlan0",
            capture=None,
        )
        assert ap is not None
        assert ap.ifname == "wlan0"
        assert ap.phy == "phy0"
        assert ap.ssid == "TestNet"
        assert ap.freq_mhz is None
        assert ap.channel is None
        assert ap.channel_width_mhz is None
        assert len(calls["get_tails"]) > 0
    finally:
        lifecycle._iw_dev_dump = orig_iw
        lifecycle._hostapd_ready = orig_ready
        lifecycle.get_tails = orig_tails


def test_wait_for_ap_ready_with_expected_ifname_and_hostapd_ready():
    calls = {"hostapd": [], "get_tails": []}

    def fake_iw_dev_dump():
        return ""

    def fake_hostapd_ready(ap_interface, *, adapter_ifname):
        calls["hostapd"].append((ap_interface, adapter_ifname))
        return ap_interface == "wlan0"
    
    def fake_get_tails():
        calls["get_tails"].append(1)
        return [""], ""

    orig_iw = lifecycle._iw_dev_dump
    orig_ready = lifecycle._hostapd_ready
    orig_tails = lifecycle.get_tails
    try:
        lifecycle._iw_dev_dump = fake_iw_dev_dump
        lifecycle._hostapd_ready = fake_hostapd_ready
        lifecycle.get_tails = fake_get_tails
        ap = lifecycle._wait_for_ap_ready(
            target_phy="phy0",
            timeout_s=0.1,
            poll_s=0.01,
            ssid="TestNet",
            adapter_ifname="wlan0",
            expected_ap_ifname="wlan0",
            capture=None,
        )
        assert ap is not None
        assert ap.ifname == "wlan0"
        assert ap.phy == "phy0"
        assert ap.ssid == "TestNet"
        assert ap.freq_mhz is None
        assert ap.channel is None
        assert ap.channel_width_mhz is None
        assert len(calls["get_tails"]) > 0
        assert ("wlan0", "wlan0") in calls["hostapd"]
    finally:
        lifecycle._iw_dev_dump = orig_iw
        lifecycle._hostapd_ready = orig_ready
        lifecycle.get_tails = orig_tails