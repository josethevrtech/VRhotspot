from copy import deepcopy
from types import SimpleNamespace

import pytest

from vr_hotspotd.adapters.radio import (
    apply_automatic, non_dfs_ap_options, recommend, verify_automatic_link,
)
from tests.test_channel_scan import phy_text


def radio_text(band='6ghz', *, ap=True, blocked=None):
    channels = range(1, 30, 4) if band == '6ghz' else range(36, 65, 4)
    text = phy_text(channels, band, blocked).replace(' MHz', '.0 MHz')
    capabilities = ('\t\tHE Iftypes: managed\n\t\t\tHE40/HE80/5GHz\n\t\t\tHE160/5GHz\n'
                    + ('\t\tHE Iftypes: AP\n\t\t\tHE40/HE80/5GHz\n\t\t\tHE160/5GHz\n' if ap else ''))
    return text.replace('\tBand 1:', '\tSupported interface modes:\n\t\t* managed\n\t\t* AP\n\tBand 1:').replace('\t\tFrequencies:', capabilities + '\t\tFrequencies:')


def test_auto_selects_widest_then_highest_band():
    options = non_dfs_ap_options(radio_text())
    assert recommend(options) == {'band': '6ghz', 'width_mhz': 160, 'channel': 5, 'security': 'wpa3_sae'}
    assert recommend([('6ghz', 80, 5), ('5ghz', 160, 36)])['width_mhz'] == 160
    assert recommend([('5ghz', 80, 149), ('5ghz', 80, 36)])['channel'] == 36


@pytest.mark.parametrize('options', [[], [('2.4ghz', 40, 6)], [('5ghz', 40, 36)], [('6ghz', 20, 5)]])
def test_vr_automatic_minimum_is_80(options):
    assert recommend(options) is None


def test_client_he_does_not_authorize_ap():
    assert recommend(non_dfs_ap_options(radio_text(ap=False))) is None


@pytest.mark.parametrize('flag', ['(no IR)', '(disabled)', '(radar detection)', '(no 160MHz)'])
def test_full_bonded_block_must_be_permitted(flag):
    plan = recommend(non_dfs_ap_options(radio_text(blocked={29: flag})))
    assert plan['width_mhz'] == 80


def test_vht_80_without_he():
    text = radio_text('5ghz', ap=False).replace('HE Iftypes: managed', 'VHT Capabilities (0):\n\t\tHE Iftypes: managed')
    assert recommend(non_dfs_ap_options(text))['width_mhz'] == 80


def test_runtime_resolution_keeps_saved_config_and_never_uses_40_fallback():
    cfg = {'radio_auto': True, 'band_preference': '2.4ghz', 'channel_width': '40', 'allow_fallback_40mhz': True}
    before = deepcopy(cfg)
    plan = recommend(non_dfs_ap_options(radio_text()))
    result = apply_automatic(cfg, {'bus': 'usb', 'automatic_radio': plan})
    assert (result['band_preference'], result['channel_width'], result['ap_security']) == ('6ghz', '160', 'wpa3_sae')
    assert result['allow_fallback_40mhz'] is False
    assert result['usb_autosuspend_disable'] is True
    assert cfg == before
    assert apply_automatic({'radio_auto': False}, {}) == {'radio_auto': False}


@pytest.mark.parametrize('freq,width,accepted', [(6135, 160, True), (5180, 80, True), (5180, 40, False), (6135, None, False), (None, 160, False), (2412, 80, False)])
def test_actual_width_is_verified(freq, width, accepted):
    info = SimpleNamespace(freq_mhz=freq, channel_width_mhz=width)
    warnings = []
    assert (verify_automatic_link({'radio_auto': True}, info, warnings) is info) == accepted
    assert bool(warnings) != accepted


def test_unknown_radio_blocks_before_host_mutation(monkeypatch):
    from tests.test_active_uplink_start_guard import _stub_read_only_start_environment, _forbid_mutations, _config
    from vr_hotspotd import lifecycle
    cfg = {**_config(ap_adapter='wlan1'), 'radio_auto': True}
    state, _, _ = _stub_read_only_start_environment(monkeypatch, config=cfg, active_uplink_interface='wlan0')
    calls = _forbid_mutations(monkeypatch)
    result = lifecycle._start_hotspot_impl(correlation_id='automatic-missing-evidence')
    assert result.code == 'start_failed'
    assert state['last_error'] == 'automatic_vr_radio_unavailable'
    assert calls == []


@pytest.mark.parametrize('band,width', [('5ghz', 160), ('6ghz', 160)])
def test_automatic_start_uses_wide_engine_and_never_falls_to_2ghz(monkeypatch, mock_missing_system_commands, band, width):
    from tests.test_passphrase_autoprovision import _common_start_mocks
    from vr_hotspotd import lifecycle
    cfg = {'radio_auto': True, 'ssid': 'VR Test', 'wpa2_passphrase': 'test-password', 'ap_adapter': 'wlan1', 'band_preference': '5ghz'}
    _common_start_mocks(monkeypatch, cfg)
    plan = {'band': band, 'width_mhz': width, 'channel': 5 if band == '6ghz' else 36, 'security': 'wpa3_sae' if band == '6ghz' else 'wpa2'}
    monkeypatch.setattr(lifecycle, 'get_adapters', lambda **kw: {'recommended': 'wlan1', 'adapters': [{'ifname': 'wlan1', 'phy': 'phy1', 'bus': 'usb', 'supports_ap': True, 'supports_5ghz': True, 'supports_6ghz': True, 'supports_wifi6': True, 'automatic_radio': plan}]})
    monkeypatch.setattr(lifecycle, '_maybe_set_regdom', lambda *a: None)
    monkeypatch.setattr(lifecycle, '_safe_revert_tuning', lambda *a: [])
    monkeypatch.setattr(lifecycle, 'select_best_channel', lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, '_collect_ap_logs', lambda *a: [])
    monkeypatch.setattr(lifecycle, '_iw_dev_dump', lambda: '')
    monkeypatch.setattr(lifecycle, '_kill_runtime_processes', lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, '_remove_conf_dirs', lambda *a: None)
    calls = []
    def build(**kwargs):
        calls.append(kwargs)
        return ['test-engine']
    monkeypatch.setattr(lifecycle, 'build_cmd_nat', build)
    monkeypatch.setattr(lifecycle, 'build_cmd_6ghz', build)
    monkeypatch.setattr(lifecycle, 'start_engine', lambda *a, **k: SimpleNamespace(ok=False, error='test_engine_rejected', pid=None, cmd=[], started_ts=None, exit_code=1, stdout_tail=[], stderr_tail=[]))
    result = lifecycle._start_hotspot_impl(correlation_id='automatic-width', basic_mode=True)
    assert result.code == 'start_failed'
    assert len(calls) == 1
    assert calls[0]['channel_width'] == '160'
    assert calls[0]['channel'] == plan['channel']
    assert result.state['last_error'] == 'test_engine_rejected'
