import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from vr_hotspotd.diagnostics.support_bundle import build_support_bundle_archive


def _read_archive(data):
    with ZipFile(BytesIO(data)) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
        }


def test_archive_contains_manifest_readme_and_sanitized_files():
    manifest = {
        "bundle_schema_version": 1,
        "files": [{"path": "wireless/iw-dev.txt", "status": "ok"}],
        "warnings": [],
    }

    archive_bytes = build_support_bundle_archive(
        manifest,
        {
            "wireless/iw-dev.txt": "Interface wlan0\nssid=<redacted-secret>\n",
            "service/journal.txt": b"token=<redacted-secret>\n",
        },
        readme="README for this test bundle\n",
    )

    members = _read_archive(archive_bytes)

    assert set(members) == {
        "manifest.json",
        "README.txt",
        "service/journal.txt",
        "wireless/iw-dev.txt",
    }
    assert members["README.txt"] == b"README for this test bundle\n"
    assert members["wireless/iw-dev.txt"] == b"Interface wlan0\nssid=<redacted-secret>\n"
    assert members["service/journal.txt"] == b"token=<redacted-secret>\n"


def test_archive_manifest_is_valid_json():
    archive_bytes = build_support_bundle_archive(
        {"bundle_schema_version": 1, "warnings": []},
        {},
    )

    members = _read_archive(archive_bytes)

    assert json.loads(members["manifest.json"]) == {
        "bundle_schema_version": 1,
        "warnings": [],
    }


def test_archive_rejects_unsafe_absolute_paths():
    with pytest.raises(ValueError, match="absolute"):
        build_support_bundle_archive(
            {"bundle_schema_version": 1},
            {"/etc/passwd": "sanitized"},
        )


def test_archive_rejects_path_traversal():
    with pytest.raises(ValueError, match="traversal"):
        build_support_bundle_archive(
            {"bundle_schema_version": 1},
            {"../secret": "sanitized"},
        )


def test_archive_generation_does_not_leak_raw_secret_when_input_is_sanitized():
    archive_bytes = build_support_bundle_archive(
        {
            "bundle_schema_version": 1,
            "files": [{"path": "vr-hotspot/config.redacted.json"}],
        },
        {
            "vr-hotspot/config.redacted.json": (
                '{"ssid": "VRHotspot", "passphrase": "<redacted-secret>"}\n'
            ),
        },
        readme="No raw credentials included.\n",
    )

    assert b"super-secret-passphrase" not in archive_bytes
    members = _read_archive(archive_bytes)
    assert b"super-secret-passphrase" not in b"".join(members.values())
    assert b"<redacted-secret>" in members["vr-hotspot/config.redacted.json"]
