import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_iface_up_with_recovery_sets_ap_type_for_no_virt(monkeypatch):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    calls = []

    def fake_iface_up(ifname):
        calls.append(("up", ifname))
        up_calls = [c for c in calls if c[0] == "up"]
        if len(up_calls) == 1:
            raise RuntimeError("cmd_failed rc=2 cmd=ip link set up out=Device or resource busy")

    monkeypatch.setattr(eng, "_iface_up", fake_iface_up)
    monkeypatch.setattr(eng, "_rfkill_unblock_wifi", lambda: calls.append(("rfkill", None)))
    monkeypatch.setattr(eng, "_is_nm_running", lambda: False)
    monkeypatch.setattr(eng, "_nm_knows", lambda _ifname: False)
    monkeypatch.setattr(
        eng,
        "_nm_set_managed",
        lambda ifname, managed: calls.append(("nm_set_managed", ifname, managed)) or True,
    )
    monkeypatch.setattr(eng, "_nm_disconnect", lambda ifname: calls.append(("nm_disconnect", ifname)))
    monkeypatch.setattr(
        eng,
        "_remove_p2p_dev_ifaces",
        lambda ifname: calls.append(("remove_p2p", ifname)) or [],
    )
    monkeypatch.setattr(eng, "_iface_disconnect", lambda ifname: calls.append(("iface_disconnect", ifname)))
    monkeypatch.setattr(eng, "_iface_down", lambda ifname: calls.append(("down", ifname)))
    monkeypatch.setattr(eng, "_flush_ip", lambda ifname: calls.append(("flush", ifname)))
    monkeypatch.setattr(
        eng,
        "_set_iface_type_ap",
        lambda ifname: calls.append(("set_type_ap", ifname)) or True,
    )
    monkeypatch.setattr(
        eng,
        "_set_iface_type_managed",
        lambda ifname: calls.append(("set_type_managed", ifname)) or True,
    )
    monkeypatch.setattr(eng.time, "sleep", lambda *_args, **_kwargs: None)

    eng._iface_up_with_recovery("wlan1", no_virt=True)

    assert ("set_type_ap", "wlan1") in calls
    assert ("set_type_managed", "wlan1") in calls
    assert ("remove_p2p", "wlan1") in calls
    assert len([c for c in calls if c[0] == "up"]) == 2


def test_iface_up_with_recovery_reasserts_nm_unmanaged_across_retries(monkeypatch):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    calls = []

    def fake_iface_up(ifname):
        calls.append(("up", ifname))
        if len([c for c in calls if c[0] == "up"]) < 4:
            raise RuntimeError("cmd_failed rc=2 cmd=ip link set up out=Device or resource busy")

    monkeypatch.setattr(eng, "_iface_up", fake_iface_up)
    monkeypatch.setattr(eng, "_rfkill_unblock_wifi", lambda: calls.append(("rfkill", None)))
    monkeypatch.setattr(eng, "_is_nm_running", lambda: True)
    monkeypatch.setattr(eng, "_nm_knows", lambda _ifname: True)
    monkeypatch.setattr(
        eng,
        "_nm_set_managed",
        lambda ifname, managed: calls.append(("nm_set_managed", ifname, managed)) or True,
    )
    monkeypatch.setattr(eng, "_nm_disconnect", lambda ifname: calls.append(("nm_disconnect", ifname)))
    monkeypatch.setattr(
        eng,
        "_remove_p2p_dev_ifaces",
        lambda ifname: calls.append(("remove_p2p", ifname)) or [],
    )
    monkeypatch.setattr(eng, "_iface_disconnect", lambda ifname: calls.append(("iface_disconnect", ifname)))
    monkeypatch.setattr(eng, "_iface_down", lambda ifname: calls.append(("down", ifname)))
    monkeypatch.setattr(eng, "_flush_ip", lambda ifname: calls.append(("flush", ifname)))
    monkeypatch.setattr(
        eng,
        "_set_iface_type_ap",
        lambda ifname: calls.append(("set_type_ap", ifname)) or True,
    )
    monkeypatch.setattr(
        eng,
        "_set_iface_type_managed",
        lambda ifname: calls.append(("set_type_managed", ifname)) or True,
    )
    monkeypatch.setattr(eng.time, "sleep", lambda *_args, **_kwargs: None)

    eng._iface_up_with_recovery("wlan1", no_virt=True)

    assert len([c for c in calls if c[0] == "up"]) == 4
    assert len([c for c in calls if c[0] == "nm_set_managed" and c[2] is False]) == 3
    assert len([c for c in calls if c[0] == "set_type_managed"]) == 3
    assert len([c for c in calls if c[0] == "set_type_ap"]) == 3


def test_write_hostapd_conf_does_not_emit_noscan_directive(tmp_path):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    conf = tmp_path / "hostapd.conf"
    eng._write_hostapd_conf(
        path=str(conf),
        ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        band="5ghz",
        channel=149,
        ap_security="wpa2",
        wifi6=False,
        channel_width="80",
    )

    text = conf.read_text(encoding="utf-8")
    assert "noscan=1" not in text


def test_write_hostapd_conf_does_not_force_noscan_by_default(tmp_path):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    conf = tmp_path / "hostapd.conf"
    eng._write_hostapd_conf(
        path=str(conf),
        ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        band="5ghz",
        channel=149,
        ap_security="wpa2",
        wifi6=False,
        channel_width="80",
    )

    text = conf.read_text(encoding="utf-8")
    assert "noscan=1" not in text


def test_create_virtual_ap_iface_with_fallback_recovers_name_conflict(monkeypatch):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    attempts = {"n": 0}
    deleted = []

    def fake_create(parent_if, virt_if):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError(
                f"cmd_failed rc=2 cmd=iw dev {parent_if} interface add {virt_if} type __ap "
                "out=RTNETLINK answers: Name not unique on network"
            )

    monkeypatch.setattr(eng, "_create_virtual_ap_iface", fake_create)
    monkeypatch.setattr(eng, "_delete_iface", lambda ifname: deleted.append(ifname))

    chosen = eng._create_virtual_ap_iface_with_fallback("wlx7419f816af4c", "x0wlx7419f816af")

    assert chosen
    assert len(chosen) <= 15
    assert attempts["n"] >= 2
    assert "x0wlx7419f816af" in deleted
