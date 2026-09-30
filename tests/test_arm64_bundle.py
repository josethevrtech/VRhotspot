"""Native bundle integrity and separation from the legacy vendor payload."""
import importlib.util
from pathlib import Path

import pytest

from vr_hotspotd import vendor_paths

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('arm64_build', ROOT / 'tools/arm64/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_source_tampering_rejected(tmp_path):
    archive = tmp_path / 'source.tar.gz'
    archive.write_bytes(b'original')
    expected = builder.digest(archive)
    builder.verify_archive(archive, expected)
    archive.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        builder.verify_archive(archive, expected)


@pytest.mark.parametrize('machine,elf_class,endianness', [(62, 2, 1), (183, 1, 1), (183, 2, 2)])
def test_wrong_elf_rejected(tmp_path, machine, elf_class, endianness):
    binary = tmp_path / 'binary'
    data = bytearray(20)
    data[:6] = b'\x7fELF' + bytes([elf_class, endianness])
    data[18:20] = machine.to_bytes(2, 'little')
    binary.write_bytes(data)
    with pytest.raises(ValueError, match='ARM64'):
        builder.verify_arm64(binary)


def test_native_bundle_does_not_mix_legacy_libraries(monkeypatch, tmp_path):
    bundle = tmp_path / 'native'
    (bundle / 'bin').mkdir(parents=True)
    for name in ('hostapd', 'dnsmasq', 'lnxrouter'):
        path = bundle / 'bin' / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    monkeypatch.setenv('VR_HOTSPOT_VENDOR_ROOT', str(bundle))
    monkeypatch.setenv('VR_HOTSPOT_VENDOR_STRICT', '1')
    resolved, lib, _, missing = vendor_paths.resolve_vendor_required(['hostapd', 'dnsmasq'])
    assert not missing
    assert all(Path(p).parent == bundle / 'bin' for p in resolved.values())
    assert lib is None
    assert all(str(p).startswith(str(bundle)) for p in vendor_paths.vendor_lib_dirs())
    assert vendor_paths.resolve_vendor_exe('lnxrouter')[0] == str(bundle / 'bin/lnxrouter')


@pytest.mark.parametrize('value', ['relative/path', '/nonexistent/frame-test-vendor'])
def test_invalid_override_does_not_fall_back_to_x86(monkeypatch, value):
    monkeypatch.setenv('VR_HOTSPOT_VENDOR_ROOT', value)
    with pytest.raises(ValueError, match='absolute bundle'):
        vendor_paths.vendor_bin_dirs()
