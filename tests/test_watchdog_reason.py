import os
import sys
import logging

import pytest


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


def _unexpected_mutation(*_args, **_kwargs):
    raise AssertionError("A healthy stream must not scan, restart, or change configuration/power")


@pytest.mark.parametrize("band", ["2.4ghz", "5ghz", "6ghz"])
@pytest.mark.parametrize("reason", [
    "connection_quality_degraded:score=40.0",
    "connection_quality_degraded:loss=8.0%",
    "connection_quality_degraded:rssi=-90dBm",
    "unknown_reason",
])
def test_quality_callback_cannot_restart_or_switch_channel(monkeypatch, band, reason):
    import vr_hotspotd.lifecycle as lifecycle
    state = {"running": True, "phase": "running", "band": band}
    cfg = {"auto_channel_switch": True, "channel_5g": 36, "channel_6g": 5}
    original = dict(cfg)
    calls = []
    monkeypatch.setattr(lifecycle, "load_state", lambda: state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    for name in ("select_best_channel", "write_config_file", "_stop_hotspot_impl", "_start_hotspot_impl"):
        monkeypatch.setattr(lifecycle, name, lambda *_args, _name=name, **_kwargs: calls.append(_name))
    lifecycle._restart_from_watchdog(reason)
    assert cfg == original
    assert calls == []


@pytest.mark.parametrize("conf_found", [True, False])
def test_healthy_processes_are_not_failed_by_rf_quality(monkeypatch, conf_found):
    import vr_hotspotd.lifecycle as lifecycle
    state = {"adapter": "wlan1", "ap_interface": "x0wlan1", "engine": {"pid": 4321}}
    monkeypatch.setattr(lifecycle, "_find_latest_conf_dir", lambda *_args: object() if conf_found else None)
    monkeypatch.setattr(lifecycle, "_hostapd_pid_running", lambda *_args: True)
    monkeypatch.setattr(lifecycle, "_dnsmasq_pid_running", lambda *_args: True)
    monkeypatch.setattr(lifecycle, "_pid_running", lambda _pid: True)
    monkeypatch.setattr(lifecycle, "_child_pids", lambda _pid: [111, 222])
    monkeypatch.setattr(lifecycle, "_pid_is_hostapd", lambda pid: pid == 111)
    monkeypatch.setattr(lifecycle, "_pid_is_dnsmasq", lambda pid: pid == 222)
    monkeypatch.setattr(lifecycle, "_check_connection_quality", lambda *_args: "connection_quality_degraded:score=1")
    assert lifecycle._watchdog_reason(state, {"connection_quality_monitoring": True}) is None


class _Ticks:
    def __init__(self, count):
        self.remaining = count

    def is_set(self):
        return self.remaining <= 0

    def wait(self, _interval):
        self.remaining -= 1
        return False


def test_watchdog_observes_quality_without_disrupting_stream(monkeypatch, caplog):
    from vr_hotspotd import lifecycle, telemetry
    from vr_hotspotd.engine import tx_power
    cfg = {"watchdog_enable": True, "connection_quality_monitoring": True,
           "auto_channel_switch": True, "tx_power": None}
    state = {"running": True, "phase": "running", "adapter": "wlan1", "ap_interface": "x0wlan1"}
    samples = iter([
        {"quality_score_avg": 20, "loss_pct_avg": 10, "rssi_avg_dbm": -40},
        {"quality_score_avg": 25, "loss_pct_avg": 8, "rssi_avg_dbm": -40},
        {},  # Unavailable counters must not be reported as recovery.
        {"quality_score_avg": 90, "loss_pct_avg": 0, "rssi_avg_dbm": -40},
    ])
    calls = []
    monkeypatch.setattr(lifecycle, "_WATCHDOG_STOP", _Ticks(4))
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "load_state", lambda: state)
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "_watchdog_reason", lambda *_args: None)
    monkeypatch.setattr(telemetry, "get_snapshot", lambda **_kwargs: {"enabled": True, "summary": next(samples)})
    for name in ("select_best_channel", "write_config_file", "_restart_from_watchdog"):
        monkeypatch.setattr(lifecycle, name, lambda *_args, _name=name, **_kwargs: calls.append(_name))

    def record_power(*_args, **_kwargs):
        calls.append("set_tx_power")
        return True, "ok"

    monkeypatch.setattr(tx_power, "set_tx_power", record_power)
    # Cover the old imported aliases as well as a module-qualified call.
    monkeypatch.setattr(lifecycle, "set_tx_power", record_power, raising=False)
    monkeypatch.setattr(lifecycle, "get_tx_power", lambda *_args: 20, raising=False)
    monkeypatch.setattr(lifecycle, "auto_adjust_tx_power", lambda *_args: 17, raising=False)
    with caplog.at_level(logging.INFO, logger="vr_hotspotd.lifecycle"):
        lifecycle._watchdog_loop()
    records = [r for r in caplog.records if r.name == "vr_hotspotd.lifecycle"]
    assert [r.message for r in records] == ["connection_quality_degraded", "connection_quality_recovered"]
    assert records[0].action == "advisory_only"
    assert cfg["tx_power"] is None
    assert calls == []


def test_watchdog_still_recovers_a_dead_engine(monkeypatch):
    import vr_hotspotd.lifecycle as lifecycle
    calls = []
    cfg = {"watchdog_enable": True}
    state = {"running": True, "phase": "running", "warnings": []}
    monkeypatch.setattr(lifecycle, "_WATCHDOG_STOP", _Ticks(1))
    monkeypatch.setattr(lifecycle, "load_config", lambda: cfg)
    monkeypatch.setattr(lifecycle, "load_state", lambda: state)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "update_state", lambda **values: state.update(values))
    monkeypatch.setattr(lifecycle, "_stop_hotspot_impl", lambda **_kwargs: calls.append("stop"))
    monkeypatch.setattr(lifecycle, "_start_hotspot_impl", lambda **_kwargs: calls.append("start"))
    lifecycle._watchdog_loop()
    assert calls == ["stop", "start"]
    assert "watchdog_restart:engine_not_running" in state["warnings"]
