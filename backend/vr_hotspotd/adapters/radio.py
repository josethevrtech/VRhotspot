"""Automatic radio choices derived from current driver and regulatory evidence."""
import re
from vr_hotspotd import host_probes
from vr_hotspotd.engine.channel_scan import _eligible_candidates


def non_dfs_ap_options(text):
    modes = host_probes.parse_all_supported_interface_modes(text) or []
    if 'AP' not in modes:
        return ()
    result = []
    for section in re.split(r"(?m)^\s*Band \d+:", text)[1:]:
        frequencies = host_probes.parse_iw_frequencies(section)
        bands = {f['band'] for f in frequencies}
        if len(bands) != 1 or 'unknown' in bands:
            continue
        band = next(iter(bands))
        he_blocks = re.split(r"(?m)^\s*HE Iftypes:\s*", section)[1:]
        ap_he = '\n'.join(block for block in he_blocks
                          if 'AP' in [m.strip() for m in block.split('\n', 1)[0].split(',')])
        if band == '6ghz' and not ap_he:
            continue
        # VHT is shared capability; HE widths must come from AP, never STA-only.
        vht = section.split('HE Iftypes:', 1)[0]
        widths = [20]
        if 'HT20/HT40' in vht or re.search(r'HE40(?:/|\b)', ap_he):
            widths.append(40)
        if band != '2.4ghz':
            if 'VHT Capabilities' in vht or 'HE40/HE80' in ap_he:
                widths.append(80)
            if re.search(r'(?m)^\s*HE160/5GHz\s*$', ap_he) or re.search(r'Supported Channel Width:.*(?<![0-9])160 MHz', vht):
                widths.append(160)
        for width in widths:
            for candidate in _eligible_candidates(section, band, width):
                result.append((band, width, candidate['channel']))
    return tuple(result)


def recommend(options):
    # VR requires at least 80 MHz. Never call a 20/40 MHz fallback optimal.
    options = [v for v in options if v[0] in ('5ghz', '6ghz') and v[1] >= 80]
    if not options:
        return None
    band, width, channel = max(options, key=lambda v: (v[1], {'5ghz': 1, '6ghz': 2}[v[0]], -v[2]))
    return {'band': band, 'width_mhz': width, 'channel': channel,
            'security': 'wpa3_sae' if band == '6ghz' else 'wpa2'}


def apply_automatic(cfg, adapter):
    """Resolve again at start. Saved manual settings and credentials stay intact."""
    if not cfg.get('radio_auto', False):
        return cfg
    plan = adapter.get('automatic_radio')
    if not plan or plan['band'] not in ('5ghz', '6ghz') or plan['width_mhz'] < 80:
        raise RuntimeError('automatic_vr_radio_unavailable')
    return {**cfg, 'band_preference': plan['band'], 'channel_width': str(plan['width_mhz']),
            'ap_security': plan['security'], 'channel_auto_select': True,
            'channel_5g': None, 'channel_6g': None, 'wifi6': 'auto', 'tx_power': None,
            'allow_fallback_40mhz': False,
            'wifi_power_save_disable': True,
            'usb_autosuspend_disable': adapter.get('bus') == 'usb',
            '_automatic_channel_fallback': plan['channel']}


def verify_automatic_link(cfg, ap_info, warnings):
    """Unknown or narrow actual widths must never be reported as VR-ready."""
    if not cfg.get('radio_auto') or ap_info is None:
        return ap_info
    freq = ap_info.freq_mhz or 0
    if 5000 <= freq < 7125 and (ap_info.channel_width_mhz or 0) >= 80:
        return ap_info
    warnings.append('automatic_vr_minimum_not_met')
    return None
