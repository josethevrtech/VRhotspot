#!/usr/bin/env python3
"""Read-only Valve 28de:2432 USB/driver diagnosis; no Steam or root required."""
from __future__ import annotations

import json
from pathlib import Path
import re

USB_ID = ('28de', '2432')
DRIVER = 'rtw89_8852cu'


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
        'steam_required_for_linux_network_interface': False,
        'next_step': (
            'No matching USB device detected; check connection.' if not devices else
            'Check iw phy capabilities, actual country, firmware and legal AP channels. '
            'An interface is not proof of a functioning hotspot. '
            'If driver_not_ready, install your distro kernel/firmware providing the exact USB ID. '
            'Do not force another device ID or bypass regulatory restrictions.'
        ),
        'limitations': [
            'Does not read serial numbers, MAC addresses, SSIDs or credentials.',
            'Does not start networking or modify kernel, firmware or regulatory settings.',
            'Does not validate headset pairing, DHCP or streaming performance.',
            'The headset-hosted direct link and a PC-hosted hotspot are different roles.',
        ],
    }


if __name__ == '__main__':
    print(json.dumps(inspect(), indent=2))
