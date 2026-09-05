from copy import deepcopy
import pytest
from vr_hotspotd import telemetry


@pytest.fixture(autouse=True)
def clear_samples():
    telemetry._LAST_SAMPLE.clear()
    telemetry._LAST_TS = None
    telemetry._LAST_RESULT = None
    telemetry._LAST_IDENTITY = None
    yield
    telemetry._LAST_SAMPLE.clear()
    telemetry._LAST_TS = None
    telemetry._LAST_RESULT = None
    telemetry._LAST_IDENTITY = None


def station(packets=10, failed=0):
    return {'mac': 'aa:bb:cc:dd:ee:ff', 'tx_packets': packets,
            'tx_failed': failed, 'tx_retries': 1, 'tx_bytes': packets * 1000,
            'signal_dbm': -45, 'tx_bitrate_mbps': 600}


def test_monotonic_deltas_survive_backward_wall_clock(monkeypatch):
    ticks, wall = iter([0.0, 2.0]), iter([1000, 200])
    clients = iter([[station()], [station(20, 2)]])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry.time, 'time', lambda: next(wall))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(clients)})
    first = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    second = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert first['clients'][0]['tx_pps'] is None
    assert second['clients'][0]['tx_pps'] == 5
    assert second['ts'] == 200
    assert second['clients'][0]['loss_pct'] == pytest.approx(100 * 2 / 12)
    assert 'not receiver-measured' in second['metric_notes']['loss_pct']


def test_cached_result_is_not_mutable_by_callers(monkeypatch):
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: 10)
    calls = []
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: calls.append(k) or {'clients': [station()]})
    first = telemetry.get_snapshot(adapter_ifname='wlan1')
    expected = deepcopy(first)
    first['clients'][0]['signal_dbm'] = -999
    assert telemetry.get_snapshot(adapter_ifname='wlan1') == expected
    assert len(calls) == 1


def test_switching_radio_never_reuses_another_radios_counters(monkeypatch):
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: 10)
    calls = []
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: calls.append(a) or {'clients': [station()]})
    telemetry.get_snapshot(adapter_ifname='wlan1', ap_interface_hint='ap1')
    other = telemetry.get_snapshot(adapter_ifname='wlan2', ap_interface_hint='ap2')
    assert len(calls) == 2
    assert other['clients'][0]['tx_pps'] is None


def test_disconnect_prunes_previous_counters_and_reconnect_has_fresh_baseline(monkeypatch):
    ticks = iter([1, 3, 5])
    clients = iter([[station()], [], [station(30)]])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(clients)})
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert telemetry._LAST_SAMPLE == {}
    result = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert result['clients'][0]['tx_pps'] is None


def test_first_sample_and_idle_counters_do_not_report_perfect_quality(monkeypatch):
    ticks = iter([1, 3])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': [station()]})
    first = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    idle = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    for result in (first, idle):
        assert result['clients'][0]['quality_score'] is None
        assert result['summary']['quality_score_avg'] is None
        assert result['summary']['quality_score_min'] is None


@pytest.mark.parametrize('missing_field', ['tx_packets', 'tx_failed', 'tx_retries', 'signal_dbm', 'tx_bitrate_mbps'])
def test_partial_evidence_is_not_scored_as_perfect(monkeypatch, missing_field):
    ticks = iter([1, 3])
    second = station(20, 2)
    second[missing_field] = None
    snapshots = iter([[station()], [second]])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(snapshots)})
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    result = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert result['clients'][0]['quality_score'] is None
    if missing_field in {'tx_packets', 'tx_failed'}:
        assert result['clients'][0]['loss_pct'] is None


def test_complete_counter_deltas_produce_advisory_quality(monkeypatch):
    ticks = iter([1, 3])
    snapshots = iter([[station()], [station(20)]])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(snapshots)})
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    result = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert result['clients'][0]['loss_pct'] == 0
    assert result['clients'][0]['retry_pct'] == 0
    assert 0 <= result['clients'][0]['quality_score'] <= 100


def test_fallback_actual_ap_change_resets_unchanged_requested_identity(monkeypatch):
    ticks = iter([1, 3, 5])
    snapshots = iter([
        {'ap_interface': 'ap1', 'clients': [station()]},
        {'ap_interface': 'fallback-ap2', 'clients': [station(100, 2)]},
        {'ap_interface': 'fallback-ap2', 'clients': [station(110, 2)]},
    ])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: next(snapshots))
    telemetry.get_snapshot(adapter_ifname='wlan1', ap_interface_hint='ap1', interval_s=0)
    changed = telemetry.get_snapshot(adapter_ifname='wlan1', ap_interface_hint='ap1', interval_s=0)
    assert changed['ap_interface'] == 'fallback-ap2'
    assert changed['clients'][0]['tx_pps'] is None
    assert changed['clients'][0]['quality_score'] is None
    stable = telemetry.get_snapshot(adapter_ifname='wlan1', ap_interface_hint='ap1', interval_s=0)
    assert stable['clients'][0]['tx_pps'] == 5
    assert stable['clients'][0]['quality_score'] is not None


def test_reconnect_between_samples_resets_even_increasing_counters(monkeypatch):
    ticks = iter([1, 3, 5])
    snapshots = iter([
        [dict(station(), connected_time_s=90)],
        [dict(station(100, 2), connected_time_s=1)],
        [dict(station(110, 2), connected_time_s=3)],
    ])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(snapshots)})
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    reconnected = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert reconnected['clients'][0]['quality_score'] is None
    assert reconnected['clients'][0]['loss_pct'] is None
    assert reconnected['clients'][0]['tx_mbps'] is None
    stable = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert stable['clients'][0]['tx_pps'] == 5


def test_partial_client_coverage_does_not_report_healthy_aggregate(monkeypatch):
    ticks = iter([1, 3])
    snapshots = iter([
        [station()],
        [station(20), dict(station(), mac='aa:bb:cc:dd:ee:00')],
    ])
    monkeypatch.setattr(telemetry.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(telemetry, 'get_clients_snapshot', lambda *a, **k: {'clients': next(snapshots)})
    telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    result = telemetry.get_snapshot(adapter_ifname='wlan1', interval_s=0)
    assert result['clients'][0]['quality_score'] is not None
    assert result['clients'][1]['quality_score'] is None
    assert result['summary']['quality_score_avg'] is None
    assert result['summary']['quality_score_min'] is None
