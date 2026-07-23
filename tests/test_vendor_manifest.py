from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.ci import vendor_manifest_check as checker


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / checker.MANIFEST_PATH


def _manifest():
    return checker.load_manifest(MANIFEST_FILE)


def _errors(manifest):
    tracked = checker.discover_tracked_vendor_files(ROOT)
    return checker.validate_manifest(manifest, tracked, ROOT)


def test_repository_vendor_manifest_is_valid_complete_and_payload_hashes_match():
    assert _errors(_manifest()) == []


def test_repository_payload_bytes_match_declared_sha256_values():
    manifest = _manifest()

    for entry in manifest["files"]:
        payload = ROOT / entry["path"]
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == entry["sha256"]


def test_manifest_json_parse_failure_is_reported(tmp_path):
    invalid_manifest = tmp_path / "VENDOR_MANIFEST.json"
    invalid_manifest.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(checker.ManifestLoadError, match="cannot parse"):
        checker.load_manifest(invalid_manifest)


def test_manifest_requires_schema_entry_fields_and_lowercase_sha256():
    manifest = _manifest()
    manifest.pop("schema_version")
    manifest["files"][0].pop("purpose")
    manifest["files"][0]["sha256"] = "A" * 64

    errors = _errors(manifest)

    assert "manifest is missing required top-level field: schema_version" in errors
    assert "files[0] is missing required field: purpose" in errors
    assert "files[0].sha256 must be exactly 64 lowercase hexadecimal characters" in errors


def test_manifest_rejects_duplicate_unsorted_missing_and_outside_paths():
    duplicate = _manifest()
    duplicate["files"].append(deepcopy(duplicate["files"][0]))
    duplicate["manifest_scope"]["entry_count"] += 1
    assert any("manifest path appears more than once" in error for error in _errors(duplicate))

    unsorted = _manifest()
    unsorted["files"][0], unsorted["files"][1] = unsorted["files"][1], unsorted["files"][0]
    assert "manifest files entries must be sorted lexicographically by path" in _errors(unsorted)

    missing = _manifest()
    removed_path = missing["files"].pop()["path"]
    missing["manifest_scope"]["entry_count"] -= 1
    assert f"tracked vendor file is missing a manifest entry: {removed_path}" in _errors(missing)

    outside = _manifest()
    outside["files"][0]["path"] = "README.md"
    outside_errors = _errors(outside)
    assert any("points outside backend/vendor/" in error for error in outside_errors)
    assert any("tracked vendor file is missing a manifest entry" in error for error in outside_errors)


def test_manifest_excludes_itself_and_matches_executable_modes():
    manifest = _manifest()
    assert manifest["manifest_scope"]["excluded_paths"][0]["path"] == checker.MANIFEST_PATH

    manifest["files"][0]["executable"] = not manifest["files"][0]["executable"]
    errors = _errors(manifest)

    assert any("manifest executable does not match Git mode" in error for error in errors)
    assert any("manifest executable does not match working-tree mode" in error for error in errors)


def test_manifest_rejects_sha256_that_does_not_match_current_file_bytes():
    manifest = _manifest()
    path = manifest["files"][0]["path"]
    manifest["files"][0]["sha256"] = "0" * 64

    assert f"manifest SHA-256 does not match current file bytes for {path}" in _errors(manifest)


def test_unknown_provenance_fields_remain_allowed():
    manifest = _manifest()
    manifest["files"][0]["license_status"] = "unknown"
    manifest["files"][0]["provenance_status"] = "unknown_unverified"

    assert _errors(manifest) == []


def test_vendor_sbom_is_deterministic_sorted_and_bounded():
    manifest = _manifest()
    expected = checker.render_sbom(manifest)
    reordered = deepcopy(manifest)
    reordered["files"].reverse()

    assert checker.render_sbom(reordered) == expected

    sbom = json.loads(expected)
    component_paths = [component["bom-ref"] for component in sbom["components"]]
    declared_hashes = {entry["path"]: entry["sha256"] for entry in manifest["files"]}

    assert component_paths == sorted(component_paths)
    assert len(component_paths) == manifest["manifest_scope"]["entry_count"]
    assert all(
        component["hashes"] == [
            {"alg": "SHA-256", "content": declared_hashes[component["bom-ref"]]}
        ]
        for component in sbom["components"]
    )
    assert "backend/vendor only; not a full-project SBOM" in expected
    assert "CI/source-tree payload bytes compared before SBOM output" in expected
    assert "syntax-only" not in expected
    assert "timestamp" not in expected
    assert "serialNumber" not in expected
    assert str(ROOT) not in expected
