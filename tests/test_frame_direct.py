import json
from types import SimpleNamespace

import pytest

from vr_hotspotd import frame_direct as direct, lifecycle
from tests.test_api_streaming import _request


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(direct, 'PROFILE', tmp_path / 'profile.nmconnection')
    monkeypatch.setattr(direct, 'JOURNAL', tmp_path / 'session.json')
    monkeypatch.setattr(direct, 'adapters', lambda: ['usbframe'])
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == 'ip':
            return '[]'
        if 'GENERAL.STATE' in args:
            return '30 (disconnected)'
        if 'GENERAL.NM-MANAGED' in args:
            return 'yes'
        if args == ('nmcli', '-g', 'UUID', 'connection', 'show', '--active'):
            return direct.UUID
        return ''
    monkeypatch.setattr(direct, 'run', run)
    monkeypatch.setattr(direct, 'link', lambda _: {'connected': True, 'frequency_mhz': 6135, 'width_mhz': 160})
    monkeypatch.setenv('VR_HOTSPOTD_API_TOKEN', 'test-streaming-token')
    return calls


def pair():
    return direct.pair({'ssid': 'frame_test', 'bssid': '02:11:22:33:44:55',
                        'passphrase': 'private pairing secret'})


def connect(running=False, **kwargs):
    return direct.connect('usbframe', ap_running=running,
                          stop_ap=kwargs.get('stop_ap', lambda: SimpleNamespace(state={'running': False})),
                          start_ap=kwargs.get('start_ap', lambda: SimpleNamespace(state={'running': True})))


def test_pairing_is_private_and_never_in_command_arguments_or_status(env):
    pair()
    assert direct.PROFILE.stat().st_mode & 0o777 == 0o600
    cfg = direct._profile()
    assert cfg['wifi-security']['key-mgmt'] == 'sae'
    assert cfg['wifi-security']['pmf'] == '3'
    assert cfg['ipv4']['never-default'] == 'true'
    assert cfg['ipv4']['ignore-auto-dns'] == 'true'
    assert cfg['connection']['autoconnect'] == 'false'
    assert 'private pairing secret' not in repr(env) + json.dumps(direct.status())


@pytest.mark.parametrize('field,value', [('passphrase', 'x\n[ipv4]\nmethod=shared'),
    ('ssid', 'x' * 33), ('bssid', 'bad'), ('passphrase', 123), ('passphrase', 'short')])
def test_pairing_rejects_invalid_data_without_writes(env, field, value):
    body = {'ssid': 'frame', 'bssid': '02:11:22:33:44:55', 'passphrase': 'private password'}
    body[field] = value
    with pytest.raises(direct.DirectError):
        direct.pair(body)
    assert env == [] and not direct.PROFILE.exists()


def test_non_dongle_and_default_uplink_cannot_be_taken_over(env, monkeypatch):
    pair()
    with pytest.raises(direct.DirectError, match='valve_adapter'):
        direct.connect('laptopwifi', stop_ap=lambda: pytest.fail('stopped'), start_ap=None, ap_running=True)
    monkeypatch.setattr(direct, 'run', lambda *a, **k: '[{"dev":"usbframe"}]')
    with pytest.raises(direct.DirectError, match='uplink'):
        connect()
    assert not direct.active()


def test_connect_disconnect_and_restart_guard(env, monkeypatch):
    pair()
    assert connect()['connected']
    monkeypatch.setattr(lifecycle, 'load_state', lambda: {})
    assert lifecycle._start_hotspot_impl().code == 'direct_disconnect_required'
    assert lifecycle._repair_impl().code == 'direct_disconnect_required'
    assert lifecycle._stop_hotspot_impl().code == 'direct_disconnect_required'
    with pytest.raises(direct.DirectError, match='already_active'):
        connect()
    direct.disconnect(start_ap=None)
    assert not direct.active()
    assert ('nmcli', '--wait', '10', 'connection', 'down', 'uuid', direct.UUID) in env
    assert direct.PROFILE.exists()  # pairing survives disconnect


@pytest.mark.parametrize('frequency,width,connected', [(5220, 80, True), (6135, 80, True), (6135, 160, False)])
def test_failed_qualification_restores_previous_hotspot(env, monkeypatch, frequency, width, connected):
    pair()
    monkeypatch.setattr(direct, 'link', lambda _: {'connected': connected, 'frequency_mhz': frequency, 'width_mhz': width})
    restored = []
    with pytest.raises(direct.DirectError, match='rolled_back'):
        connect(True, start_ap=lambda: restored.append(True) or SimpleNamespace(state={'running': True}))
    assert restored == [True]
    assert not direct.active()


def test_failure_to_disconnect_keeps_recovery_guard(env, monkeypatch):
    pair()
    connect()
    original = direct.run
    def fail(*args, **kwargs):
        if 'down' in args:
            raise direct.DirectError('direct_network_command_failed')
        return original(*args, **kwargs)
    monkeypatch.setattr(direct, 'run', fail)
    with pytest.raises(direct.DirectError):
        direct.disconnect(start_ap=None)
    assert direct.active()


@pytest.mark.parametrize('path,method', [('/v1/frame-direct', 'GET'),
    ('/v1/frame-direct/pair', 'POST'), ('/v1/frame-direct/connect', 'POST'),
    ('/v1/frame-direct/disconnect', 'POST')])
def test_api_requires_authentication(env, path, method):
    code, _ = _request(path, method=method, token=None)
    assert code == 401 and not env


def test_api_pair_then_connect_then_disconnect(env, monkeypatch):
    monkeypatch.setattr(lifecycle, 'load_state', lambda: {'running': False})
    monkeypatch.setattr(lifecycle, 'is_running', lambda: False)
    code, result = _request('/v1/frame-direct/pair', method='POST', body={
        'ssid': 'frame_test', 'bssid': '02:11:22:33:44:55', 'passphrase': 'private secret'})
    assert code == 200 and result['data']['paired']
    code, result = _request('/v1/frame-direct/connect', method='POST', body={'adapter': 'usbframe'})
    assert code == 200 and result['data']['connected']
    code, result = _request('/v1/frame-direct/disconnect', method='POST')
    assert code == 200 and not result['data']['active']
    assert 'private secret' not in json.dumps(result)


def test_no_wrong_interface_or_query_injection(env):
    pair()
    code, _ = _request('/v1/frame-direct/connect', method='POST', body={'adapter': '--help'})
    assert code == 409 and not direct.active()
    code, _ = _request('/v1/frame-direct/connect?extra=1', method='POST', body={'adapter': 'usbframe'})
    assert code == 409 and not direct.active()


def test_rejected_profile_load_preserves_previous_pairing(env, monkeypatch):
    pair()
    previous = direct.PROFILE.read_bytes()
    def fail(*args, **kwargs):
        if not kwargs.get('optional'):
            raise direct.DirectError('direct_network_command_failed')
    monkeypatch.setattr(direct, 'run', fail)
    with pytest.raises(direct.DirectError):
        direct.pair({'ssid': 'different', 'bssid': '02:22:33:44:55:66', 'passphrase': 'different secret'})
    assert direct.PROFILE.read_bytes() == previous


def test_journal_storage_failure_after_stop_restores_ap(env, monkeypatch):
    pair()
    restored = []
    original = direct.atomic_private
    def fail(path, text):
        if path == direct.JOURNAL:
            raise OSError('disk full')
        original(path, text)
    monkeypatch.setattr(direct, 'atomic_private', fail)
    with pytest.raises(direct.DirectError, match='rolled_back'):
        connect(True, start_ap=lambda: restored.append(True) or SimpleNamespace(state={'running': True}))
    assert restored == [True]


def test_adapter_detection_follows_usb_ancestry_and_exact_ids(tmp_path, monkeypatch):
    sys = tmp_path / 'net'; sys.mkdir()
    usb = tmp_path / 'usb'; usb.mkdir()
    (usb / 'idVendor').write_text('28de\n')
    (usb / 'idProduct').write_text('2432\n')
    net = usb / 'interface/net/dongle'; net.mkdir(parents=True)
    (sys / 'dongle').symlink_to(net)
    (sys / 'unrelated').mkdir()
    monkeypatch.setattr(direct, 'SYS', sys)
    assert direct.adapters() == ['dongle']
    (usb / 'idProduct').write_text('1304\n')
    assert direct.adapters() == []


def test_networkmanager_readiness_is_awaited(env, monkeypatch):
    states = iter(['20 (unavailable)', '20 (unavailable)', '30 (disconnected)'])
    waits = []
    monkeypatch.setattr(direct, 'run', lambda *a: next(states))
    monkeypatch.setattr(direct.time, 'sleep', waits.append)
    direct._wait_managed('usbframe')
    assert waits == [0.25, 0.25]


def test_native_ui_bridge_accepts_exact_direct_routes_with_bounded_transition_timeout():
    from flatpak_client import LocalApiClient
    from tests.test_flatpak_streaming import Transport
    transport = Transport()
    client = LocalApiClient(token='private-token', transport=transport)
    for path, method, body in [('/v1/frame-direct', 'GET', None),
        ('/v1/frame-direct/pair', 'POST', {'ssid': 'test'}),
        ('/v1/frame-direct/connect', 'POST', {'adapter': 'usbframe'}),
        ('/v1/frame-direct/disconnect', 'POST', {})]:
        client.portal_request(path, method=method, body=body)
        request = transport.requests[-1]
        assert request.timeout == (120 if path.endswith(('connect', 'disconnect')) else 10)
        assert 'private-token' not in repr(request)
