import pytest

from vr_hotspotd.engine import hostapd6_engine, hostapd_bridge_engine, hostapd_nat_engine
from vr_hotspotd import wifi_probe


def write_config(tmp_path, writer, *, band="5ghz", channel=36, width="80", sgi=True, mode="full"):
    path = tmp_path / "hostapd.conf"
    args = dict(path=str(path), ifname="wlan0", ssid="VR Test",
                passphrase="abcdefgh12345", country="US", channel=channel,
                channel_width=width, short_guard_interval=sgi)
    if writer == "6ghz":
        hostapd6_engine._write_hostapd_6ghz_conf(**args)
    elif writer == "bridge":
        hostapd_bridge_engine._write_hostapd_conf(
            **args, band=band, ap_security="wpa2", wifi6=True, bridge="br0",
        )
    else:
        hostapd_nat_engine._write_hostapd_conf(
            **args, band=band, ap_security="wpa2", wifi6=True, mode=mode,
        )
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


@pytest.mark.parametrize("writer", ["6ghz", "bridge"])
@pytest.mark.parametrize("width,opclass,oper,center", [
    ("20", "131", "0", "5"), ("40", "132", "0", "3"),
    ("80", "133", "1", "7"), ("auto", "133", "1", "7"),
    ("160", "134", "2", "15"),
])
def test_6ghz_operating_class_width_and_center(tmp_path, writer, width, opclass, oper, center):
    cfg = write_config(tmp_path, writer, band="6ghz", channel=5, width=width)
    assert cfg["op_class"] == opclass
    assert cfg["he_oper_chwidth"] == oper
    assert cfg["he_oper_centr_freq_seg0_idx"] == center
    assert cfg["sae_pwe"] == "1"
    assert cfg["ieee80211w"] == "2"
    assert cfg["wpa_key_mgmt"] == "SAE"
    assert "ht_capab" not in cfg
    assert "amsdu_frames" not in cfg and "ampdu_density" not in cfg


@pytest.mark.parametrize("writer", ["6ghz", "bridge"])
@pytest.mark.parametrize("channel,width", [(2, "80"), (237, "20"), (229, "80"), (229, "160")])
def test_6ghz_impossible_geometry_never_writes(tmp_path, writer, channel, width):
    with pytest.raises(ValueError, match="invalid_.*channel_block"):
        write_config(tmp_path, writer, band="6ghz", channel=channel, width=width)
    assert not (tmp_path / "hostapd.conf").exists()


@pytest.mark.parametrize("writer", ["bridge", "nat"])
@pytest.mark.parametrize("sgi", [True, False])
@pytest.mark.parametrize("width,oper,center", [
    ("20", "0", None), ("40", "0", None),
    ("80", "1", "42"), ("auto", "1", "42"), ("160", "2", "50"),
])
def test_5ghz_width_consistent_across_writers_and_independent_of_sgi(
    tmp_path, writer, sgi, width, oper, center,
):
    cfg = write_config(tmp_path, writer, width=width, sgi=sgi)
    assert cfg["vht_oper_chwidth"] == oper
    assert cfg["he_oper_chwidth"] == oper
    assert cfg.get("vht_oper_centr_freq_seg0_idx") == center
    assert cfg.get("he_oper_centr_freq_seg0_idx") == center
    if width != "20":
        assert "HT40+" in cfg.get("ht_capab", "")
    else:
        assert "HT40+" not in cfg.get("ht_capab", "")
    assert ("SHORT-GI" in cfg.get("ht_capab", "")) == sgi
    assert ("VHT160" in cfg.get("vht_capab", "")) == (width == "160")
    if width == "160":
        assert ("SHORT-GI-160" in cfg["vht_capab"]) == sgi
        assert cfg["vht_capab"].count("VHT160") == 1
        assert cfg["vht_capab"].count("SHORT-GI-160") <= 1
    assert "amsdu_frames" not in cfg and "ampdu_density" not in cfg


@pytest.mark.parametrize("writer", ["bridge", "nat"])
@pytest.mark.parametrize("channel,ht40,center", [
    (40, "HT40-", "42"), (44, "HT40+", "42"),
    (153, "HT40-", "155"), (177, "HT40-", "171"),
])
def test_primary_upper_lower_and_extended_5ghz_block(tmp_path, writer, channel, ht40, center):
    cfg = write_config(tmp_path, writer, channel=channel)
    assert ht40 in cfg["ht_capab"]
    assert cfg["vht_oper_centr_freq_seg0_idx"] == center


@pytest.mark.parametrize("writer", ["bridge", "nat"])
@pytest.mark.parametrize("width,channel,ht40", [
    ("auto", 6, None), ("80", 6, None), ("20", 1, None),
    ("40", 1, "HT40+"), ("40", 11, "HT40-"),
])
def test_24ghz_fallback_and_explicit40(tmp_path, writer, width, channel, ht40):
    cfg = write_config(tmp_path, writer, band="2.4ghz", channel=channel, width=width, sgi=False)
    assert cfg["hw_mode"] == "g"
    assert "vht_oper_chwidth" not in cfg
    assert "he_oper_chwidth" not in cfg
    if ht40:
        assert ht40 in cfg["ht_capab"]
    else:
        assert "ht_capab" not in cfg


@pytest.mark.parametrize("mode", ["reduced", "legacy"])
def test_nat_recovery_modes_stay_narrow(tmp_path, mode):
    cfg = write_config(tmp_path, "nat", width="80", mode=mode)
    assert "vht_oper_chwidth" not in cfg
    assert "HT40" not in cfg.get("ht_capab", "")
    assert "he_oper_chwidth" not in cfg


def test_probe_40mhz_pairs_are_aligned_and_preserve_upper_primary():
    channels = [{"channel": ch} for ch in (36, 40, 44, 48)]
    rows, _ = wifi_probe._build_40mhz_candidates(
        channels, allow_dfs=False, preferred_primary_channel=40, country="US",
    )
    assert [(row["primary_channel"], row["center_channel"]) for row in rows] == [(40, 38), (44, 46)]


def test_probe_80mhz_uses_shared_geometry_and_complete_blocks():
    channels = [{"channel": ch} for ch in (36, 40, 44, 165, 169, 173, 177)]
    rows, _ = wifi_probe._build_80mhz_candidates(
        channels, allow_dfs=False, preferred_primary_channel=177, country="US",
    )
    assert [(row["primary_channel"], row["center_channel"]) for row in rows] == [(177, 171)]


@pytest.mark.parametrize("channel,width,ht40,oper,center", [
    (36, "20", "", None, None),
    (40, "40", "[HT40-]", None, None),
    (153, "80", "[HT40-]", "1", "155"),
    (44, "auto", "[HT40+]", "1", "42"),
    (40, "160", "[HT40-]", "2", "50"),
])
def test_linux_router_uses_same_width_geometry(monkeypatch, channel, width, ht40, oper, center):
    from vr_hotspotd.engine import lnxrouter_cmd
    monkeypatch.setattr(lnxrouter_cmd, "_lnxrouter_path", lambda: "/vendor/lnxrouter")
    cmd = lnxrouter_cmd.build_cmd(
        ap_ifname="wlan0", ssid="VR Test", passphrase="abcdefgh12345",
        channel=channel, channel_width=width, wifi6=True,
    )
    assert cmd[cmd.index("--ht-capab") + 1] == ht40
    if width == "160":
        assert cmd[cmd.index("--vht-capab") + 1] == "[VHT160]"
        assert cmd.count("--vht-capab") == 1
    if oper is not None:
        assert cmd[cmd.index("--vht-ch-width") + 1] == oper
        assert cmd[cmd.index("--he-ch-width") + 1] == oper
        assert cmd[cmd.index("--vht-seg0-ch") + 1] == center
        assert cmd[cmd.index("--he-seg0-ch") + 1] == center
    else:
        assert "--vht-ch-width" not in cmd


def test_linux_router_rejects_mismatched_center(monkeypatch):
    from vr_hotspotd.engine import lnxrouter_cmd
    monkeypatch.setattr(lnxrouter_cmd, "_lnxrouter_path", lambda: "/vendor/lnxrouter")
    with pytest.raises(ValueError, match="channel_center_mismatch"):
        lnxrouter_cmd.build_cmd(
            ap_ifname="wlan0", ssid="VR Test", passphrase="abcdefgh12345",
            channel=153, channel_width="80", center_channel=42,
        )
