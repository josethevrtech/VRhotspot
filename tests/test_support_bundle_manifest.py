from datetime import datetime, timezone

from vr_hotspotd import __version__
from vr_hotspotd.diagnostics.support_bundle import (
    CollectorStatus,
    SECRET_PLACEHOLDER,
    SupportBundleCommandResult,
    SupportBundleFileResult,
    default_support_bundle_archive_layout,
    make_support_bundle_manifest,
)


def _manifest(**kwargs):
    kwargs.setdefault("generated_at", datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc))
    return make_support_bundle_manifest(**kwargs)


def test_manifest_includes_bundle_schema_version():
    manifest = _manifest()

    assert manifest["bundle_schema_version"] == 1


def test_manifest_includes_generated_at():
    manifest = _manifest()

    assert manifest["generated_at"] == "2026-05-09T12:00:00Z"


def test_manifest_includes_vr_hotspot_version():
    manifest = _manifest()

    assert manifest["vr_hotspot_version"] == __version__


def test_manifest_includes_redaction_policy():
    manifest = _manifest()

    assert manifest["redaction_policy"] == {
        "secrets": "redacted",
        "emails_usernames": "redacted_when_detected",
        "public_ips": "redacted",
        "mac_addresses": "redacted_by_default",
    }


def test_manifest_includes_file_metadata_and_archive_layout_metadata():
    file_result = SupportBundleFileResult(
        path="wireless/iw-dev.txt",
        collector="iw dev",
        status=CollectorStatus.OK,
        content_type="text/plain",
        size_bytes=842,
    )

    manifest = _manifest(files=[file_result])
    layout = default_support_bundle_archive_layout()

    assert manifest["files"] == [
        {
            "path": "wireless/iw-dev.txt",
            "collector": "iw dev",
            "status": "ok",
            "content_type": "text/plain",
            "size_bytes": 842,
        }
    ]
    assert {
        "path": "manifest.json",
        "collector": "manifest",
        "content_type": "application/json",
    } in layout
    assert {
        "path": "wireless/iw-dev.txt",
        "collector": "iw dev",
        "content_type": "text/plain",
    } in layout
    assert {
        "path": "vr-hotspot/vendor_provenance.json",
        "collector": "vendor provenance",
        "content_type": "application/json",
    } in layout


def test_manifest_records_missing_command():
    manifest = _manifest(
        command_results=[
            SupportBundleCommandResult(
                command=["iw", "dev"],
                status=CollectorStatus.MISSING_COMMAND,
                exit_code=None,
                error_summary="iw not found",
            )
        ]
    )

    assert manifest["command_results"] == [
        {
            "command": "iw dev",
            "status": "missing_command",
            "error_summary": "iw not found",
        }
    ]


def test_manifest_records_permission_denied():
    manifest = _manifest(
        command_results=[
            SupportBundleCommandResult(
                command="journalctl -u vr-hotspotd.service -n 300 --no-pager",
                status=CollectorStatus.PERMISSION_DENIED,
                exit_code=1,
                permission_denied=True,
                error_summary="permission denied reading journal",
            )
        ]
    )

    assert manifest["command_results"][0] == {
        "command": "journalctl -u vr-hotspotd.service -n 300 --no-pager",
        "status": "permission_denied",
        "exit_code": 1,
        "permission_denied": True,
        "error_summary": "permission denied reading journal",
    }


def test_manifest_records_timeout():
    manifest = _manifest(
        command_results=[
            SupportBundleCommandResult(
                command="systemctl status vr-hotspotd.service --no-pager",
                status=CollectorStatus.TIMEOUT,
                exit_code=None,
                timed_out=True,
                error_summary="collector timed out after 2s",
            )
        ]
    )

    assert manifest["command_results"][0] == {
        "command": "systemctl status vr-hotspotd.service --no-pager",
        "status": "timeout",
        "timed_out": True,
        "error_summary": "collector timed out after 2s",
    }


def test_manifest_records_failed():
    manifest = _manifest(
        command_results=[
            SupportBundleCommandResult(
                command="nmcli device status",
                status=CollectorStatus.FAILED,
                exit_code=10,
                error_summary="nmcli failed for alice@example.com",
            )
        ]
    )

    assert manifest["command_results"][0] == {
        "command": "nmcli device status",
        "status": "failed",
        "exit_code": 10,
        "error_summary": "nmcli failed for <redacted-email-1>",
    }


def test_manifest_records_redaction_failed():
    manifest = _manifest(
        files=[
            SupportBundleFileResult(
                path="vr-hotspot/config.redacted.json",
                collector="vr hotspot config",
                status=CollectorStatus.REDACTION_FAILED,
                content_type="application/json",
                size_bytes=0,
                error_summary="could not sanitize token=super-secret-token",
            )
        ],
        command_results=[
            SupportBundleCommandResult(
                command="cat /etc/vr-hotspot/config.json",
                status=CollectorStatus.REDACTION_FAILED,
                exit_code=0,
                error_summary="redaction failed for VR_HOTSPOTD_API_TOKEN=super-secret-token",
            )
        ],
        warnings=["redaction failed; raw output omitted"],
    )

    assert manifest["files"][0] == {
        "path": "vr-hotspot/config.redacted.json",
        "collector": "vr hotspot config",
        "status": "redaction_failed",
        "content_type": "application/json",
        "size_bytes": 0,
        "error_summary": f"could not sanitize token={SECRET_PLACEHOLDER}",
    }
    assert manifest["command_results"][0] == {
        "command": "cat /etc/vr-hotspot/config.json",
        "status": "redaction_failed",
        "exit_code": 0,
        "error_summary": f"redaction failed for VR_HOTSPOTD_API_TOKEN={SECRET_PLACEHOLDER}",
    }
    assert manifest["warnings"] == ["redaction failed; raw output omitted"]
