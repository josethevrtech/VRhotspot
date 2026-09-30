#!/usr/bin/env python3
"""Read-only Valve 28de:2432 USB/driver diagnosis; no Steam or root required."""
from __future__ import annotations

import json
import argparse
from pathlib import Path
import re
import subprocess

USB_ID = ('28de', '2432')
DRIVER = 'rtw89_8852cu'
USB_ACPI_FIX = 'bf4a37f516f0382832c10a9d04414944d0d96591'


def parse_link(info: str, link: str) -> dict:
    """Return only radio facts; never return raw iw output or peer identifiers."""
    result = {'role': 'unknown', 'associated': False}
    role = re.search(r'^\s*type (\S+)\s*$', info, re.M)
    if role and role[1] in {'managed', 'AP', 'monitor', 'P2P-client', 'P2P-GO'}:
        result['role'] = role[1]
    result['associated'] = bool(re.search(r'^Connected to ', link, re.M))
    freq = re.search(r'^\s*freq:\s*([0-9.]+)', link, re.M)
    if not freq:
        freq = re.search(r'channel \d+ \(([0-9.]+) MHz\)', info)
    if freq:
        result['frequency_mhz'] = float(freq[1])
    width = re.search(r'width:\s*(\d+) MHz', info)
    if width:
        result['channel_width_mhz'] = int(width[1])
    for direction in ('tx', 'rx'):
        rate = re.search(r'^\s*' + direction + r' bitrate:\s*([0-9.]+) MBit/s', link, re.M)
        if rate:
            result[direction + '_phy_mbps'] = float(rate[1])
    result['performance_qualified'] = False
    return result


def inspect_link(ifname: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', ifname) or ifname.startswith('-'):
        return {'error': 'invalid_interface_name'}
    outputs = []
    for operation in ('info', 'link'):
        try:
            proc = subprocess.run(['iw', 'dev', ifname, operation], capture_output=True,
                                  text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {'error': 'iw_unavailable_or_timed_out'}
        if proc.returncode:
            return {'error': 'iw_failed'}
        outputs.append(proc.stdout)
    return parse_link(*outputs)


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return ''


def inspect(sysfs: Path = Path('/sys')) -> dict:
    devices = []
    for device in sorted((sysfs / 'bus/usb/devices').glob('*')):
        if (read(device / 'idVendor').lower(), read(device / 'idProduct').lower()) != USB_ID:
            continue
        interfaces = []
        drivers = set()
        for interface in (sysfs / 'bus/usb/devices').glob(device.name + ':*'):
            link = interface / 'driver'
            if link.is_symlink():
                drivers.add(link.resolve().name)
            interfaces.extend(p.name for p in (interface / 'net').glob('*')
                              if re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', p.name))
        devices.append({
            'usb_id': ':'.join(USB_ID), 'name': 'Steam Frame Wireless Adapter',
            'usb_speed_mbps': read(device / 'speed'),
            'drivers': sorted(drivers), 'interfaces': sorted(set(interfaces)),
            'status': 'interface_available' if interfaces else 'driver_not_ready',
        })
    return {
        'devices': devices,
        'expected_driver': DRIVER,
        'six_ghz_usb_acpi_fix': {
            'upstream_commit': USB_ACPI_FIX,
            'installed_status': 'unknown',
            'note': 'Kernel version alone cannot prove this fix is present. '
                    'Older USB drivers can wrongly apply internal-card ACPI VLP policy. '
                    'An authentication/STA insertion error -22 is not unique to this cause.',
        },
        'steam_required_for_linux_network_interface': False,
        'next_step': (
            'No matching USB device detected; check connection.' if not devices else
            'Check iw phy capabilities, actual country, firmware and legal AP channels. '
            'An interface is not proof of a functioning hotspot. '
            'If driver_not_ready, install your distro kernel/firmware providing the exact USB ID. '
            'Do not force another device ID or bypass regulatory restrictions.'
        ),
        'limitations': [
            'Does not report serial numbers, MAC addresses, SSIDs or credentials.',
            'Does not start networking or modify kernel, firmware or regulatory settings.',
            'Does not validate headset pairing, DHCP or streaming performance.',
            'The headset-hosted direct link and a PC-hosted hotspot are different roles.',
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--link', action='store_true', help='include sanitized iw link facts')
    args = parser.parse_args()
    report = inspect()
    if args.link:
        for device in report['devices']:
            device['links'] = {name: inspect_link(name) for name in device['interfaces']}
    print(json.dumps(report, indent=2))
