import argparse
import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_main_bootstrap_failure_restores_managed_state(monkeypatch, tmp_path):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    args = argparse.Namespace(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase="password123",
        band="5ghz",
        ap_security="wpa2",
        country="US",
        channel=36,
        no_virt=True,
        debug=False,
        wifi6=False,
        gateway_ip="192.168.68.1",
        dhcp_start="192.168.68.10",
        dhcp_end="192.168.68.250",
        dhcp_dns="gateway",
        no_internet=False,
        channel_width="80",
        beacon_interval=50,
        dtim_period=1,
        short_guard_interval=True,
        tx_power=None,
        strict_width=True,
    )

    calls = []
    conf_dir = tmp_path / "lnxrouter.wlan1.conf.TEST"
    conf_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(eng.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(eng, "_resolve_binary", lambda name, env_key: f"/usr/sbin/{name}")
    monkeypatch.setattr(eng, "_maybe_set_regdom", lambda _country: None)
    monkeypatch.setattr(eng.tempfile, "mkdtemp", lambda prefix, dir: str(conf_dir))
    monkeypatch.setattr(eng, "_is_nm_running", lambda: True)
    monkeypatch.setattr(eng, "_nm_knows", lambda _if: True)
    monkeypatch.setattr(eng, "_nm_disconnect", lambda ifname: calls.append(("nm_disconnect", ifname)))
    monkeypatch.setattr(
        eng,
        "_nm_set_managed",
        lambda ifname, managed: calls.append(("nm_set_managed", ifname, managed)) or True,
    )
    monkeypatch.setattr(eng, "_rfkill_unblock_wifi", lambda: None)
    monkeypatch.setattr(eng, "_iface_down", lambda _if: None)
    monkeypatch.setattr(eng, "_flush_ip", lambda _if: None)
    monkeypatch.setattr(eng, "_set_iface_type_ap", lambda _if: True)
    monkeypatch.setattr(
        eng,
        "_set_iface_type_managed",
        lambda ifname: calls.append(("set_type_managed", ifname)) or True,
    )
    monkeypatch.setattr(
        eng,
        "_iface_up_with_recovery",
        lambda ifname, no_virt=False: (_ for _ in ()).throw(RuntimeError("iface_busy")),
    )

    with pytest.raises(RuntimeError):
        eng.main()

    assert ("set_type_managed", "wlan1") in calls
    assert ("nm_set_managed", "wlan1", True) in calls
