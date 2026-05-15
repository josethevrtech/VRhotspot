import json
import re
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile

import pytest

from vr_hotspotd.diagnostics.support_bundle import (
    CollectorStatus,
    CollectedCommand,
    CollectedFile,
    SupportBundleCommandResult,
    SupportBundleFileResult,
    assemble_support_bundle,
)


def _read_archive(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_assembled_bundle_filename_format():
    bundle = assemble_support_bundle(
        generated_at=datetime(2026, 5, 9, 12, 34, 56, tzinfo=timezone.utc)
    )

    assert bundle.filename == "vr-hotspot-support-bundle-20260509-123456.zip"
    assert re.fullmatch(
        r"vr-hotspot-support-bundle-\d{8}-\d{6}\.zip",
        bundle.filename,
    )


def test_assembled_bundle_contains_manifest_readme_file_and_command_output():
    bundle = assemble_support_bundle(
        generated_at=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        files=[
            CollectedFile(
                result=SupportBundleFileResult(
                    path="system/os-release.txt",
                    collector="os release",
                    status=CollectorStatus.OK,
                    content_type="text/plain",
                    size_bytes=len("ID=steamos\n"),
                ),
                content="ID=steamos\n",
            )
        ],
        commands=[
            CollectedCommand(
                result=SupportBundleCommandResult(
                    command=["iw", "dev"],
                    status=CollectorStatus.OK,
                    exit_code=0,
                ),
                stdout="Interface wlan0\nssid=<redacted-secret>\n",
                stderr="warning: <redacted-mac-1>\n",
            )
        ],
        readme="README for assembly test\n",
    )

    members = _read_archive(bundle.archive_bytes)
    manifest = json.loads(members["manifest.json"])

    assert "manifest.json" in members
    assert members["README.txt"] == b"README for assembly test\n"
    assert members["system/os-release.txt"] == b"ID=steamos\n"
    assert members["commands/001-iw-dev-stdout.txt"] == (
        b"Interface wlan0\nssid=<redacted-secret>\n"
    )
    assert members["commands/001-iw-dev-stderr.txt"] == b"warning: <redacted-mac-1>\n"
    assert manifest == bundle.manifest
    assert manifest["command_results"] == [
        {
            "command": "iw dev",
            "status": "ok",
            "exit_code": 0,
            "stdout_path": "commands/001-iw-dev-stdout.txt",
            "stderr_path": "commands/001-iw-dev-stderr.txt",
        }
    ]
    assert {
        "path": "system/os-release.txt",
        "collector": "os release",
        "status": "ok",
        "content_type": "text/plain",
        "size_bytes": len("ID=steamos\n"),
    } in manifest["files"]
    assert {
        "path": "commands/001-iw-dev-stdout.txt",
        "collector": "iw dev",
        "status": "ok",
        "content_type": "text/plain",
        "size_bytes": len("Interface wlan0\nssid=<redacted-secret>\n"),
    } in manifest["files"]


def test_assembled_bundle_omits_redaction_failed_raw_content_files():
    bundle = assemble_support_bundle(
        generated_at=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
        files=[
            CollectedFile(
                result=SupportBundleFileResult(
                    path="vr-hotspot/config.redacted.env",
                    collector="config",
                    status=CollectorStatus.REDACTION_FAILED,
                    size_bytes=0,
                    error_summary="redaction failed; raw file content omitted",
                ),
                content="VR_HOTSPOTD_API_TOKEN=raw-file-secret\n",
            )
        ],
        commands=[
            CollectedCommand(
                result=SupportBundleCommandResult(
                    command=["print-secrets"],
                    status=CollectorStatus.REDACTION_FAILED,
                    exit_code=0,
                    error_summary="redaction failed; raw command output omitted",
                ),
                stdout="VR_HOTSPOTD_API_TOKEN=raw-command-secret\n",
                stderr="wpa_passphrase=raw-stderr-secret\n",
            )
        ],
    )

    members = _read_archive(bundle.archive_bytes)
    manifest = json.loads(members["manifest.json"])
    combined_content = b"".join(members.values())

    assert "vr-hotspot/config.redacted.env" not in members
    assert not any(name.startswith("commands/") for name in members)
    assert b"raw-file-secret" not in combined_content
    assert b"raw-command-secret" not in combined_content
    assert b"raw-stderr-secret" not in combined_content
    assert manifest["files"] == [
        {
            "path": "vr-hotspot/config.redacted.env",
            "collector": "config",
            "status": "redaction_failed",
            "content_type": "text/plain",
            "size_bytes": 0,
            "error_summary": "redaction failed; raw file content omitted",
        }
    ]
    assert manifest["command_results"] == [
        {
            "command": "print-secrets",
            "status": "redaction_failed",
            "exit_code": 0,
            "error_summary": "redaction failed; raw command output omitted",
        }
    ]


def test_assembled_bundle_rejects_unsafe_collected_file_paths():
    with pytest.raises(ValueError, match="traversal"):
        assemble_support_bundle(
            files=[
                CollectedFile(
                    result=SupportBundleFileResult(
                        path="../raw-secret.txt",
                        collector="unsafe",
                        status=CollectorStatus.OK,
                        size_bytes=9,
                    ),
                    content="sanitized\n",
                )
            ]
        )
