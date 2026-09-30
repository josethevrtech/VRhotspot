#!/usr/bin/env python3
"""Build a private native ARM64 networking bundle; never install or start it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_archive(path: Path, expected: str) -> None:
    if digest(path) != expected:
        raise ValueError(f"Source checksum mismatch: {path.name}")


def verify_arm64(path: Path) -> None:
    header = path.read_bytes()[:20]
    if (len(header) != 20 or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != 183):
        raise ValueError(f"Not a little-endian ARM64 ELF: {path.name}")


def build(output: Path, cache: Path | None, jobs: int) -> None:
    if platform.machine().lower() not in {'aarch64', 'arm64'}:
        raise ValueError('Run this native build on an ARM64 Linux host')
    if os.geteuid() == 0:
        raise ValueError('Run as an ordinary user, not root')
    if jobs < 1 or jobs > 32:
        raise ValueError('jobs must be between 1 and 32')
    for command in ('cc', 'make', 'pkg-config', 'ldd'):
        if not shutil.which(command):
            raise ValueError(f'Missing existing build dependency: {command}')
    subprocess.run(['pkg-config', '--exists', 'libnl-3.0', 'libnl-genl-3.0', 'openssl'], check=True)
    output.mkdir(parents=True, exist_ok=False)
    sources = output / 'sources'
    sources.mkdir()
    pins = json.loads((HERE / 'sources.json').read_text())
    for pin in pins.values():
        name = pin['url'].rsplit('/', 1)[1]
        archive = sources / name
        if cache and (cache / name).is_file():
            shutil.copy2(cache / name, archive)
        else:
            with urllib.request.urlopen(pin['url'], timeout=60) as response, archive.open('wb') as target:
                shutil.copyfileobj(response, target)
        verify_archive(archive, pin['sha256'])
        with tarfile.open(archive) as source:
            source.extractall(sources, filter='data')
    hostapd = sources / f"hostapd-{pins['hostapd']['version']}" / 'hostapd'
    dnsmasq = sources / f"dnsmasq-{pins['dnsmasq']['version']}"
    shutil.copy2(HERE / 'hostapd.config', hostapd / '.config')
    subprocess.run(['make', f'-j{jobs}', 'hostapd', 'hostapd_cli'], cwd=hostapd, check=True)
    subprocess.run(['make', f'-j{jobs}'], cwd=dnsmasq, check=True)
    bundle = output / 'vendor'
    binaries = bundle / 'bin'
    binaries.mkdir(parents=True)
    licenses = bundle / 'licenses'
    licenses.mkdir()
    receipt = {'architecture': 'aarch64', 'sources': pins,
               'hostapd_config_sha256': digest(HERE / 'hostapd.config'),
               'binaries': {}, 'hardware_hotspot_tested': False,
               'linkage': 'dynamic; requires compatible native host libraries; not a universal ARM64 release'}
    for name, source in [('hostapd', hostapd / 'hostapd'), ('hostapd_cli', hostapd / 'hostapd_cli'),
                         ('dnsmasq', dnsmasq / 'src/dnsmasq')]:
        verify_arm64(source)
        shutil.copy2(source, binaries / name)
        probe = subprocess.run([str(binaries / name), '--version' if name == 'dnsmasq' else '-v'],
                               capture_output=True, text=True)
        # hostapd uses exit status 1 for its successful version-only command.
        version = probe.stdout + probe.stderr
        expected = pins['dnsmasq' if name == 'dnsmasq' else 'hostapd']['version']
        if probe.returncode not in (0, 1) or expected not in version:
            raise ValueError(f'Native version probe failed: {name}')
        deps = subprocess.check_output(['ldd', str(binaries / name)], text=True)
        if 'not found' in deps:
            raise ValueError(f'Missing runtime dependency: {name}')
        receipt['binaries'][name] = {'sha256': digest(binaries / name), 'version': version.strip(), 'dependencies': deps}
    router = REPO / 'backend/vendor/bin/lnxrouter'
    shutil.copy2(router, binaries / 'lnxrouter')
    receipt['binaries']['lnxrouter'] = {'sha256': digest(router), 'source': 'repository bundled shell script'}
    shutil.copy2(hostapd.parent / 'COPYING', licenses / 'hostapd-COPYING')
    for name in ('COPYING', 'COPYING-v3'):
        if (dnsmasq / name).exists():
            shutil.copy2(dnsmasq / name, licenses / f'dnsmasq-{name}')
    shutil.copy2(REPO / 'backend/vendor/licenses/linux-router.LICENSE.txt', licenses / 'linux-router.LICENSE.txt')
    (bundle / 'BUILD_RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Built and version-checked: {bundle}. Nothing installed or started.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New output directory (must not exist)')
    parser.add_argument('--source-cache', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    build(args.output.resolve(), args.source_cache, args.jobs)
