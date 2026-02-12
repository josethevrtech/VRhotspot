import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_stop_performs_forced_cleanup_when_state_stopped_but_runtime_present(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    state = {
        "phase": "stopped",
        "adapter": "wlan1",
        "tuning": {},
        "network_tuning": {},
    }
    updates = []
    calls = {}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        updates.append(dict(kwargs))
        return dict(state)

    monkeypatch.setattr(lifecycle, "load_state", lambda: dict(state))
    monkeypatch.setattr(lifecycle, "load_config", lambda: {})
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "_safe_revert_tuning", lambda _s: [])
    monkeypatch.setattr(lifecycle, "_safe_revert_network_tuning", lambda _s: [])
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_find_our_lnxrouter_pids", lambda: [])
    monkeypatch.setattr(lifecycle, "_find_hostapd_pids", lambda _a: [])
    monkeypatch.setattr(lifecycle, "_find_dnsmasq_pids", lambda _a: [])
    monkeypatch.setattr(lifecycle, "stop_engine", lambda firewalld_cfg=None: (True, 0, [], [], None))
    monkeypatch.setattr(
        lifecycle,
        "_kill_runtime_processes",
        lambda adapter_ifname, firewalld_cfg=None, stop_engine_first=True: calls.setdefault(
            "stop_engine_first", stop_engine_first
        ),
    )
    monkeypatch.setattr(lifecycle, "_remove_conf_dirs", lambda _a: [])
    monkeypatch.setattr(lifecycle, "_cleanup_virtual_ap_ifaces", lambda target_phy=None: [])

    res = lifecycle.stop_hotspot(correlation_id="t-stop")

    assert res.code == "stopped"
    assert calls.get("stop_engine_first") is True
    assert any(u.get("phase") == "stopping" for u in updates)
