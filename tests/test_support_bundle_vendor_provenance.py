from datetime import datetime, timezone
import hashlib
import json

from vr_hotspotd.diagnostics.vendor_provenance import collect_vendor_provenance


GENERATED_AT = datetime(2026, 7, 22, 14, 30, tzinfo=timezone.utc)


def _entry(path, declared_sha256, *, provenance_status="unknown_unverified"):
    return {
        "path": path,
        "file_type": "test_payload",
        "executable": False,
        "purpose": "Exercises bounded support-bundle provenance reporting.",
        "source_project": "test-project",
        "version": None,
        "license": "unknown",
        "provenance_status": provenance_status,
        "sha256": declared_sha256,
    }


def _write_manifest(repository_root, entries):
    manifest_path = repository_root / "backend/vendor/VENDOR_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "policy_doc": "docs/VENDOR_PROVENANCE_SBOM_PLAN.md",
                "manifest_scope": {"entry_count": len(entries)},
                "files": entries,
            }
        ),
        encoding="utf-8",
    )


def test_vendor_provenance_reports_hash_match_without_file_contents(tmp_path):
    payload_bytes = b"payload-contents-must-not-appear"
    payload_path = "backend/vendor/bin/example"
    local_path = tmp_path / payload_path
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(payload_bytes)
    declared_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    _write_manifest(tmp_path, [_entry(payload_path, declared_sha256)])

    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["generated_at"] == "2026-07-22T14:30:00Z"
    assert report["manifest_status"] == "present"
    assert report["manifest_schema_version"] == "1.0.0"
    assert report["manifest_path"] == "backend/vendor/VENDOR_MANIFEST.json"
    assert report["policy_doc_path"] == "docs/VENDOR_PROVENANCE_SBOM_PLAN.md"
    assert report["enforcement_boundary"] == "reporting_only"
    assert report["total_manifest_entries"] == 1
    assert report["entries"] == [
        {
            "path": payload_path,
            "file_type": "test_payload",
            "executable": False,
            "purpose": "Exercises bounded support-bundle provenance reporting.",
            "source_project": "test-project",
            "version": None,
            "license": "unknown",
            "provenance_status": "unknown_unverified",
            "sha256": declared_sha256,
            "local_file_status": "present",
            "checksum_status": "hash_match",
            "local_computed_sha256": declared_sha256,
        }
    ]
    assert report["summary"] == {
        "present": 1,
        "missing": 0,
        "unreadable": 0,
        "hash_match": 1,
        "hash_mismatch": 0,
        "not_checked": 0,
    }
    assert payload_bytes.decode("utf-8") not in json.dumps(report)


def test_vendor_provenance_reports_missing_file_without_raising(tmp_path):
    payload_path = "backend/vendor/bin/missing"
    _write_manifest(
        tmp_path,
        [_entry(payload_path, hashlib.sha256(b"expected").hexdigest())],
    )

    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["entries"][0]["local_file_status"] == "missing"
    assert report["entries"][0]["checksum_status"] == "not_checked"
    assert "local_computed_sha256" not in report["entries"][0]
    assert report["summary"]["missing"] == 1
    assert report["summary"]["not_checked"] == 1


def test_vendor_provenance_reports_hash_mismatch_as_finding(tmp_path):
    payload_path = "backend/vendor/bin/changed"
    local_path = tmp_path / payload_path
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"current-local-bytes")
    declared_sha256 = hashlib.sha256(b"expected-reviewed-bytes").hexdigest()
    local_sha256 = hashlib.sha256(b"current-local-bytes").hexdigest()
    _write_manifest(tmp_path, [_entry(payload_path, declared_sha256)])

    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["entries"][0]["local_file_status"] == "present"
    assert report["entries"][0]["checksum_status"] == "hash_mismatch"
    assert report["entries"][0]["local_computed_sha256"] == local_sha256
    assert report["summary"]["hash_mismatch"] == 1


def test_vendor_provenance_refuses_symlinked_payload(tmp_path):
    outside_payload = tmp_path / "outside-payload"
    outside_payload.write_bytes(b"outside-file-contents-must-not-appear")
    payload_path = "backend/vendor/bin/symlinked"
    local_path = tmp_path / payload_path
    local_path.parent.mkdir(parents=True)
    local_path.symlink_to(outside_payload)
    _write_manifest(
        tmp_path,
        [_entry(payload_path, hashlib.sha256(outside_payload.read_bytes()).hexdigest())],
    )

    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["entries"][0]["local_file_status"] == "unreadable"
    assert report["entries"][0]["checksum_status"] == "not_checked"
    assert "outside-file-contents-must-not-appear" not in json.dumps(report)


def test_vendor_provenance_reports_missing_manifest_without_raising(tmp_path):
    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["manifest_status"] == "missing"
    assert report["manifest_schema_version"] is None
    assert report["total_manifest_entries"] == 0
    assert report["entries"] == []


def test_vendor_provenance_rejects_manifest_path_outside_vendor_scope(tmp_path):
    _write_manifest(
        tmp_path,
        [_entry("../../etc/passwd", hashlib.sha256(b"not-read").hexdigest())],
    )

    report = collect_vendor_provenance(
        repository_root=tmp_path,
        generated_at=GENERATED_AT,
    )

    assert report["manifest_status"] == "invalid"
    assert report["entries"] == []
