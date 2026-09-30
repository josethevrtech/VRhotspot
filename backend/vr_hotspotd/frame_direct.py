"""Explicit, experimental Frame-hosted 6 GHz link. No SSH or Steam dependency.

Mutations run under lifecycle._OP_LOCK. NetworkManager owns association/DHCP;
only our fixed UUID may be activated or deactivated. Secrets never enter argv,
status, or generic hotspot configuration/support exports.
"""
import configparser
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

PROFILE = Path('/etc/NetworkManager/system-connections/vr-hotspot-frame-direct.nmconnection')
JOURNAL = Path('/var/lib/vr-hotspot/frame-direct-session.json')
UUID = 'c463edeb-f2ee-48f3-bf4c-99fc7600341f'
SYS = Path('/sys/class/net')


class DirectError(Exception):
    """Only constant, public error codes are exposed to clients."""


def run(*argv, optional=False):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError):
        if optional:
            return ''
        raise DirectError('direct_command_unavailable') from None
    if result.returncode and not optional:
        # NM errors may contain an SSID or credential. Never relay them.
        raise DirectError('direct_network_command_failed')
    return result.stdout if result.returncode == 0 else ''


def atomic_private(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.frame-direct-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def adapters():
    found = []
    for net in sorted(SYS.glob('*')):
        for parent in (net.resolve(), *net.resolve().parents):
            try:
                if ((parent / 'idVendor').read_text().strip() == '28de'
                        and (parent / 'idProduct').read_text().strip() == '2432'):
                    found.append(net.name)
                    break
            except OSError:
                pass
    return found


def active():
    # Keep conflicting AP operations blocked even after daemon restart or an
    # interrupted connect. Explicit Disconnect is the recovery operation.
    return JOURNAL.exists()


def _journal():
    try:
        return json.loads(JOURNAL.read_text())
    except (OSError, ValueError):
        raise DirectError('direct_session_unreadable') from None


def _profile():
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read_string(PROFILE.read_text())
        if cfg['connection']['uuid'] != UUID:
            raise ValueError()
        return cfg
    except (OSError, ValueError, KeyError, configparser.Error):
        raise DirectError('direct_pairing_required') from None


def _save_profile(cfg):
    buf = io.StringIO()
    cfg.write(buf, space_around_delimiters=False)
    previous = PROFILE.read_text() if PROFILE.exists() else None
    atomic_private(PROFILE, buf.getvalue())
    try:
        run('nmcli', 'connection', 'load', str(PROFILE))
    except DirectError:
        if previous is None:
            PROFILE.unlink()
        else:
            atomic_private(PROFILE, previous)
            run('nmcli', 'connection', 'load', str(PROFILE), optional=True)
        raise


def pair(body):
    if active():
        raise DirectError('direct_disconnect_before_pairing')
    if set(body) != {'ssid', 'bssid', 'passphrase'}:
        raise DirectError('direct_invalid_pairing')
    ssid, bssid, password = (body[k] for k in ('ssid', 'bssid', 'passphrase'))
    if (not all(isinstance(x, str) for x in (ssid, bssid, password))
            or not 1 <= len(ssid.encode('utf-8')) <= 32
            or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', bssid)
            or not 8 <= len(password) <= 63
            or any(ord(c) < 32 or ord(c) > 126 for c in password)):
        raise DirectError('direct_invalid_pairing')
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        'connection': {'id': 'VR Hotspot Frame Direct', 'uuid': UUID,
                       'type': 'wifi', 'autoconnect': 'false'},
        'wifi': {'mode': 'infrastructure', 'hidden': 'true',
                 'ssid': ''.join(f'{b};' for b in ssid.encode('utf-8')),
                 'bssid': bssid.lower(), 'powersave': '2'},
        'wifi-security': {'key-mgmt': 'sae', 'pmf': '3',
                          'psk': password.replace('\\', '\\\\').replace(' ', '\\s')},
        'ipv4': {'method': 'auto', 'never-default': 'true',
                 'ignore-auto-dns': 'true', 'ignore-auto-routes': 'true'},
        'ipv6': {'method': 'disabled'},
    })
    _save_profile(cfg)
    return {'paired': True}


def _check_adapter(iface):
    if iface not in adapters():
        raise DirectError('direct_valve_adapter_required')
    for family in ('-4', '-6'):
        try:
            routes = json.loads(run('ip', '-j', family, 'route', 'show', 'default'))
        except ValueError:
            raise DirectError('direct_route_check_failed') from None
        if any(r.get('dev') == iface for r in routes):
            raise DirectError('direct_adapter_is_uplink')


def _wait_managed(iface):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = run('nmcli', '-g', 'GENERAL.STATE', 'device', 'show', iface).split()
        if state and state[0] in ('30', '100'):
            return
        time.sleep(0.25)
    raise DirectError('direct_adapter_not_ready')


def link(iface):
    info = run('iw', 'dev', iface, 'info', optional=True)
    connected = run('iw', 'dev', iface, 'link', optional=True)
    match = re.search(r'channel \d+ \((\d+) MHz\), width: (\d+) MHz', info)
    frequency, width = map(int, match.groups()) if match else (0, 0)
    addresses = run('ip', '-j', '-4', 'address', 'show', 'dev', iface, optional=True)
    try:
        has_ip = any(a.get('scope') == 'global' for d in json.loads(addresses or '[]')
                     for a in d.get('addr_info', []))
    except ValueError:
        has_ip = False
    profile = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', iface, optional=True).strip()
    return {'connected': profile == UUID and 'Connected to ' in connected and has_ip,
            'frequency_mhz': frequency, 'width_mhz': width}


def status():
    result = {'experimental': True, 'paired': PROFILE.exists(), 'active': active(),
              'adapters': adapters(), 'internet': 'existing_uplinks',
              'connected': False, 'performance_qualified': False}
    if result['active']:
        try:
            session = _journal()
            result['adapter'] = session['adapter']
            result['phase'] = session['phase']
            if session['adapter'] in result['adapters']:
                result.update(link(session['adapter']))
        except (DirectError, KeyError):
            result['phase'] = 'recovery_required'
    return result


def connect(iface, *, stop_ap, start_ap, ap_running):
    if active():
        raise DirectError('direct_already_active')
    _check_adapter(iface)
    cfg = _profile()
    # Never take over an unrelated managed connection on this radio.
    current = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', iface).strip()
    if current not in ('', '--', UUID):
        raise DirectError('direct_adapter_in_use')
    managed = run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', iface).strip()
    session = {'adapter': iface, 'phase': 'connecting', 'restore_hotspot': bool(ap_running),
               'managed_before': managed == 'yes'}
    if ap_running:
        result = stop_ap()
        if result.state.get('running'):
            raise DirectError('direct_hotspot_stop_failed')
    try:
        atomic_private(JOURNAL, json.dumps(session))
        _check_adapter(iface)
        run('nmcli', 'device', 'set', iface, 'managed', 'yes')
        _wait_managed(iface)
        cfg['connection']['interface-name'] = iface
        _save_profile(cfg)
        run('nmcli', '--wait', '30', 'connection', 'up', 'uuid', UUID, 'ifname', iface)
        observed = link(iface)
        if not observed['connected'] or not 5925 <= observed['frequency_mhz'] <= 7125:
            raise DirectError('direct_6ghz_link_not_ready')
        if observed['width_mhz'] != 160:
            raise DirectError('direct_160mhz_not_negotiated')
        session['phase'] = 'connected'
        atomic_private(JOURNAL, json.dumps(session))
        return status()
    except (DirectError, OSError):
        if active():
            disconnect(start_ap=start_ap, restore_hotspot=bool(ap_running))
        elif ap_running:
            if not start_ap().state.get('running'):
                raise DirectError('direct_disconnected_hotspot_restore_failed') from None
        raise DirectError('direct_connect_failed_rolled_back') from None


def disconnect(*, start_ap, restore_hotspot=False):
    if not active():
        return status()
    session = _journal()
    # Do not disconnect arbitrary devices/profile names. UUID is ours alone.
    connected = run('nmcli', '-g', 'UUID', 'connection', 'show', '--active')
    if UUID in connected.splitlines():
        run('nmcli', '--wait', '10', 'connection', 'down', 'uuid', UUID)
    iface = session['adapter']
    if iface in adapters() and not session['managed_before']:
        run('nmcli', 'device', 'set', iface, 'managed', 'no')
    JOURNAL.unlink()
    if restore_hotspot:
        result = start_ap()
        if not result.state.get('running'):
            raise DirectError('direct_disconnected_hotspot_restore_failed')
    return status()
