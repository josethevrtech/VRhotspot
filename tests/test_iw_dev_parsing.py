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
