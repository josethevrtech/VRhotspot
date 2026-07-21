import argparse
import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_steamos_iwd_reservation_writes_per_interface_config(monkeypatch, tmp_path):
    from vr_hotspotd import lifecycle

    writer = lifecycle._write_nm_iwd_autoconnect_conf
    restarted = []

    monkeypatch.setattr(lifecycle.os_release, "is_steamos", lambda _info=None: True)
    monkeypatch.setattr(lifecycle, "_iwd_is_active", lambda: True)
    monkeypatch.setattr(
        lifecycle,
        "_write_nm_iwd_autoconnect_conf",
        lambda ifname: writer(ifname, conf_dir=tmp_path),
    )
    monkeypatch.setattr(lifecycle, "_restart_unit", lambda unit: restarted.append(unit) or True)

    warnings = lifecycle._reserve_iwd_ap_adapter(
        "wlan1",
        platform_info={"id": "steamos"},
        adapter={"supports_ap": True},
    )

    conf = tmp_path / "99-vrhotspot-wlan1-iwd.conf"
    assert conf.read_text(encoding="utf-8") == (
        "[device-vrhotspot-wlan1]\n"
        "match-device=interface-name:wlan1\n"
        "wifi.backend=iwd\n"
        "wifi.iwd.autoconnect=false\n"
    )
    assert restarted == ["NetworkManager", "iwd"]
    assert any(str(conf) in warning for warning in warnings)


def test_hostapd_nat_iwd_disconnects_parent_before_ap_start(
    monkeypatch,
    mock_missing_system_commands,
    tmp_path,
):
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

    monkeypatch.setenv("VR_HOTSPOT_LNXROUTER_TMPDIR", str(tmp_path / "lnxrouter_tmp"))
    monkeypatch.setattr(eng.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(eng, "_resolve_binary", lambda name, env_key: f"/usr/sbin/{name}")
    monkeypatch.setattr(eng, "_maybe_set_regdom", lambda _country: None)
    monkeypatch.setattr(eng, "_remove_p2p_dev_ifaces", lambda ifname: calls.append(("p2p", ifname)) or [])
    monkeypatch.setattr(eng, "_iwd_is_active", lambda: True)
    monkeypatch.setattr(eng, "_iwctl_station_disconnect", lambda ifname: calls.append(("iwctl", ifname)))
    monkeypatch.setattr(eng, "_iface_disconnect", lambda ifname: calls.append(("iw", ifname)))
    monkeypatch.setattr(
        eng,
        "_nm_set_managed",
        lambda ifname, managed: calls.append(("nm_set_managed", ifname, managed)) or True,
    )
    monkeypatch.setattr(eng, "_iface_has_ssid", lambda ifname: False)
    monkeypatch.setattr(eng, "_is_nm_running", lambda: False)
    monkeypatch.setattr(eng, "_nm_knows", lambda _ifname: False)
    monkeypatch.setattr(eng, "_rfkill_unblock_wifi", lambda: None)
    monkeypatch.setattr(eng, "_iface_down", lambda _ifname: None)
    monkeypatch.setattr(eng, "_flush_ip", lambda _ifname: None)
    monkeypatch.setattr(eng, "_set_iface_type_ap", lambda _ifname: True)
    monkeypatch.setattr(eng, "_set_iface_type_managed", lambda _ifname: True)
    monkeypatch.setattr(
        eng,
        "_iface_up_with_recovery",
        lambda _ifname, no_virt=False: (_ for _ in ()).throw(RuntimeError("stop_after_prep")),
    )

    with pytest.raises(RuntimeError, match="stop_after_prep"):
        eng.main()

    assert calls[:4] == [
        ("p2p", "wlan1"),
        ("iwctl", "wlan1"),
        ("iw", "wlan1"),
        ("nm_set_managed", "wlan1", False),
    ]


def test_steamos_iwd_still_associated_fails_with_clear_error(
    monkeypatch,
    mock_missing_system_commands,
):
    from vr_hotspotd import lifecycle

    states = []
    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "load_state", lambda: {"phase": "stopped"})
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "_repair_impl", lambda correlation_id="start": {})
    monkeypatch.setattr(lifecycle, "update_state", lambda **kwargs: states.append(kwargs) or kwargs)
    monkeypatch.setattr(
        lifecycle,
        "load_config",
        lambda: {
            "ssid": "VR-Hotspot",
            "wpa2_passphrase": "password123",
            "band_preference": "5ghz",
        },
    )
    monkeypatch.setattr(lifecycle.wifi_probe, "detect_firewall_backends", lambda: {"selected_backend": "firewalld"})
    monkeypatch.setattr(lifecycle.os_release, "read_os_release", lambda: {"id": "steamos"})
    monkeypatch.setattr(lifecycle.os_release, "apply_platform_overrides", lambda cfg, info: (cfg, []))
    monkeypatch.setattr(lifecycle.os_release, "is_cachyos", lambda _info=None: False)
    monkeypatch.setattr(lifecycle.os_release, "is_pop_os", lambda _info=None: False)
    monkeypatch.setattr(lifecycle.os_release, "is_bazzite", lambda _info=None: False)
    monkeypatch.setattr(lifecycle.os_release, "is_steamos", lambda _info=None: True)
    monkeypatch.setattr(
        lifecycle,
        "get_adapters",
        lambda: {
            "recommended": "wlan1",
            "adapters": [
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": True,
                }
            ],
        },
    )
    monkeypatch.setattr(lifecycle, "_iwd_is_active", lambda: True)
    monkeypatch.setattr(lifecycle, "_reserve_iwd_ap_adapter", lambda *args, **kwargs: [])
    monkeypatch.setattr(lifecycle, "_nm_gate_check", lambda _ifname: None)
    monkeypatch.setattr(lifecycle, "_disconnect_iwd_ap_adapter", lambda _ifname: [])
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_iw_iface_has_ssid", lambda _ifname: True)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda *_args, **_kwargs: None)

    res = lifecycle._start_hotspot_impl(correlation_id="t1")

    assert res.code == "start_failed"
    assert res.state["last_error"] == "ap_adapter_still_associated_iwd_autoconnect"
    assert "reserve USB Wi-Fi for AP" in res.state["last_error_detail"]["remediation"]
