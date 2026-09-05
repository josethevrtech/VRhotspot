import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vr_hotspotd.diagnostics import streaming_collectors as collectors
from vr_hotspotd.host_probes import CommandResult


AP_INFO = """Interface vr-ap0
    ifindex 8
    wdev 0x2
    addr 00:11:22:33:44:55
    ssid Private VR SSID
    type AP
    channel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
    txpower 17.00 dBm
"""
STATION_INFO = """Station aa:bb:cc:dd:ee:ff (on vr-ap0)
    inactive time: 6 ms
    rx bytes: 20000
    rx packets: 100
    tx bytes: 90000
    tx packets: 200
    tx retries: 14
    tx failed: 3
    signal: -48 dBm
    signal avg: -50 dBm
    tx bitrate: 1200.0 MBit/s
    rx bitrate: 800.0 MBit/s
    connected time: 100 seconds
"""


@pytest.fixture
def isolated_collector(monkeypatch, tmp_path):
    state = {"running": True, "phase": "running", "band": "5ghz",
             "adapter": "wlan1", "ap_interface": "vr-ap0"}
    cfg = {"ssid": "Private VR SSID", "wpa2_passphrase": "private-passphrase"}
    monkeypatch.setattr(collectors, "load_state", lambda: state)
    monkeypatch.setattr(collectors, "load_config_snapshot", lambda: cfg)
    monkeypatch.setattr(collectors.os, "uname", lambda: SimpleNamespace(release="test-kernel"), raising=False)
    monkeypatch.setattr(collectors, "_NET_ROOT", tmp_path)
    monkeypatch.setattr(collectors, "_adapter_facts", lambda _ifname: {"driver": "mt7921u"})
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "private-api-token")
    original_resolve = Path.resolve

    def fake_resolve(path, strict=False):
        if path.name == "phy80211" and path.parent.name in {"wlan1", "vr-ap0"}:
            return tmp_path / "phy0"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:3] == ["iw", "dev", "vr-ap0"]:
            text = AP_INFO if argv[-1] == "info" else STATION_INFO
        elif argv[0] == "tc":
            text = json.dumps([{"kind": "fq_codel", "drops": 2, "backlog": 0, "packets": 200}])
        elif argv[0] == "journalctl":
            text = json.dumps({"MESSAGE": "usb 1-2: reset SuperSpeed USB device private-passphrase",
                               "__REALTIME_TIMESTAMP": "1000000"})
        else:
            raise AssertionError("Unexpected collector command")
        return CommandResult(tuple(argv), 0, stdout=text)

    monkeypatch.setattr(collectors.host_probes, "run_command", run)
    return state, cfg, calls


def test_snapshot_uses_only_verified_passive_bounded_commands(isolated_collector):
    _state, _cfg, calls = isolated_collector
    result = collectors.collect_streaming_snapshot()
    assert [argv for argv, _ in calls] == [
        ["iw", "dev", "vr-ap0", "info"],
        ["iw", "dev", "vr-ap0", "station", "dump"],
        ["tc", "-j", "-s", "qdisc", "show", "dev", "vr-ap0"],
        ["journalctl", "-k", "--since=-3s", "-n", "32", "--no-pager", "-o", "json"],
    ]
    assert all(0 < kwargs["timeout_s"] <= 0.4 for _, kwargs in calls)
    assert all(kwargs["env"]["LC_ALL"] == "C" for _, kwargs in calls)
    assert result["radio"]["width_mhz"] == 80
    assert result["radio"]["reported_tx_power_dbm"] == 17
    assert result["stations"][0]["tx_packets"] == 200
    assert result["stations"][0]["tx_failed"] == 3
    rendered = json.dumps(result)
    for private in ("Private VR SSID", "aa:bb:cc:dd:ee:ff", "00:11:22:33:44:55", "private-passphrase", "private-api-token"):
        assert private not in rendered
    assert result["kernel_events"][0]["kind"] == "usb_reset"
    assert result["network_rtt_ms"] is None


def test_capture_reads_legacy_configuration_without_persisting_migrations(monkeypatch, tmp_path, isolated_collector):
    from vr_hotspotd import config
    target = tmp_path / 'legacy-config.json'
    raw = json.dumps({'version': 1, 'ssid': 'Private VR SSID', 'channel_width': 80})
    target.write_text(raw)
    monkeypatch.setattr(config, 'CONFIG_PATH', target)
    monkeypatch.setattr(config, '_write_atomic', lambda *a, **k: pytest.fail('capture wrote configuration'))
    monkeypatch.setattr(collectors, 'load_config_snapshot', config.load_config_snapshot)
    result = collectors.collect_streaming_snapshot()
    assert result['status'] == 'ok'
    assert target.read_text() == raw


def test_wrong_ssid_never_queries_stations_or_queue(isolated_collector):
    _state, cfg, calls = isolated_collector
    cfg["ssid"] = "Different hotspot"
    result = collectors.collect_streaming_snapshot()
    assert result["radio_status"] == "identity_or_radio_unavailable"
    assert all("station" not in argv and "tc" not in argv for argv, _ in calls)
    assert "stations" not in result


@pytest.mark.parametrize("ap", ["../../etc", "wlan;echo", "a" * 16, None])
def test_invalid_ap_name_never_reaches_a_command(isolated_collector, ap):
    state, _cfg, calls = isolated_collector
    state["ap_interface"] = ap
    result = collectors.collect_streaming_snapshot()
    assert all(argv[0] == "journalctl" for argv, _ in calls)
    assert "stations" not in result


def test_phy_mismatch_never_queries_radio_or_stations(monkeypatch, isolated_collector, tmp_path):
    _state, _cfg, calls = isolated_collector
    monkeypatch.setattr(Path, "resolve", lambda path, strict=False: tmp_path / path.parent.name)
    result = collectors.collect_streaming_snapshot()
    assert all(argv[0] == "journalctl" for argv, _ in calls)
    assert "radio" not in result


def test_stopped_hotspot_never_queries_radio(isolated_collector):
    state, _cfg, calls = isolated_collector
    state["running"] = False
    assert collectors.collect_streaming_snapshot()["radio_status"] == "hotspot_not_running"
    assert all(argv[0] == "journalctl" for argv, _ in calls)


@pytest.mark.parametrize("flags, expected", [
    ({"missing": True}, "missing_command"), ({"timed_out": True}, "timeout"),
    ({"permission_denied": True}, "permission_denied"), ({}, "failed"),
])
def test_command_errors_never_export_raw_secrets(monkeypatch, flags, expected):
    monkeypatch.setattr(collectors.host_probes, "run_command", lambda argv, **kwargs:
                        CommandResult(tuple(argv), 1, stdout="private raw output", stderr="secret error", **flags))
    result = collectors._command(["iw", "dev"], collectors.time.monotonic() + 1)
    assert result["status"] == expected
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_exhausted_budget_does_not_spawn_command(monkeypatch):
    calls = []
    monkeypatch.setattr(collectors.host_probes, "run_command", lambda *args, **kwargs: calls.append(args))
    assert collectors._command(["iw", "dev"], collectors.time.monotonic() - 1) == {"status": "budget_exhausted"}
    assert not calls


def test_station_ids_are_stable_within_daemon_and_do_not_expose_mac():
    first = collectors._stations(STATION_INFO)
    second = collectors._stations(STATION_INFO)
    assert first == second
    assert len(first[0]["station_id"]) == 16
    assert "aa:bb:cc:dd:ee:ff" not in json.dumps(first)
    assert "mac" not in first[0]


def test_kernel_events_are_bounded_classifications_not_raw_messages():
    records = [json.dumps({"MESSAGE": "usb 1-2: reset SuperSpeed USB device 192.168.1.2 private-user",
                           "__REALTIME_TIMESTAMP": "1000"}) for _ in range(100)]
    events = collectors._kernel_events("\n".join(records))
    assert len(events) == 16
    assert all(event["scope"] == "host_event_not_proven_adapter_specific" for event in events)
    assert "192.168.1.2" not in json.dumps(events)
    assert "private-user" not in json.dumps(events)


def test_known_secret_scrubbing_handles_json_punctuation(monkeypatch, isolated_collector):
    state, _cfg, _calls = isolated_collector
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", ":")
    state["phase"] = "phase:secret"
    result = collectors.collect_streaming_snapshot()
    assert result["status"] == "ok"
    assert ":" not in result["hotspot"]["phase"]


def test_known_passphrase_is_scrubbed_in_arbitrary_nested_keys_and_values(monkeypatch, isolated_collector):
    state, cfg, _calls = isolated_collector
    secret = 'a-private-"quoted-secret'
    cfg["wpa2_passphrase"] = secret
    state["phase"] = {secret: ["prefix:" + secret]}
    result = collectors.collect_streaming_snapshot()
    assert secret not in json.dumps(result)
    assert json.dumps(secret)[1:-1] not in json.dumps(result)


def test_radio_power_belongs_to_verified_interface_only():
    other = AP_INFO.replace("vr-ap0", "other-ap").replace("17.00", "30.00")
    result = collectors._radio(AP_INFO + other, "vr-ap0", "Private VR SSID")
    assert result["reported_tx_power_dbm"] == 17


def test_summary_counts_evidence_without_claiming_network_loss_or_vr_grade():
    first = {"hotspot": {"running": True, "ap_interface": "vr-ap0"},
             "radio": {"frequency_mhz": 5180, "width_mhz": 80},
             "stations": [{"station_id": "headset", "tx_packets": 100, "tx_failed": 1}],
             "kernel_events": [{"kind": "usb_reset", "unix_time_us": "1"}]}
    second = {"hotspot": {"running": True, "ap_interface": "vr-ap0"},
              "radio": {"frequency_mhz": 5180, "width_mhz": 80},
              "stations": [{"station_id": "headset", "tx_packets": 190, "tx_failed": 11}],
              "kernel_events": [{"kind": "usb_reset", "unix_time_us": "1"}]}
    third = {"hotspot": {"running": False, "ap_interface": "vr-ap0"},
             "radio": {"frequency_mhz": 5200, "width_mhz": 80}}
    report = {"samples": [{"data": first}, {"data": second}, {"data": third},
                          {"data": {"error": "sample_unavailable"}}]}
    result = collectors.summarize_streaming_report(report)
    assert result["observed_hotspot_interruptions"] == 1
    assert result["observed_radio_changes"] == 1
    assert result["max_driver_tx_failure_ratio_pct"] == 10
    assert result["kernel_event_count"] == 1
    assert result["samples_without_verified_radio"] == 1
    assert result["network_rtt_p99_ms"] is None
    assert result["receiver_packet_loss_pct"] is None
    assert result["vr_qualified"] is False


def test_summary_ignores_counter_resets_and_missing_samples():
    samples = []
    for tx, failed in [(100, 10), (10, 1)]:
        samples.append({"data": {"stations": [{"station_id": "headset", "tx_packets": tx, "tx_failed": failed}]}})
    result = collectors.summarize_streaming_report({"samples": samples})
    assert result["max_driver_tx_failure_ratio_pct"] is None


def test_busy_sample_keeps_radio_and_one_station_with_explicit_omissions():
    data = {'radio': {'width_mhz': 80}, 'stations': [
        {'station_id': str(i), 'tx_packets': i, 'extra': 'x' * 500} for i in range(8)
    ], 'kernel_events': [{'kind': 'usb_reset', 'extra': 'x' * 200} for _ in range(16)]}
    result = collectors._fit_sample(data)
    assert len(json.dumps(result).encode()) <= collectors.MAX_SAMPLE_BYTES
    assert result['radio']['width_mhz'] == 80
    assert result['stations']
    assert result['omitted_for_size']


def test_summary_handles_unknown_shapes_and_numeric_strings():
    report = {'samples': [None, {'data': {'hotspot': None, 'stations': [None]}},
        {'data': {'stations': [{'station_id': 's', 'tx_packets': '10', 'tx_failed': '0'}]}},
        {'data': {'stations': [{'station_id': 's', 'tx_packets': '19', 'tx_failed': '1'}]}}]}
    for sample in report['samples'][2:]:
        sample['data'].update(hotspot={'running': True, 'ap_interface': 'ap0'},
                              radio={'frequency_mhz': 5180, 'width_mhz': 80})
    result = collectors.summarize_streaming_report(report)
    assert result['max_driver_tx_failure_ratio_pct'] == 10


@pytest.mark.parametrize('change', ['adapter', 'ap_interface', 'channel', 'reconnect', 'not_running'])
def test_summary_does_not_difference_station_counters_across_identity_changes(change):
    first = {'hotspot': {'running': True, 'adapter': 'wlan0', 'ap_interface': 'ap0'},
             'radio': {'frequency_mhz': 5180, 'width_mhz': 80},
             'stations': [{'station_id': 's', 'tx_packets': 10, 'tx_failed': 0, 'connected_time_s': 100}]}
    second = json.loads(json.dumps(first))
    second['stations'][0].update(tx_packets=19, tx_failed=1, connected_time_s=102)
    if change in ('adapter', 'ap_interface'):
        second['hotspot'][change] = 'different'
    elif change == 'channel':
        second['radio']['frequency_mhz'] = 5745
    elif change == 'reconnect':
        second['stations'][0]['connected_time_s'] = 1
    else:
        second['hotspot']['running'] = False
    result = collectors.summarize_streaming_report({'samples': [{'data': first}, {'data': second}]})
    assert result['max_driver_tx_failure_ratio_pct'] is None
