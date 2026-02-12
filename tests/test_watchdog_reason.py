import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_watchdog_reason_accepts_engine_children_when_pidfiles_missing(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    st = {
        "adapter": "wlx7419f816af4c",
        "ap_interface": "wlx7419f816af4c",
        "engine": {"pid": 4321},
    }
    cfg = {"bridge_mode": False, "connection_quality_monitoring": False}

    monkeypatch.setattr(lifecycle, "_find_latest_conf_dir", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(lifecycle, "_hostapd_pid_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_dnsmasq_pid_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_hostapd_ready", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_pid_running", lambda pid: pid == 4321)
    monkeypatch.setattr(lifecycle, "_child_pids", lambda pid: [111, 222] if pid == 4321 else [])
    monkeypatch.setattr(lifecycle, "_pid_is_hostapd", lambda pid: pid == 111)
    monkeypatch.setattr(lifecycle, "_pid_is_dnsmasq", lambda pid: pid == 222)

    reason = lifecycle._watchdog_reason(st, cfg)
    assert reason is None


def test_watchdog_reason_reports_hostapd_exited_when_no_fallback_signal(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    st = {
        "adapter": "wlx7419f816af4c",
        "ap_interface": "wlx7419f816af4c",
        "engine": {"pid": 4321},
    }
    cfg = {"bridge_mode": False, "connection_quality_monitoring": False}

    monkeypatch.setattr(lifecycle, "_find_latest_conf_dir", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(lifecycle, "_hostapd_pid_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_dnsmasq_pid_running", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_hostapd_ready", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "_pid_running", lambda _pid: False)

    reason = lifecycle._watchdog_reason(st, cfg)
    assert reason == "hostapd_exited"


def test_restart_from_watchdog_skips_when_not_running(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle

    called = {"stop": 0, "start": 0}

    monkeypatch.setattr(lifecycle, "load_state", lambda: {"running": False, "phase": "stopped"})
    monkeypatch.setattr(lifecycle, "_stop_hotspot_impl", lambda **_kwargs: called.__setitem__("stop", called["stop"] + 1))
    monkeypatch.setattr(lifecycle, "_start_hotspot_impl", lambda **_kwargs: called.__setitem__("start", called["start"] + 1))

    lifecycle._restart_from_watchdog("hostapd_exited")

    assert called["stop"] == 0
    assert called["start"] == 0
