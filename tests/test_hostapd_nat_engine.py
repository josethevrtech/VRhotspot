from vr_hotspotd.engine import hostapd_nat_engine


def test_hostapd_nat_conf_vht80_5ghz(tmp_path):
    conf_path = tmp_path / "hostapd.conf"
    hostapd_nat_engine._write_hostapd_conf(
        path=str(conf_path),
        ifname="wlan0",
        ssid="TestSSID",
        passphrase="password123",
        country="US",
        band="5ghz",
        channel=36,
        ap_security="wpa2",
        wifi6=False,
        channel_width="80",
    )

    text = conf_path.read_text()
    assert "ieee80211ac=1" in text
    assert "vht_oper_chwidth=1" in text
    assert "vht_oper_centr_freq_seg0_idx=42" in text
    assert "secondary_channel=" not in text
    assert "HT40+" in text


def test_hostapd_nat_virt_create_falls_back_to_parent(monkeypatch, capsys):
    def fake_run_capture(cmd):
        if cmd == ["iw", "dev", "wlan1", "info"]:
            return 0, "Interface wlan1\n\twiphy 0\n", ""
        if cmd == ["iw", "phy", "0", "interface", "add", "x0wlan1", "type", "__ap"]:
            return 1, "", "phy_add_failed"
        if cmd == ["iw", "dev", "wlan1", "interface", "add", "x0wlan1", "type", "__ap"]:
            return 1, "", "dev_add_failed"
        if cmd == ["iw", "dev"]:
            return 0, "phy#0\n\tInterface wlan1\n\t\tifindex 3\n", ""
        return 0, "", ""

    monkeypatch.setattr(hostapd_nat_engine, "_iw_path", lambda: "iw")
    monkeypatch.setattr(hostapd_nat_engine, "_run_capture", fake_run_capture)

    ap_iface, virt_iface, created_virt = hostapd_nat_engine._select_ap_iface("wlan1", no_virt=False)

    assert ap_iface == "wlan1"
    assert virt_iface == "x0wlan1"
    assert created_virt is False

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "stderr=phy_add_failed" in out or "stderr=dev_add_failed" in out
