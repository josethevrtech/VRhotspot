from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple

import vr_hotspotd.diagnostics.clients as clients


def test_parse_dnsmasq_leases(tmp_path: Path):
    conf = tmp_path / "lnxrouter.wlan1.conf.TEST"
    conf.mkdir(parents=True)
    (conf / "dnsmasq.leases").write_text(
        "1767400000 76:d4:ff:3c:12:8d 192.168.120.217 iPhone *\n"
        "1767400000 aa:bb:cc:dd:ee:ff 192.168.120.10 * *\n"
    )
    out = clients._dnsmasq_leases(conf)
    assert out["76:d4:ff:3c:12:8d"][0] == "192.168.120.217"
    assert out["76:d4:ff:3c:12:8d"][1] == "iPhone"
    assert out["aa:bb:cc:dd:ee:ff"][1] is None


def test_iw_station_dump_parsing(monkeypatch):
    def fake_run(cmd: List[str], timeout_s: float) -> Tuple[int, str, str]:
        if cmd[:4] == ["iw", "dev", "x0wlan1", "station"]:
            return (
                0,
                "Station 76:d4:ff:3c:12:8d (on x0wlan1)\n"
                "\tinactive time:\t40 ms\n"
                "\tsignal:\t\t-44 dBm\n"
                "\tsignal avg:\t-45 dBm\n"
                "\tauthorized:\tyes\n"
                "\tauthenticated:\tyes\n"
                "\tassociated:\tyes\n"
                "\tconnected time:\t123 seconds\n"
                "\ttx retries:\t2\n"
                "\ttx failed:\t1\n"
                "\trx bitrate:\t433.3 MBit/s\n"
                "\ttx bitrate:\t600.0 MBit/s\n",
                "",
            )
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)
    parsed, warn = clients._iw_station_dump("x0wlan1")
    assert warn == ""
    assert parsed is not None
    assert parsed[0].mac == "76:d4:ff:3c:12:8d"
    assert parsed[0].signal_dbm == -44
    assert parsed[0].signal_avg_dbm == -45
    assert parsed[0].authorized is True
    assert parsed[0].authenticated is True
    assert parsed[0].associated is True
    assert parsed[0].connected_time_s == 123
    assert parsed[0].tx_retries == 2
    assert parsed[0].tx_failed == 1
    assert parsed[0].rx_bitrate_mbps == 433.3
    assert parsed[0].tx_bitrate_mbps == 600.0
    assert parsed[0].inactive_ms == 40


def test_snapshot_falls_back_to_iw_when_hostapd_cli_times_out(tmp_path: Path, monkeypatch):
    # fake runtime dir
    root = tmp_path / "lnxrouter_tmp"
    root.mkdir()
    conf = root / "lnxrouter.wlan1.conf.ABCD"
    conf.mkdir()

    (conf / "hostapd.conf").write_text(
        "ssid=VRHotspot-Test\n"
        "interface=x0wlan1\n"
        f"ctrl_interface={conf}/hostapd_ctrl\n"
    )
    (conf / "hostapd_ctrl").mkdir()
    (conf / "hostapd_ctrl" / "x0wlan1").write_text("")
    (conf / "dnsmasq.leases").write_text(
        "1767400000 76:d4:ff:3c:12:8d 192.168.120.217 iPhone *\n"
    )

    # Point module constant at our tmp root
    monkeypatch.setattr(clients, "LNXROUTER_TMP", root)
    monkeypatch.setattr(clients, "load_config", lambda: {"ssid": "VRHotspot-Test"})

    monkeypatch.setattr(clients, "_hostapd_cli_path", lambda: "hostapd_cli")

    def fake_run(cmd: List[str], timeout_s: float):
        if cmd == ["iw", "dev"]:
            return (
                0,
                "phy#0\n\tInterface x0wlan1\n\t\tssid VRHotspot-Test\n\t\ttype AP\n",
                "",
            )
        if cmd[:1] == ["hostapd_cli"] and "ping" in cmd:
            return 0, "PONG\n", ""
        # hostapd_cli list_sta -> timeout
        if cmd[:1] == ["hostapd_cli"] and "list_sta" in cmd:
            return 124, "", "timeout"
        if cmd[:1] == ["hostapd_cli"] and "all_sta" in cmd:
            return 127, "", "nope"
        # iw fallback works
        if cmd[:4] == ["iw", "dev", "x0wlan1", "station"]:
            return (
                0,
                "Station 76:d4:ff:3c:12:8d (on x0wlan1)\n\tsignal:\t-50 dBm\n",
                "",
            )
        # ip neigh enrichment
        if cmd[:3] == ["ip", "neigh", "show"]:
            return (0, "192.168.120.217 lladdr 76:d4:ff:3c:12:8d REACHABLE\n", "")
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)

    snap = clients.get_clients_snapshot("wlan1")
    assert snap["sources"]["primary"] == "iw"
    assert snap["clients"][0]["mac"] == "76:d4:ff:3c:12:8d"
    assert snap["clients"][0]["ip"] == "192.168.120.217"
    assert snap["clients"][0]["hostname"] == "iPhone"
    assert "hostapd_cli_unreliable" in snap["warnings"]


def test_conf_dir_prefers_ctrl_socket_match(tmp_path: Path, monkeypatch):
    root = tmp_path / "lnxrouter_tmp"
    root.mkdir()

    conf_old = root / "lnxrouter.wlan1.conf.OLD"
    conf_new = root / "lnxrouter.wlan1.conf.NEW"
    conf_old.mkdir()
    conf_new.mkdir()

    (conf_old / "hostapd.conf").write_text("interface=x0wlan1\n")
    (conf_new / "hostapd.conf").write_text("interface=x1wlan1\n")
    (conf_old / "hostapd.pid").write_text(str(os.getpid()))

    os.utime(conf_old, (100, 100))
    os.utime(conf_new, (200, 200))

    monkeypatch.setattr(clients, "LNXROUTER_TMP", root)
    monkeypatch.setattr(clients, "load_config", lambda: {"ssid": "VRHotspot"})
    monkeypatch.setattr(clients, "_hostapd_cli_path", lambda: None)

    def fake_run(cmd: List[str], timeout_s: float):
        if cmd == ["iw", "dev"]:
            return (
                0,
                "phy#0\n\tInterface x0wlan1\n\t\tssid VRHotspot\n\t\ttype AP\n",
                "",
            )
        if cmd[:4] == ["iw", "dev", "x0wlan1", "station"]:
            return (
                0,
                "Station 76:d4:ff:3c:12:8d (on x0wlan1)\n\tsignal:\t-50 dBm\n",
                "",
            )
        if cmd[:3] == ["ip", "neigh", "show"]:
            return (0, "", "")
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)

    snap = clients.get_clients_snapshot("wlan1")
    assert snap["conf_dir"] == str(conf_old)


def test_snapshot_skips_hostapd_cli_when_ctrl_missing(tmp_path: Path, monkeypatch):
    root = tmp_path / "lnxrouter_tmp"
    root.mkdir()
    conf = root / "lnxrouter.wlan1.conf.ABCD"
    conf.mkdir()

    (conf / "hostapd.conf").write_text("interface=x0wlan1\n")

    monkeypatch.setattr(clients, "LNXROUTER_TMP", root)
    monkeypatch.setattr(clients, "load_config", lambda: {"ssid": "VRHotspot"})

    def fake_run(cmd: List[str], timeout_s: float):
        if cmd == ["iw", "dev"]:
            return (
                0,
                "phy#0\n\tInterface x0wlan1\n\t\tssid VRHotspot\n\t\ttype AP\n",
                "",
            )
        if cmd[:4] == ["iw", "dev", "x0wlan1", "station"]:
            return (
                0,
                "Station 76:d4:ff:3c:12:8d (on x0wlan1)\n\tsignal:\t-50 dBm\n",
                "",
            )
        if cmd[:3] == ["ip", "neigh", "show"]:
            return (0, "192.168.120.217 lladdr 76:d4:ff:3c:12:8d REACHABLE\n", "")
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)
    monkeypatch.setattr(clients, "_find_ctrl_dir", lambda *_args, **_kwargs: None)

    def explode(*_args, **_kwargs):
        raise AssertionError("hostapd_cli should not be called")

    monkeypatch.setattr(clients, "_hostapd_cli_list_stas", explode)

    snap = clients.get_clients_snapshot("wlan1")
    assert snap["sources"]["primary"] == "iw"
    assert snap["clients"][0]["ip"] == "192.168.120.217"
    assert "hostapd_ctrl_socket_missing" in snap["warnings"]


def test_snapshot_no_active_ap_interface(monkeypatch):
    calls: List[List[str]] = []

    def fake_run(cmd: List[str], timeout_s: float):
        calls.append(cmd)
        if cmd == ["iw", "dev"]:
            return (
                0,
                "phy#0\n\tInterface wlan0\n\t\ttype managed\n",
                "",
            )
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)

    snap = clients.get_clients_snapshot("wlan1")
    assert snap["ap_interface"] is None
    assert snap["conf_dir"] is None
    assert snap["clients"] == []
    assert snap["sources"]["primary"] is None
    assert "no_active_ap_interface" in snap["warnings"]
    assert calls == [["iw", "dev"]]


def test_snapshot_uses_iw_when_hostapd_cli_ping_times_out(tmp_path: Path, monkeypatch):
    root = tmp_path / "lnxrouter_tmp"
    root.mkdir()
    conf = root / "lnxrouter.wlan1.conf.ABCD"
    conf.mkdir()

    (conf / "hostapd.conf").write_text(
        "ssid=VRHotspot-Test\n"
        "interface=x0wlan1\n"
        f"ctrl_interface={conf}/hostapd_ctrl\n"
    )
    (conf / "hostapd_ctrl").mkdir()
    (conf / "hostapd_ctrl" / "x0wlan1").write_text("")

    monkeypatch.setattr(clients, "LNXROUTER_TMP", root)
    monkeypatch.setattr(clients, "load_config", lambda: {"ssid": "VRHotspot-Test"})
    monkeypatch.setattr(clients, "_hostapd_cli_path", lambda: "hostapd_cli")

    def fake_run(cmd: List[str], timeout_s: float):
        if cmd == ["iw", "dev"]:
            return (
                0,
                "phy#0\n\tInterface x0wlan1\n\t\tssid VRHotspot-Test\n\t\ttype AP\n",
                "",
            )
        if cmd[:1] == ["hostapd_cli"] and "ping" in cmd:
            return 124, "", ""
        if cmd[:4] == ["iw", "dev", "x0wlan1", "station"]:
            return (
                0,
                "Station 76:d4:ff:3c:12:8d (on x0wlan1)\n\tsignal:\t-50 dBm\n",
                "",
            )
        return 127, "", "nope"

    monkeypatch.setattr(clients, "_run", fake_run)

    snap = clients.get_clients_snapshot("wlan1")
    assert snap["sources"]["primary"] == "iw"
    assert snap["clients"][0]["mac"] == "76:d4:ff:3c:12:8d"
    assert "hostapd_cli_unreliable" in snap["warnings"]
