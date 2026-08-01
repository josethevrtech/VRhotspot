from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile

from vr_hotspotd.devtools.platform_tools_manager import (
    inspect_managed_platform_tools,
    install_managed_platform_tools,
    remove_managed_platform_tools,
)


class _Response:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def _archive(*, symlink_adb=False, include_notice=True) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        adb = zipfile.ZipInfo("platform-tools/adb")
        adb.create_system = 3
        adb.external_attr = ((stat.S_IFLNK if symlink_adb else stat.S_IFREG) | 0o755) << 16
        bundle.writestr(adb, b"target" if symlink_adb else b"#!/bin/sh\necho adb\n")
        if include_notice:
            notice = zipfile.ZipInfo("platform-tools/NOTICE.txt")
            notice.create_system = 3
            notice.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(notice, b"Android SDK Platform-Tools notice\n")
        bundle.writestr("platform-tools/ignored-tool", b"not allowlisted")
    return output.getvalue()


def _pin(tmp_path: Path, archive: bytes, **overrides) -> Path:
    pin = {
        "schema_version": 1,
        "version": "r37.0.0",
        "url": "https://dl.google.com/android/repository/platform-tools_r37.0.0-linux.zip",
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "implementation_blocked": False,
        "max_archive_bytes": 100000000,
        "arch": "x86_64",
        "extract_allowlist": [
            "platform-tools/NOTICE.txt",
            "platform-tools/adb",
        ],
        "license_name": "Android SDK License Agreement",
        "license_terms_url": "https://developer.android.com/studio/terms",
    }
    pin.update(overrides)
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(pin), encoding="utf-8")
    return path


def _opener(payload: bytes, seen: dict):
    def open_request(request, *, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["user_agent"] = request.headers.get("User-agent")
        return _Response(payload)

    return open_request


def test_install_downloads_verified_pin_and_records_manifest(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive)
    root = tmp_path / "managed"
    seen = {}

    result = install_managed_platform_tools(
        license_accepted=True,
        root=root,
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, seen),
        now=lambda: datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc),
    )

    assert result["success"] is True
    assert result["result_code"] == "ok"
    assert result["data"] == {
        "version": "r37.0.0",
        "adb_path": str(root / "platform-tools" / "adb"),
        "verified": True,
    }
    assert seen["url"].startswith("https://dl.google.com/")
    assert seen["timeout"] == 45.0
    assert seen["user_agent"] == "VRhotspot-Developer-Hub/1.1"

    adb = root / "platform-tools" / "adb"
    notice = root / "platform-tools" / "NOTICE.txt"
    assert adb.is_file()
    assert notice.is_file()
    assert os.access(adb, os.X_OK)
    assert stat.S_IMODE(adb.stat().st_mode) == 0o755
    assert stat.S_IMODE(notice.stat().st_mode) == 0o644
    assert not (root / "platform-tools" / "ignored-tool").exists()
    assert not list(root.glob(".platform-tools-staging-*"))
    assert not list(root.rglob("*.zip"))

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "r37.0.0"
    assert manifest["archive_sha256"] == hashlib.sha256(archive).hexdigest()
    assert manifest["installed_at"] == "2026-08-01T20:00:00+00:00"
    assert {entry["path"] for entry in manifest["files"]} == {
        "platform-tools/adb",
        "platform-tools/NOTICE.txt",
    }
    assert inspect_managed_platform_tools(root)["verified"] is True


def test_install_requires_explicit_license_acceptance(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive)
    called = False

    def must_not_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("download must not run")

    result = install_managed_platform_tools(
        license_accepted=False,
        root=tmp_path / "managed",
        pin_path=pin,
        host_machine="x86_64",
        opener=must_not_open,
    )

    assert result["success"] is False
    assert result["result_code"] == "license_not_accepted"
    assert called is False


def test_checksum_mismatch_leaves_no_install_or_archive(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive, archive_sha256="0" * 64)
    root = tmp_path / "managed"

    result = install_managed_platform_tools(
        license_accepted=True,
        root=root,
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )

    assert result["success"] is False
    assert result["result_code"] == "checksum_mismatch"
    assert not (root / "platform-tools").exists()
    assert not (root / "manifest.json").exists()
    assert not list(root.glob(".platform-tools-staging-*"))
    assert not list(root.rglob("*.zip"))


def test_install_rejects_unsupported_arch_before_download(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive)
    called = False

    def must_not_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("download must not run")

    result = install_managed_platform_tools(
        license_accepted=True,
        root=tmp_path / "managed",
        pin_path=pin,
        host_machine="aarch64",
        opener=must_not_open,
    )

    assert result["result_code"] == "unsupported_arch"
    assert called is False


def test_archive_missing_required_file_is_rejected(tmp_path):
    archive = _archive(include_notice=False)
    pin = _pin(tmp_path, archive)
    root = tmp_path / "managed"

    result = install_managed_platform_tools(
        license_accepted=True,
        root=root,
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )

    assert result["result_code"] == "archive_invalid"
    assert not (root / "platform-tools").exists()


def test_archive_symlink_entry_is_rejected(tmp_path):
    archive = _archive(symlink_adb=True)
    pin = _pin(tmp_path, archive)

    result = install_managed_platform_tools(
        license_accepted=True,
        root=tmp_path / "managed",
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )

    assert result["result_code"] == "archive_invalid"


def test_download_size_cap_is_enforced(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive, max_archive_bytes=len(archive) - 1)

    result = install_managed_platform_tools(
        license_accepted=True,
        root=tmp_path / "managed",
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )

    assert result["result_code"] == "archive_too_large"


def test_remove_deletes_only_managed_tree_and_manifest(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive)
    root = tmp_path / "managed"
    unrelated = root / "keep-me.txt"

    installed = install_managed_platform_tools(
        license_accepted=True,
        root=root,
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )
    unrelated.write_text("unrelated", encoding="utf-8")

    removed = remove_managed_platform_tools(root=root)

    assert installed["success"] is True
    assert removed["success"] is True
    assert removed["data"]["removed"] is True
    assert not (root / "platform-tools").exists()
    assert not (root / "manifest.json").exists()
    assert unrelated.read_text(encoding="utf-8") == "unrelated"


def test_remove_refuses_unrecognized_tree_without_manifest(tmp_path):
    root = tmp_path / "managed"
    tree = root / "platform-tools"
    tree.mkdir(parents=True)
    (tree / "adb").write_bytes(b"unknown")

    result = remove_managed_platform_tools(root=root)

    assert result["success"] is False
    assert result["result_code"] == "manifest_invalid"
    assert (tree / "adb").exists()


def test_manifest_tampering_disables_verification(tmp_path):
    archive = _archive()
    pin = _pin(tmp_path, archive)
    root = tmp_path / "managed"
    installed = install_managed_platform_tools(
        license_accepted=True,
        root=root,
        pin_path=pin,
        host_machine="x86_64",
        opener=_opener(archive, {}),
    )
    (root / "platform-tools" / "adb").write_bytes(b"tampered")

    inspected = inspect_managed_platform_tools(root)

    assert installed["success"] is True
    assert inspected["installed"] is False
    assert inspected["verified"] is False
    assert inspected["error"] == "manifest_invalid"
