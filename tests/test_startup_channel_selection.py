from copy import deepcopy
import pytest
from vr_hotspotd import lifecycle


@pytest.mark.parametrize('band,width', [('5ghz', 80), ('6ghz', '80'), ('2.4ghz', '20')])
def test_startup_choice_uses_requested_width_without_persisting(monkeypatch, band, width):
    cfg = {'channel_auto_select': True, 'channel_5g': None, 'channel_6g': None}
    before = deepcopy(cfg)
    calls = []
    monkeypatch.setattr(lifecycle, 'select_best_channel', lambda *args, **kw: calls.append((args, kw)) or 149)
    monkeypatch.setattr(lifecycle, 'write_config_file', lambda *a, **k: pytest.fail('automatic choice must not become manual'))
    assert lifecycle._startup_channel(cfg, 'wlan1', band, None, width, []) == 149
    assert cfg == before
    assert calls == [(('wlan1', band, None), {'width_mhz': width})]


@pytest.mark.parametrize('automatic,current', [(False, None), (True, 36)])
def test_manual_or_disabled_selection_does_not_scan(monkeypatch, automatic, current):
    monkeypatch.setattr(lifecycle, 'select_best_channel', lambda *a, **k: pytest.fail('must not scan'))
    assert lifecycle._startup_channel({'channel_auto_select': automatic}, 'wlan1', '5ghz', current, 80, []) == current


def test_unavailable_auto_scan_is_reported_without_inventing_channel(monkeypatch):
    monkeypatch.setattr(lifecycle, 'select_best_channel', lambda *a, **k: None)
    warnings = []
    assert lifecycle._startup_channel({'channel_auto_select': True}, 'wlan1', '5ghz', None, 80, warnings) is None
    assert warnings == ['channel_auto_selection_unavailable']


@pytest.mark.parametrize("automatic,manual,expected,does_scan", [
    (True, None, 149, True),
    (True, 0, 149, True),
    (True, 153, 153, False),
    (False, 153, 153, False),
    (False, None, None, False),
])
def test_usb_startup_respects_manual_or_auto_channel(
    monkeypatch, mock_missing_system_commands, automatic, manual, expected, does_scan,
):
    # Reuse the established isolated lifecycle environment rather than a new
    # mock startup implementation. Stop at the real strict path's probe gate.
    from tests.test_passphrase_autoprovision import _common_start_mocks

    cfg = {
        "ssid": "VR Test", "wpa2_passphrase": "abcdefgh12345",
        "band_preference": "5ghz", "ap_adapter": "wlan1",
        "channel_auto_select": automatic, "channel_5g": manual,
        "channel_width": "80",
    }
    before = deepcopy(cfg)
    _common_start_mocks(monkeypatch, cfg)
    monkeypatch.setattr(lifecycle, "_maybe_set_regdom", lambda *_args: None)
    monkeypatch.setattr(lifecycle, "_safe_revert_tuning", lambda *_args: [])
    scan_calls, probe_calls, writes = [], [], []

    def choose(*args, **kwargs):
        scan_calls.append((args, kwargs))
        return 149

    def probe(*args, **kwargs):
        probe_calls.append((args, kwargs))
        return {"wifi": {"errors": [{"code": "test_probe_stop"}], "warnings": []}}

    monkeypatch.setattr(lifecycle, "select_best_channel", choose)
    monkeypatch.setattr(lifecycle.wifi_probe, "probe", probe)
    monkeypatch.setattr(lifecycle, "write_config_file", lambda value: writes.append(value))
    result = lifecycle._start_hotspot_impl(correlation_id="usb-channel-regression")

    assert result.code == "start_failed"  # Deliberate gate; no engine starts.
    assert len(probe_calls) == 1
    assert probe_calls[0][1]["preferred_primary_channel"] == expected
    assert bool(scan_calls) is does_scan
    if does_scan:
        assert scan_calls == [(("wlan1", "5ghz", None), {"width_mhz": 80})]
    assert cfg == before
    assert writes == []
