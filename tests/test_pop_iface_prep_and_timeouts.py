import os
import subprocess
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_prepare_ap_interface_force_disconnect_sets_unmanaged(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    calls = []

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(
        lifecycle,
        "_nm_set_unmanaged",
        lambda ifname: calls.append(("set_unmanaged", ifname)) or (True, None),
    )
    monkeypatch.setattr(
        lifecycle,
        "_nm_disconnect",
        lambda ifname: calls.append(("disconnect", ifname)) or (True, None),
    )
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)

    warnings = lifecycle._prepare_ap_interface("wlan1", force_nm_disconnect=True)

    assert warnings == []
    assert ("set_unmanaged", "wlan1") in calls
    assert ("disconnect", "wlan1") in calls


def test_nm_set_unmanaged_treats_device_not_found_as_success(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    class P:
        returncode = 10
        stdout = ""
        stderr = "Error: Device 'wlan9' not found."

    monkeypatch.setattr(lifecycle.os, "geteuid", lambda: 0)
    monkeypatch.setattr(lifecycle, "_nmcli_path", lambda: "/usr/bin/nmcli")
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *_args, **_kwargs: P())

    ok, err = lifecycle._nm_set_unmanaged("wlan9")
    assert ok is True
    assert err is None


def test_nm_disconnect_treats_not_all_devices_found_as_success(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    class P:
        returncode = 10
        stdout = ""
        stderr = "Error: Device 'wlan9' not found.\nError: not all devices found."

    monkeypatch.setattr(lifecycle, "_nmcli_path", lambda: "/usr/bin/nmcli")
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *_args, **_kwargs: P())

    ok, err = lifecycle._nm_disconnect("wlan9")
    assert ok is True
    assert err is None


def test_prepare_ap_interface_reports_removed_p2p_iface(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_nm_disconnect", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_cleanup_p2p_dev_ifaces", lambda _ifname: ["p2p-dev-wlan1"])
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)

    warnings = lifecycle._prepare_ap_interface("wlan1", force_nm_disconnect=True)

    assert "removed_p2p_dev_iface:p2p-dev-wlan1" in warnings


def test_prepare_ap_interface_warns_when_nm_still_managed(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_nm_disconnect", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_cleanup_p2p_dev_ifaces", lambda _ifname: [])
    monkeypatch.setattr(lifecycle, "_nm_wait_non_interfering", lambda _ifname: False)
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)

    warnings = lifecycle._prepare_ap_interface("wlan1", force_nm_disconnect=True)

    assert "nm_still_managed_prestart" in warnings


def test_prepare_ap_interface_attempts_driver_reload_when_still_down(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_nm_disconnect", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_cleanup_p2p_dev_ifaces", lambda _ifname: [])
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_bus_type", lambda _ifname: "usb")
    monkeypatch.setattr(lifecycle, "_driver_reload_recovery_enabled", lambda: True)

    up_calls = {"n": 0}

    def fake_ensure_up(_ifname):
        up_calls["n"] += 1
        return up_calls["n"] >= 2

    monkeypatch.setattr(lifecycle, "_ensure_iface_up", fake_ensure_up)
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda _ifname: False)
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *_args, **_kwargs: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr(
        lifecycle,
        "_reload_wifi_driver_for_iface",
        lambda _ifname: (True, "mt7921u"),
    )

    warnings = lifecycle._prepare_ap_interface("wlan1", force_nm_disconnect=True)

    assert "ap_iface_not_up_prestart" in warnings
    assert "ap_iface_driver_reload:mt7921u" in warnings
    assert up_calls["n"] >= 2


def test_prepare_ap_interface_skips_driver_reload_on_non_usb(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_nm_disconnect", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_cleanup_p2p_dev_ifaces", lambda _ifname: [])
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_bus_type", lambda _ifname: "pci")
    monkeypatch.setattr(lifecycle, "_driver_reload_recovery_enabled", lambda: True)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: False)
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda _ifname: False)
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *_args, **_kwargs: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    reload_called = {"n": 0}
    monkeypatch.setattr(
        lifecycle,
        "_reload_wifi_driver_for_iface",
        lambda _ifname: reload_called.__setitem__("n", reload_called["n"] + 1) or (True, "iwlwifi"),
    )

    warnings = lifecycle._prepare_ap_interface("wlp8s0", force_nm_disconnect=True)

    assert "ap_iface_not_up_prestart" in warnings
    assert "ap_iface_driver_reload_skipped_non_usb:pci" in warnings
    assert reload_called["n"] == 0


def test_prepare_ap_interface_does_not_reload_driver_by_default(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_set_unmanaged", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_nm_disconnect", lambda _ifname: (True, None))
    monkeypatch.setattr(lifecycle, "_cleanup_p2p_dev_ifaces", lambda _ifname: [])
    monkeypatch.setattr(lifecycle, "_rfkill_unblock_wifi", lambda: True)
    monkeypatch.setattr(lifecycle, "_iface_exists", lambda _ifname: True)
    monkeypatch.setattr(lifecycle, "_iface_bus_type", lambda _ifname: "usb")
    monkeypatch.setattr(lifecycle, "_driver_reload_recovery_enabled", lambda: False)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: False)
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda _ifname: False)
    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *_args, **_kwargs: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    reload_called = {"n": 0}
    monkeypatch.setattr(
        lifecycle,
        "_reload_wifi_driver_for_iface",
        lambda _ifname: reload_called.__setitem__("n", reload_called["n"] + 1) or (True, "mt7921u"),
    )

    warnings = lifecycle._prepare_ap_interface("wlx7419f816af4c", force_nm_disconnect=True)

    assert "ap_iface_not_up_prestart" in warnings
    assert "ap_iface_driver_reload:mt7921u" not in warnings
    assert reload_called["n"] == 0


def test_reselect_adapter_after_reload_when_iface_missing(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    inv_initial = {
        "adapters": [{"ifname": "wlxOLD", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True}],
        "recommended": "wlxOLD",
    }
    inv_refreshed = {
        "adapters": [{"ifname": "wlxNEW", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True}],
        "recommended": "wlxNEW",
    }

    monkeypatch.setattr(lifecycle, "get_adapters", lambda: inv_refreshed)

    def fake_exists(path: str) -> bool:
        if path.endswith("/sys/class/net/wlxOLD"):
            return False
        if path.endswith("/sys/class/net/wlxNEW"):
            return True
        return False

    monkeypatch.setattr(lifecycle.os.path, "exists", fake_exists)

    ap_ifname, inv_out, adapter_out, warnings = lifecycle._maybe_reselect_ap_after_prestart_failure(
        ap_ifname="wlxOLD",
        preferred_ifname="wlxOLD",
        band_pref="5ghz",
        inv=inv_initial,
        adapter=inv_initial["adapters"][0],
        platform_is_pop=True,
        prep_warnings=["ap_iface_not_up_post_driver_reload"],
    )

    assert ap_ifname == "wlxNEW"
    assert inv_out["recommended"] == "wlxNEW"
    assert adapter_out and adapter_out.get("ifname") == "wlxNEW"
    assert "ap_adapter_reselected_after_reload:wlxOLD->wlxNEW" in warnings


def test_reselect_adapter_noop_when_not_pop(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    inv_initial = {
        "adapters": [{"ifname": "wlxOLD", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True}],
        "recommended": "wlxOLD",
    }

    ap_ifname, inv_out, adapter_out, warnings = lifecycle._maybe_reselect_ap_after_prestart_failure(
        ap_ifname="wlxOLD",
        preferred_ifname="wlxOLD",
        band_pref="5ghz",
        inv=inv_initial,
        adapter=inv_initial["adapters"][0],
        platform_is_pop=False,
        prep_warnings=["ap_iface_not_up_post_driver_reload"],
    )

    assert ap_ifname == "wlxOLD"
    assert inv_out["recommended"] == "wlxOLD"
    assert adapter_out and adapter_out.get("ifname") == "wlxOLD"
    assert warnings == []


def test_reselect_adapter_keeps_usb_when_only_pci_available(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    inv_initial = {
        "adapters": [{"ifname": "wlxUSBOLD", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True, "bus": "usb"}],
        "recommended": "wlxUSBOLD",
    }
    inv_pci_only = {
        "adapters": [{"ifname": "wlp8s0", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True, "bus": "pci"}],
        "recommended": "wlp8s0",
    }

    monkeypatch.setattr(lifecycle, "get_adapters", lambda: inv_pci_only)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)

    def fake_exists(path: str) -> bool:
        if path.endswith("/sys/class/net/wlxUSBOLD"):
            return False
        if path.endswith("/sys/class/net/wlp8s0"):
            return True
        return False

    monkeypatch.setattr(lifecycle.os.path, "exists", fake_exists)

    ap_ifname, inv_out, adapter_out, warnings = lifecycle._maybe_reselect_ap_after_prestart_failure(
        ap_ifname="wlxUSBOLD",
        preferred_ifname="wlxUSBOLD",
        band_pref="5ghz",
        inv=inv_initial,
        adapter=inv_initial["adapters"][0],
        platform_is_pop=True,
        prep_warnings=["ap_iface_not_up_post_driver_reload"],
    )

    assert ap_ifname == "wlxUSBOLD"
    assert inv_out["recommended"] == "wlxUSBOLD"
    assert adapter_out and adapter_out.get("ifname") == "wlxUSBOLD"
    assert "ap_adapter_reselect_usb_missing_after_reload" in warnings
    assert not any("->wlp8s0" in w for w in warnings)


def test_virtual_iface_missing_signal_detects_x0_cannot_find_device():
    import vr_hotspotd.lifecycle as lifecycle

    lines = [
        "iface_up_retry iface=x0wlx7419f816af reason=cmd_failed rc=1 cmd=/usr/sbin/ip link set x0wlx7419f816af up out=Cannot find device \"x0wlx7419f816af\"",
    ]
    assert lifecycle._lines_have_virtual_iface_missing_signal(lines) is True


def test_attempt_start_candidate_refreshes_tails_when_engine_exits_early(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    res = SimpleNamespace(
        ok=False,
        pid=None,
        cmd=["fake"],
        started_ts=1,
        exit_code=1,
        error="engine_exited_early: rc=1",
        stdout_tail=[],
        stderr_tail=[],
    )

    monkeypatch.setattr(lifecycle, "start_engine", lambda *_args, **_kwargs: res)
    monkeypatch.setattr(
        lifecycle,
        "get_tails",
        lambda: (
            ['iface_up_retry iface=x0wlx7419f816af reason=cmd_failed rc=1 cmd=/usr/sbin/ip link set x0wlx7419f816af up out=Cannot find device "x0wlx7419f816af"'],
            [],
        ),
    )

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)

    ap_info, _res_out, failure_code, _failure_detail, out_tail, _err_tail = lifecycle._attempt_start_candidate(
        cmd=["fake"],
        firewalld_cfg={},
        target_phy="phy1",
        ap_ready_timeout_s=6.0,
        ssid="VR-Hotspot",
        adapter_ifname="wlx7419f816af4c",
        expected_ap_ifname="x0wlx7419f816af",
        require_band="5ghz",
        require_width_mhz=80,
    )

    assert ap_info is None
    assert failure_code == "hostapd_failed"
    assert out_tail and "Cannot find device" in out_tail[0]
    assert "engine" in state


def test_nm_interference_reason_ignores_disconnected(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_device_state", lambda _ifname: "disconnected")

    assert lifecycle._nm_interference_reason("wlan0") is None


def test_nm_interference_reason_reports_connected(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_nm_is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_nm_device_state", lambda _ifname: "connected")

    assert lifecycle._nm_interference_reason("wlan0") == "nm_state=connected"


def test_parent_iface_missing_signal_detects_cannot_find_parent_iface():
    import vr_hotspotd.lifecycle as lifecycle

    lines = [
        'iface_up_retry iface=wlx7419f816af4c reason=cmd_failed rc=1 cmd=/usr/sbin/ip link set wlx7419f816af4c up out=Cannot find device "wlx7419f816af4c"',
    ]
    assert lifecycle._lines_have_parent_iface_missing_signal(lines, "wlx7419f816af4c") is True


def test_start_5ghz_strict_reselects_iface_after_no_virt_parent_missing(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    candidate = {
        "band": 5,
        "width": 80,
        "primary_channel": 36,
        "center_channel": 42,
        "country": "US",
        "flags": ["non_dfs"],
        "rationale": "test",
    }
    probe_payload = {
        "wifi": {
            "errors": [],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [candidate],
        }
    }
    inv = {
        "adapters": [
            {"ifname": "wlxOLD", "phy": "phy9", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True, "bus": "usb"},
            {"ifname": "wlxNEW", "phy": "phy10", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True, "bus": "usb"},
        ],
        "recommended": "wlxOLD",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    call_n = {"n": 0}

    def fake_attempt_start_candidate(**kwargs):
        call_n["n"] += 1
        cmd = kwargs.get("cmd") or []
        cmd_s = " ".join(str(x) for x in cmd)
        # 1) virt on old iface fails
        if call_n["n"] == 1:
            return (
                None,
                SimpleNamespace(pid=1, cmd=["virt-old"], started_ts=1),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                [],
                ['RuntimeError: cmd_failed rc=1 cmd=/usr/sbin/ip link set x0wlxOLD up out=Cannot find device "x0wlxOLD"'],
            )
        # 2) no-virt on old iface fails (parent missing)
        if call_n["n"] == 2:
            assert "if=wlxOLD" in cmd_s
            assert "no_virt=True" in cmd_s
            return (
                None,
                SimpleNamespace(pid=2, cmd=["no-virt-old"], started_ts=2),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                ['iface_up_retry iface=wlxOLD reason=cmd_failed rc=1 cmd=/usr/sbin/ip link set wlxOLD up out=Cannot find device "wlxOLD"'],
                [],
            )
        # 3) no-virt on reselected iface succeeds
        assert "if=wlxNEW" in cmd_s
        assert "no_virt=True" in cmd_s
        return (
            lifecycle.APReadyInfo(
                ifname="wlxNEW",
                phy="phy10",
                ssid="VR-Hotspot",
                freq_mhz=5180,
                channel=36,
                channel_width_mhz=80,
            ),
            SimpleNamespace(pid=3, cmd=["no-virt-new"], started_ts=3),
            None,
            None,
            [],
            [],
        )

    def fake_build_cmd_nat(**kwargs):
        return [f"if={kwargs.get('ap_ifname')}", f"no_virt={kwargs.get('no_virt')}"]

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "_attempt_start_candidate", fake_attempt_start_candidate)
    monkeypatch.setattr(lifecycle, "build_cmd_nat", fake_build_cmd_nat)
    monkeypatch.setattr(lifecycle, "_prepare_ap_interface", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_kill_runtime_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_remove_conf_dirs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_cleanup_virtual_ap_ifaces", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_collect_affinity_pids", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle, "_watchdog_enabled", lambda _cfg: False)
    monkeypatch.setattr(lifecycle, "_maybe_reselect_ap_after_prestart_failure", lambda **_kwargs: ("wlxNEW", inv, inv["adapters"][1], ["ap_adapter_reselected_after_reload:wlxOLD->wlxNEW"]))
    monkeypatch.setattr(lifecycle.os.path, "exists", lambda p: not p.endswith("/sys/class/net/wlxOLD"))

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlxOLD",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=[],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="parent-missing-reselect",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=True,
    )

    assert res.code == "started"
    assert "ap_parent_iface_missing_reselect" in state.get("warnings", [])
    assert "ap_adapter_reselected_after_reload:wlxOLD->wlxNEW" in state.get("warnings", [])


def test_start_5ghz_strict_retries_after_parent_missing_even_when_iface_name_unchanged(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    candidate = {
        "band": 5,
        "width": 80,
        "primary_channel": 36,
        "center_channel": 42,
        "country": "US",
        "flags": ["non_dfs"],
        "rationale": "test",
    }
    probe_payload = {
        "wifi": {
            "errors": [],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [candidate],
        }
    }
    inv = {
        "adapters": [
            {"ifname": "wlxOLD", "phy": "phy9", "supports_ap": True, "supports_5ghz": True, "supports_80mhz": True, "bus": "usb"},
        ],
        "recommended": "wlxOLD",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    call_n = {"n": 0}

    def fake_attempt_start_candidate(**kwargs):
        call_n["n"] += 1
        cmd = kwargs.get("cmd") or []
        cmd_s = " ".join(str(x) for x in cmd)
        # 1) virt on old iface fails
        if call_n["n"] == 1:
            return (
                None,
                SimpleNamespace(pid=1, cmd=["virt-old"], started_ts=1),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                [],
                ['RuntimeError: cmd_failed rc=1 cmd=/usr/sbin/ip link set x0wlxOLD up out=Cannot find device "x0wlxOLD"'],
            )
        # 2) no-virt on old iface fails (parent missing)
        if call_n["n"] == 2:
            assert "if=wlxOLD" in cmd_s
            assert "no_virt=True" in cmd_s
            return (
                None,
                SimpleNamespace(pid=2, cmd=["no-virt-old"], started_ts=2),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                ['iface_up_retry iface=wlxOLD reason=cmd_failed rc=1 cmd=/usr/sbin/ip link set wlxOLD up out=Cannot find device "wlxOLD"'],
                [],
            )
        # 3) post-reselect retry on same iface succeeds
        assert "if=wlxOLD" in cmd_s
        assert "no_virt=True" in cmd_s
        return (
            lifecycle.APReadyInfo(
                ifname="wlxOLD",
                phy="phy9",
                ssid="VR-Hotspot",
                freq_mhz=5180,
                channel=36,
                channel_width_mhz=80,
            ),
            SimpleNamespace(pid=3, cmd=["no-virt-old-retry"], started_ts=3),
            None,
            None,
            [],
            [],
        )

    def fake_build_cmd_nat(**kwargs):
        return [f"if={kwargs.get('ap_ifname')}", f"no_virt={kwargs.get('no_virt')}"]

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "_attempt_start_candidate", fake_attempt_start_candidate)
    monkeypatch.setattr(lifecycle, "build_cmd_nat", fake_build_cmd_nat)
    monkeypatch.setattr(lifecycle, "_prepare_ap_interface", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_kill_runtime_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_remove_conf_dirs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_cleanup_virtual_ap_ifaces", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_collect_affinity_pids", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle, "_watchdog_enabled", lambda _cfg: False)
    monkeypatch.setattr(
        lifecycle,
        "_maybe_reselect_ap_after_prestart_failure",
        lambda **_kwargs: ("wlxOLD", inv, inv["adapters"][0], []),
    )

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlxOLD",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=[],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="parent-missing-retry-same-ifname",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=True,
    )

    assert res.code == "started"
    assert call_n["n"] == 3
    assert "ap_parent_iface_missing_reselect" in state.get("warnings", [])


def test_lifecycle_run_timeout_returns_marker(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["iw", "dev"], timeout=0.01, output="", stderr="")

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)

    out = lifecycle._run(["iw", "dev"], timeout_s=0.01)
    assert "cmd_timed_out:iw dev" in out


def test_hostapd_nat_run_timeout_raises_runtimeerror(monkeypatch):
    import vr_hotspotd.engine.hostapd_nat_engine as eng

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ip", "link", "set", "wlan1", "up"], timeout=0.01, output="", stderr="")

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="cmd_timeout"):
        eng._run(["ip", "link", "set", "wlan1", "up"], timeout_s=0.01)


def test_start_5ghz_strict_pop_degrades_probe_errors_and_uses_default_candidates(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    probe_payload = {
        "wifi": {
            "errors": [
                {"code": "driver_no_ap_mode_5ghz", "context": {"phy": "phy9"}},
                {"code": "driver_no_ap_mode_5ghz", "context": {"phy": "phy9", "reason": "no_5ghz_channels"}},
                {"code": "driver_no_vht80_or_he80", "context": {"phy": "phy9"}},
            ],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [],
        }
    }
    inv = {
        "adapters": [
            {
                "ifname": "wlx7419f816af4c",
                "phy": "phy9",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            }
        ],
        "recommended": "wlx7419f816af4c",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    called = {"n": 0}

    def fake_attempt_start_candidate(**kwargs):
        called["n"] += 1
        cmd = kwargs.get("cmd") or []
        assert any("channel=36" == str(tok) for tok in cmd)
        return (
            lifecycle.APReadyInfo(
                ifname="x0wlx7419f816af",
                phy="phy9",
                ssid="VR-Hotspot",
                freq_mhz=5180,
                channel=36,
                channel_width_mhz=80,
            ),
            SimpleNamespace(pid=321, cmd=["fake"], started_ts=1),
            None,
            None,
            [],
            [],
        )

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "_attempt_start_candidate", fake_attempt_start_candidate)
    monkeypatch.setattr(
        lifecycle,
        "build_cmd_nat",
        lambda **kwargs: [f"channel={kwargs.get('channel')}", f"no_virt={kwargs.get('no_virt')}"],
    )
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_collect_affinity_pids", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle, "_watchdog_enabled", lambda _cfg: False)

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlx7419f816af4c",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=["ap_iface_not_up_prestart"],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="pop-probe-degrade",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=True,
    )

    assert res.code == "started"
    assert called["n"] == 1
    assert "wifi_probe_errors_degraded_platform_pop" in state.get("warnings", [])
    assert "wifi_probe_default_candidates_used" in state.get("warnings", [])


def test_start_5ghz_strict_non_pop_keeps_probe_errors_fatal(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    probe_payload = {
        "wifi": {
            "errors": [
                {"code": "driver_no_ap_mode_5ghz", "context": {"phy": "phy9"}},
                {"code": "driver_no_vht80_or_he80", "context": {"phy": "phy9"}},
            ],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [],
        }
    }
    inv = {
        "adapters": [
            {
                "ifname": "wlx7419f816af4c",
                "phy": "phy9",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            }
        ],
        "recommended": "wlx7419f816af4c",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlx7419f816af4c",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=[],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="non-pop-probe-fatal",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=False,
    )

    assert res.code == "start_failed"
    assert state.get("last_error") == "driver_no_ap_mode_5ghz"


def test_start_5ghz_strict_retries_no_virt_when_virtual_iface_missing(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    candidate = {
        "band": 5,
        "width": 80,
        "primary_channel": 36,
        "center_channel": 42,
        "country": "US",
        "flags": ["non_dfs"],
        "rationale": "test",
    }
    probe_payload = {
        "wifi": {
            "errors": [],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [candidate],
        }
    }
    inv = {
        "adapters": [
            {
                "ifname": "wlx7419f816af4c",
                "phy": "phy9",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            }
        ],
        "recommended": "wlx7419f816af4c",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    attempt_calls = {"n": 0}

    def fake_attempt_start_candidate(**_kwargs):
        attempt_calls["n"] += 1
        if attempt_calls["n"] == 1:
            return (
                None,
                SimpleNamespace(pid=111, cmd=["virt"], started_ts=1),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                [],
                [
                    "RuntimeError: cmd_failed rc=237 cmd=/usr/sbin/iw dev wlx7419f816af4c interface add x0wlx7419f816af type __ap out=command failed: No such device (-19)"
                ],
            )
        return (
            lifecycle.APReadyInfo(
                ifname="wlx7419f816af4c",
                phy="phy9",
                ssid="VR-Hotspot",
                freq_mhz=5180,
                channel=36,
                channel_width_mhz=80,
            ),
            SimpleNamespace(pid=112, cmd=["no-virt"], started_ts=2),
            None,
            None,
            [],
            [],
        )

    build_no_virt_calls = []

    def fake_build_cmd_nat(**kwargs):
        build_no_virt_calls.append(bool(kwargs.get("no_virt")))
        return [f"no_virt={kwargs.get('no_virt')}"]

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "_attempt_start_candidate", fake_attempt_start_candidate)
    monkeypatch.setattr(lifecycle, "build_cmd_nat", fake_build_cmd_nat)
    monkeypatch.setattr(lifecycle, "_prepare_ap_interface", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_kill_runtime_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_remove_conf_dirs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_cleanup_virtual_ap_ifaces", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_collect_affinity_pids", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle, "_watchdog_enabled", lambda _cfg: False)

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlx7419f816af4c",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=[],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="virt-missing-no-virt-retry",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=True,
    )

    assert res.code == "started"
    assert build_no_virt_calls[:2] == [False, True]
    assert "virt_iface_missing_retry_no_virt" in state.get("warnings", [])


def test_start_5ghz_strict_pop_timeout_retry_handles_hostapd_early_exit(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    candidate = {
        "band": 5,
        "width": 80,
        "primary_channel": 149,
        "center_channel": 155,
        "country": "US",
        "flags": ["non_dfs"],
        "rationale": "test",
    }
    probe_payload = {
        "wifi": {
            "errors": [],
            "warnings": [],
            "counts": {"dfs": 0},
            "candidates": [candidate],
        }
    }
    inv = {
        "adapters": [
            {
                "ifname": "wlx7419f816af4c",
                "phy": "phy9",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            }
        ],
        "recommended": "wlx7419f816af4c",
    }

    state = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        return dict(state)

    attempt_calls = {"n": 0}

    def fake_attempt_start_candidate(**_kwargs):
        attempt_calls["n"] += 1
        if attempt_calls["n"] == 1:
            return (
                None,
                SimpleNamespace(pid=501, cmd=["virt"], started_ts=1),
                "hostapd_failed",
                "engine_exited_early: rc=1",
                [],
                [],
            )
        return (
            lifecycle.APReadyInfo(
                ifname="wlx7419f816af4c",
                phy="phy9",
                ssid="VR-Hotspot",
                freq_mhz=5745,
                channel=149,
                channel_width_mhz=80,
            ),
            SimpleNamespace(pid=502, cmd=["no-virt"], started_ts=2),
            None,
            None,
            [],
            [],
        )

    build_no_virt_calls = []

    def fake_build_cmd_nat(**kwargs):
        build_no_virt_calls.append(bool(kwargs.get("no_virt")))
        return [f"no_virt={kwargs.get('no_virt')}"]

    monkeypatch.setattr(lifecycle.wifi_probe, "probe", lambda *_args, **_kwargs: probe_payload)
    monkeypatch.setattr(lifecycle, "_attempt_start_candidate", fake_attempt_start_candidate)
    monkeypatch.setattr(lifecycle, "build_cmd_nat", fake_build_cmd_nat)
    monkeypatch.setattr(lifecycle, "_prepare_ap_interface", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_kill_runtime_processes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lifecycle, "_remove_conf_dirs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "_cleanup_virtual_ap_ifaces", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_collect_affinity_pids", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle.system_tuning, "apply_runtime", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle.network_tuning, "apply", lambda *_args, **_kwargs: ({}, []))
    monkeypatch.setattr(lifecycle, "_watchdog_enabled", lambda _cfg: False)

    res = lifecycle._start_hotspot_5ghz_strict(
        cfg={"watchdog_enable": False},
        inv=inv,
        ap_ifname="wlx7419f816af4c",
        target_phy="phy9",
        ssid="VR-Hotspot",
        passphrase="password123",
        country="US",
        ap_security="wpa2",
        ap_ready_timeout_s=8.0,
        optimized_no_virt=False,
        debug=False,
        enable_internet=True,
        bridge_mode=False,
        bridge_name=None,
        bridge_uplink=None,
        gateway_ip="192.168.68.1",
        dhcp_start_ip="192.168.68.10",
        dhcp_end_ip="192.168.68.250",
        dhcp_dns="gateway",
        effective_wifi6=False,
        tuning_state={},
        start_warnings=[],
        fw_cfg={},
        firewall_backend="nftables",
        use_hostapd_nat=True,
        correlation_id="pop-timeout-retry-hostapd-early-exit",
        enforced_channel_5g=None,
        allow_fallback_40mhz=False,
        allow_dfs_channels=False,
        pop_timeout_retry_no_virt=True,
    )

    assert res.code == "started"
    assert build_no_virt_calls[:2] == [False, True]
    assert "platform_pop_timeout_retry_no_virt" in state.get("warnings", [])


def test_stdout_ap_not_ready_detects_disable_markers():
    import vr_hotspotd.lifecycle as lifecycle

    lines = [
        "wlx7419f816af4c: AP-ENABLED",
        "wlx7419f816af4c: AP-DISABLED",
        "wlx7419f816af4c: CTRL-EVENT-TERMINATING",
    ]

    assert lifecycle._stdout_has_ap_ready(lines) is True
    assert lifecycle._stdout_has_ap_not_ready(lines) is True


def test_attempt_start_candidate_iface_not_up_treats_busy_as_transient_while_running(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    res = SimpleNamespace(
        ok=True,
        pid=4242,
        cmd=["fake"],
        started_ts=1,
        exit_code=None,
        error=None,
        stdout_tail=[],
        stderr_tail=[],
    )

    monkeypatch.setattr(lifecycle, "start_engine", lambda *_args, **_kwargs: res)
    monkeypatch.setattr(lifecycle, "update_state", lambda **_kwargs: {})
    monkeypatch.setattr(
        lifecycle,
        "_wait_for_ap_ready",
        lambda *_args, **_kwargs: lifecycle.APReadyInfo(
            ifname="wlx7419f816af4c",
            phy="phy9",
            ssid="VR-Hotspot",
            freq_mhz=5180,
            channel=36,
            channel_width_mhz=80,
        ),
    )
    monkeypatch.setattr(lifecycle, "_iface_is_up", lambda _ifname: False)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up_with_grace", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        lifecycle,
        "get_tails",
        lambda: (
            [
                "Failed to request a scan of neighboring BSSes ret=-16 (Device or resource busy) - try to scan again",
                "wlx7419f816af4c: AP-DISABLED",
            ],
            [],
        ),
    )
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)

    ap_info, _res_out, failure_code, failure_detail, _out_tail, _err_tail = lifecycle._attempt_start_candidate(
        cmd=["fake"],
        firewalld_cfg={},
        target_phy="phy9",
        ap_ready_timeout_s=8.0,
        ssid="VR-Hotspot",
        adapter_ifname="wlx7419f816af4c",
        expected_ap_ifname="wlx7419f816af4c",
        require_band="5ghz",
        require_width_mhz=80,
    )

    assert ap_info is None
    assert failure_code == "ap_start_timed_out"
    assert failure_detail == "iface_not_up"


def test_attempt_start_candidate_classifies_ap_disabled(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    res = SimpleNamespace(
        ok=True,
        pid=4243,
        cmd=["fake"],
        started_ts=1,
        exit_code=None,
        error=None,
        stdout_tail=[],
        stderr_tail=[],
    )

    monkeypatch.setattr(lifecycle, "start_engine", lambda *_args, **_kwargs: res)
    monkeypatch.setattr(lifecycle, "update_state", lambda **_kwargs: {})
    monkeypatch.setattr(lifecycle, "_wait_for_ap_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "get_tails",
        lambda: (
            [
                "wlx7419f816af4c: interface state HT_SCAN->DISABLED",
                "wlx7419f816af4c: AP-DISABLED",
            ],
            [],
        ),
    )
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)

    ap_info, _res_out, failure_code, failure_detail, _out_tail, _err_tail = lifecycle._attempt_start_candidate(
        cmd=["fake"],
        firewalld_cfg={},
        target_phy="phy9",
        ap_ready_timeout_s=8.0,
        ssid="VR-Hotspot",
        adapter_ifname="wlx7419f816af4c",
        expected_ap_ifname="wlx7419f816af4c",
        require_band="5ghz",
        require_width_mhz=80,
    )

    assert ap_info is None
    assert failure_code == "hostapd_failed"
    assert failure_detail == "ap_disabled"
