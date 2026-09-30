from types import SimpleNamespace

import pytest

from flatpak_app.frame_control import FrameControlBridge, FrameControlState
from flatpak_app.tray import build_tray_menu_model
from flatpak_client import TrayState


def bridge(responses):
    calls = []
    def call(*args):
        calls.append(args)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(unpack=lambda: response)
    gio = SimpleNamespace(
        bus_get_sync=lambda *_: SimpleNamespace(call_sync=call),
        BusType=SimpleNamespace(SESSION=1),
        DBusCallFlags=SimpleNamespace(NO_AUTO_START=2),
    )
    glib = SimpleNamespace(VariantType=SimpleNamespace(new=lambda s: s))
    return FrameControlBridge(Gio=gio, GLib=glib), calls


@pytest.mark.parametrize('status', ['idle', 'connecting', 'controlling-frame'])
def test_status_uses_fixed_bounded_session_call(status):
    client, calls = bridge([(status,)])
    assert client.refresh().status == status
    assert calls == [('org.mainframeos.FrameControl', '/Control',
                      'org.mainframeos.FrameControl', 'Status', None,
                      '(s)', 2, 500, None)]


@pytest.mark.parametrize('response', [(), ('unknown',), ({'secret': 'value'},),
                                     RuntimeError('private error'), ('idle', 'extra')])
def test_missing_or_invalid_service_fails_closed(response):
    client, calls = bridge([response])
    assert not client.refresh().available
    assert client.state.label == 'Frame Control Service Unavailable'
    assert len(calls) == 1


def test_toggle_checks_availability_and_refreshes_without_other_methods():
    client, calls = bridge([('idle',), (), ('connecting',)])
    assert client.toggle()
    assert client.state.status == 'connecting'
    assert [call[3] for call in calls] == ['Status', 'Toggle', 'Status']


def test_toggle_does_not_start_missing_service():
    client, calls = bridge([RuntimeError('service missing')])
    assert not client.toggle()
    assert [call[3] for call in calls] == ['Status']


def test_toggle_failure_clears_stale_availability():
    client, _ = bridge([('controlling-frame',), RuntimeError('connection gone')])
    assert not client.toggle()
    assert not client.state.available


@pytest.mark.parametrize('status,label,enabled', [
    ('idle', 'Control Steam Frame', True),
    ('connecting', 'Cancel Frame Connection', True),
    ('controlling-frame', 'Return Control to Laptop', True),
    ('unavailable', 'Frame Control Service Unavailable', False),
])
def test_frame_control_menu_independent_of_hotspot_auth(status, label, enabled):
    # Existing home-network operation must work even without a hotspot daemon.
    menu = build_tray_menu_model(TrayState(), window_visible=False,
                                frame_control=FrameControlState(status))
    item = next(i for i in menu.all_items() if i.action == 'frame_control')
    assert item.label == label and item.enabled is enabled
    ids = [i.item_id for i in menu.all_items()]
    assert len(ids) == len(set(ids))
