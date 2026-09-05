"""Bounded, passive radio evidence. Never scan, transmit, or change host state."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any, Dict, Optional

from vr_hotspotd import __version__
from vr_hotspotd import host_probes
from vr_hotspotd.config import load_config_snapshot
from vr_hotspotd.state import load_state
from vr_hotspotd.diagnostics.clients import parse_iw_station_dump
from vr_hotspotd.diagnostics.streaming import MAX_SAMPLE_BYTES
from vr_hotspotd.diagnostics.support_bundle import redact_support_bundle_data, redact_known_secrets

_NET_ROOT = Path('/sys/class/net')
_STATION_SALT = secrets.token_bytes(32)
_IFNAME = re.compile(r'[A-Za-z0-9_.:-]{1,15}')
_STATION_FIELDS = ('tx_packets', 'tx_failed', 'tx_retries', 'rx_packets', 'tx_bytes',
                   'rx_bytes', 'inactive_ms', 'connected_time_s', 'signal_dbm',
                   'signal_avg_dbm', 'tx_bitrate_mbps', 'rx_bitrate_mbps')
_EVENTS = {
    'usb_reset': r'usb .*reset .*USB device',
    'usb_disconnect': r'usb .*USB disconnect',
    'firmware_failure': r'(?:firmware|mt7\w*|iwlwifi|ath\w*).*(?:crash|timeout|timed out|failed|reset)',
    'gpu_failure': r'(?:amdgpu|i915|NVRM).*(?:reset|timeout|timed out|hang|Xid)',
}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _command(argv: list[str], deadline: float) -> Dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0.02:
        return {'status': 'budget_exhausted'}
    result = host_probes.run_command(argv, timeout_s=min(0.4, remaining),
                                     env={**os.environ, 'LC_ALL': 'C'})
    if result.missing:
        return {'status': 'missing_command'}
    if result.timed_out:
        return {'status': 'timeout'}
    if result.permission_denied:
        return {'status': 'permission_denied'}
    if result.returncode != 0:
        return {'status': 'failed', 'exit_code': result.returncode}
    if len(result.stdout or '') > 65536:
        return {'status': 'output_limit'}
    return {'status': 'ok', 'text': result.stdout or ''}


def _read(path: Path) -> Optional[str]:
    try:
        with path.open(encoding='utf-8') as source:
            return source.read(256).strip()
    except (OSError, UnicodeError):
        return None


def _adapter_facts(ifname: str) -> Dict[str, Any]:
    """USB identity without serial numbers, MACs, hostnames, or home paths."""
    facts: Dict[str, Any] = {}
    try:
        device = (_NET_ROOT / ifname / 'device').resolve(strict=True)
        driver = device / 'driver'
        if driver.exists():
            facts['driver'] = driver.resolve().name
        module = driver / 'module'
        if module.exists():
            facts['module'] = module.resolve().name
            facts['module_version'] = _read(module / 'version')
        for parent in [device, *list(device.parents)[:7]]:
            vendor, product = _read(parent / 'idVendor'), _read(parent / 'idProduct')
            if vendor and product and re.fullmatch(r'[0-9a-fA-F]{4}', vendor + '') and re.fullmatch(r'[0-9a-fA-F]{4}', product):
                facts['usb'] = {
                    'vendor_id': vendor.lower(), 'product_id': product.lower(),
                    'revision': _read(parent / 'bcdDevice'),
                    'speed_mbps': _number(_read(parent / 'speed')),
                    'power_control': _read(parent / 'power/control'),
                }
                break
    except OSError:
        facts['status'] = 'unavailable'
    return facts


def _radio(text: str, ifname: str, ssid: Any) -> Optional[Dict[str, Any]]:
    # Reuse lifecycle's established AP parser, but never its relaxed selector.
    from vr_hotspotd.lifecycle import _parse_iw_dev_ap_info
    block = []
    selected = False
    for line in text.splitlines():
        if line.strip().startswith('Interface '):
            if selected:
                break
            selected = line.strip() == f'Interface {ifname}'
        if selected:
            block.append(line)
    selected_text = '\n'.join(block)
    candidates = [ap for ap in _parse_iw_dev_ap_info(selected_text)
                  if ap.ifname == ifname and ap.ssid == ssid and ap.freq_mhz is not None]
    if len(candidates) != 1:
        return None
    ap = candidates[0]
    out: Dict[str, Any] = {'channel': ap.channel, 'frequency_mhz': ap.freq_mhz,
                           'width_mhz': ap.channel_width_mhz}
    for line in selected_text.splitlines():
        line = line.strip()
        match = re.match(r'txpower ([\d.+-]+)', line)
        if match:
            out['reported_tx_power_dbm'] = _number(match[1])
    return out if 'channel' in out else None


def _stations(text: str) -> list[Dict[str, Any]]:
    stations: list[Dict[str, Any]] = []
    for client in parse_iw_station_dump(text)[:8]:
        label = hmac.new(_STATION_SALT, client.mac.encode(), hashlib.sha256).hexdigest()[:16]
        station = {'station_id': label}
        for key in _STATION_FIELDS:
            value = getattr(client, key)
            if _number(value) is not None:
                station[key] = value
        stations.append(station)
    return stations


def _kernel_events(text: str) -> list[Dict[str, Any]]:
    events = []
    for line in text.splitlines()[:32]:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        message = record.get('MESSAGE') if isinstance(record, dict) else None
        if not isinstance(message, str):
            continue
        timestamp = record.get('__REALTIME_TIMESTAMP')
        if not isinstance(timestamp, str) or not timestamp.isdigit():
            timestamp = None
        for code, pattern in _EVENTS.items():
            if re.search(pattern, message, re.IGNORECASE):
                events.append({'kind': code, 'unix_time_us': timestamp,
                               'scope': 'host_event_not_proven_adapter_specific'})
    return events[:16]


def collect_streaming_snapshot() -> Dict[str, Any]:
    deadline = time.monotonic() + 1.8
    # The normal loader can persist migrations. Passive evidence must use the
    # existing read-only view and leave saved configuration untouched.
    state, cfg = load_state(), load_config_snapshot()
    if not isinstance(state, dict) or not isinstance(cfg, dict):
        return {'status': 'state_unavailable'}
    out: Dict[str, Any] = {
        'status': 'ok', 'app_version': __version__, 'kernel': os.uname().release,
        'hotspot': {key: state.get(key) for key in ('running', 'phase', 'band', 'adapter', 'ap_interface')},
        'measurement': 'passive_driver_counters_not_packet_capture',
        'network_rtt_ms': None,
        'limitations': ['no_receiver_packet_loss', 'no_render_encode_decode_timings',
                        'two_second_samples_do_not_measure_short_latency_spikes'],
    }
    tuning = state.get('tuning')
    power = tuning.get('tx_power') if isinstance(tuning, dict) else None
    if isinstance(power, dict):
        out['power'] = {key: power.get(key) for key in ('mode', 'requested_dbm', 'effective_dbm', 'status')}
    adapter, ap = state.get('adapter'), state.get('ap_interface')
    if isinstance(adapter, str) and _IFNAME.fullmatch(adapter):
        out['adapter'] = _adapter_facts(adapter)
    else:
        out['status'] = 'adapter_unavailable'
    if state.get('running') and isinstance(ap, str) and _IFNAME.fullmatch(ap):
        verified = False
        try:
            verified = bool(adapter and (_NET_ROOT / adapter / 'phy80211').resolve(strict=True)
                            == (_NET_ROOT / ap / 'phy80211').resolve(strict=True))
        except (OSError, RuntimeError):
            pass
        info = _command(['iw', 'dev', ap, 'info'], deadline) if verified else {'status': 'identity_unverified'}
        radio = _radio(info.get('text', ''), ap, cfg.get('ssid')) if info['status'] == 'ok' else None
        out['radio_status'] = info['status'] if radio else 'identity_or_radio_unavailable'
        if radio:
            out['radio'] = radio
            station_result = _command(['iw', 'dev', ap, 'station', 'dump'], deadline)
            out['stations_status'] = station_result['status']
            out['stations'] = _stations(station_result.get('text', ''))
            queue = _command(['tc', '-j', '-s', 'qdisc', 'show', 'dev', ap], deadline)
            out['queue_status'] = queue['status']
            try:
                entries = json.loads(queue.get('text', '[]'))
                out['queues'] = [{key: entry.get(key) for key in ('kind', 'bytes', 'packets', 'drops', 'overlimits', 'qlen', 'backlog')}
                                 for entry in entries[:8] if isinstance(entry, dict)] if isinstance(entries, list) else []
            except (ValueError, TypeError):
                out['queue_status'] = 'invalid_output'
    else:
        out['radio_status'] = 'hotspot_not_running'
    journal = _command(['journalctl', '-k', '--since=-3s', '-n', '32', '--no-pager', '-o', 'json'], deadline)
    out['kernel_events_status'] = journal['status']
    out['kernel_events'] = _kernel_events(journal.get('text', ''))
    # No raw config, command output, SSID, station addresses, or kernel message
    # enters the report. Also scrub known secrets even inside innocuous fields.
    known_secrets = [value for value in (cfg.get('wpa2_passphrase'), os.environ.get('VR_HOTSPOTD_API_TOKEN'))
                     if isinstance(value, str) and value]

    return _fit_sample(redact_known_secrets(redact_support_bundle_data(out), known_secrets))


def _fit_sample(data: Dict[str, Any]) -> Dict[str, Any]:
    """Retain core radio evidence on busy samples; explicitly count omissions."""
    omitted = {}
    for field in ('kernel_events', 'queues', 'stations'):
        values = data.get(field)
        while isinstance(values, list) and len(values) > (1 if field == 'stations' else 0):
            if len(json.dumps(data, allow_nan=False).encode('utf-8')) <= MAX_SAMPLE_BYTES:
                return data
            values.pop()
            omitted[field] = omitted.get(field, 0) + 1
            data['omitted_for_size'] = omitted
    return data


def summarize_streaming_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Derived evidence, not a VR pass/fail grade or receiver-loss estimate."""
    interruptions, changes, unavailable = 0, 0, 0
    previous_running, previous_channel, previous_identity = None, None, None
    station_previous: Dict[str, Any] = {}
    failure_ratios = []
    events = set()
    def mapping(value):
        return value if isinstance(value, dict) else {}

    def items(value):
        return value if isinstance(value, list) else []

    for sample in items(report.get('samples')):
        data = mapping(mapping(sample).get('data'))
        hotspot = mapping(data.get('hotspot'))
        running = hotspot.get('running')
        if previous_running is True and running is False:
            interruptions += 1
        if isinstance(running, bool):
            previous_running = running
        radio = mapping(data.get('radio'))
        channel = (hotspot.get('ap_interface'), radio.get('frequency_mhz'), radio.get('width_mhz'))
        identity = (hotspot.get('adapter'), *channel) if running is True and channel[1] is not None else None
        if identity is None or identity != previous_identity:
            station_previous = {}
        previous_identity = identity
        if channel[1] is not None:
            if previous_channel and channel != previous_channel:
                changes += 1
            previous_channel = channel
        else:
            unavailable += 1
        current = {}
        for station in items(data.get('stations')):
            station = mapping(station)
            sid = station.get('station_id')
            if not isinstance(sid, str):
                continue
            before = station_previous.get(sid, {})
            connected, prev_connected = _number(station.get('connected_time_s')), _number(before.get('connected_time_s'))
            if connected is not None and prev_connected is not None and connected < prev_connected:
                before = {}
            tx, failed = _number(station.get('tx_packets')), _number(station.get('tx_failed'))
            prev_tx, prev_failed = _number(before.get('tx_packets')), _number(before.get('tx_failed'))
            if all(n is not None for n in (tx, failed, prev_tx, prev_failed)):
                delta_tx, delta_failed = tx - prev_tx, failed - prev_failed
                if delta_tx >= 0 and delta_failed >= 0 and delta_tx + delta_failed > 0:
                    failure_ratios.append(100 * delta_failed / (delta_tx + delta_failed))
            current[sid] = station
        station_previous = current if identity is not None else {}
        for event in items(data.get('kernel_events')):
            event = mapping(event)
            kind, timestamp = event.get('kind'), event.get('unix_time_us')
            if isinstance(kind, str) and isinstance(timestamp, (str, type(None))):
                events.add((kind, timestamp))
    return {
        'observed_hotspot_interruptions': interruptions,
        'observed_radio_changes': changes,
        'samples_without_verified_radio': unavailable,
        'max_driver_tx_failure_ratio_pct': max(failure_ratios) if failure_ratios else None,
        'kernel_event_count': len(events), 'network_rtt_p99_ms': None,
        'receiver_packet_loss_pct': None, 'vr_qualified': False,
        'note': 'Evidence only. Missing events do not prove a stable stream; correlate freeze markers with streamer timings.',
    }
