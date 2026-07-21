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


def _exercise_watchdog_channel_switch(
    monkeypatch,
    *,
    band,
    selected_channel,
    fallback_channel_2g=6,
    persistence_error=None,
):
    import vr_hotspotd.lifecycle as lifecycle

    state = {
        "running": True,
        "phase": "running",
        "adapter": "wlan0",
        "band": band,
        "warnings": [],
    }
    cfg = {
        "auto_channel_switch": True,
        "fallback_channel_2g": fallback_channel_2g,
        "channel_5g": 36,
        "channel_6g": 5,
    }
    writes = []
    restart_calls = []

    def fake_write_config_file(updates):
        writes.append(dict(updates))
        if persistence_error is not None:
            raise persistence_error
        cfg.update(updates)
        return dict(cfg)

    monkeypatch.setattr(lifecycle, "load_state", lambda: state)
    monkeypatch.setattr(lifecycle, "update_state", lambda **updates: state.update(updates))
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(
        lifecycle,
        "select_best_channel",
        lambda adapter_ifname, requested_band: selected_channel,
    )
    monkeypatch.setattr(lifecycle, "write_config_file", fake_write_config_file)
    monkeypatch.setattr(
        lifecycle,
        "_stop_hotspot_impl",
        lambda **_kwargs: restart_calls.append("stop"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_start_hotspot_impl",
        lambda **_kwargs: restart_calls.append("start"),
    )

    lifecycle._restart_from_watchdog("connection_quality_degraded:score=40.0")

    return cfg, writes, restart_calls


def test_watchdog_5ghz_channel_switch_persists_channel_5g(monkeypatch):
    cfg, writes, restart_calls = _exercise_watchdog_channel_switch(
        monkeypatch,
        band="5ghz",
        selected_channel=149,
    )

    assert writes == [{"channel_5g": 149}]
    assert cfg["channel_5g"] == 149
    assert restart_calls == ["stop", "start"]


def test_watchdog_5ghz_channel_switch_preserves_2g_fallback(monkeypatch):
    cfg, writes, _restart_calls = _exercise_watchdog_channel_switch(
        monkeypatch,
        band="5ghz",
        selected_channel=149,
        fallback_channel_2g=11,
    )

    assert writes == [{"channel_5g": 149}]
    assert cfg["fallback_channel_2g"] == 11


def test_watchdog_2g_channel_switch_persists_fallback_channel_2g(monkeypatch):
    cfg, writes, _restart_calls = _exercise_watchdog_channel_switch(
        monkeypatch,
        band="2.4ghz",
        selected_channel=1,
    )

    assert writes == [{"fallback_channel_2g": 1}]
    assert cfg["fallback_channel_2g"] == 1


def test_watchdog_6ghz_channel_switch_persists_channel_6g(monkeypatch):
    cfg, writes, _restart_calls = _exercise_watchdog_channel_switch(
        monkeypatch,
        band="6ghz",
        selected_channel=37,
    )

    assert writes == [{"channel_6g": 37}]
    assert cfg["channel_6g"] == 37


def test_watchdog_config_persistence_failure_remains_best_effort(monkeypatch):
    _cfg, writes, restart_calls = _exercise_watchdog_channel_switch(
        monkeypatch,
        band="5ghz",
        selected_channel=149,
        persistence_error=OSError("config write failed"),
    )

    assert writes == [{"channel_5g": 149}]
    assert restart_calls == ["stop", "start"]
