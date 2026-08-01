from pathlib import Path

import pytest

from tools.ci import platform_tools_pin_check as checker


ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / checker.PIN_PATH


def _pin():
    return checker.load_pin(PIN_FILE)


def test_repository_pin_is_valid_verified_and_unblocked():
    pin = _pin()

    assert checker.validate_pin(pin) == []
    assert checker.SHA256_RE.fullmatch(pin["archive_sha256"]) is not None
    assert pin["archive_sha256"] != checker.BLOCKED_SHA256_PLACEHOLDER
    assert pin["implementation_blocked"] is False
    assert pin["license_review"]["record"] == "docs/devbridge-platform-tools-license-review.md"


def test_pin_json_parse_failure_is_reported(tmp_path):
    invalid_pin = tmp_path / "platform_tools_pin.json"
    invalid_pin.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(checker.PinLoadError, match="cannot parse"):
        checker.load_pin(invalid_pin)


def test_pin_requires_schema_fields_and_complete_license_review():
    pin = _pin()
    pin.pop("schema_version")
    pin["license_review"].pop("decision")
    pin["license_review"]["restrictions"] = []

    errors = checker.validate_pin(pin)

    assert "pin is missing required field: schema_version" in errors
    assert "license_review is missing required field: decision" in errors
    assert "license_review.restrictions must not be empty" in errors


def test_pin_rejects_http_wrong_host_latest_and_unversioned_urls():
    http_pin = _pin()
    http_pin["url"] = http_pin["url"].replace("https://", "http://")
    assert any("must use https" in error for error in checker.validate_pin(http_pin))

    host_pin = _pin()
    host_pin["url"] = f"https://example.com/android/repository/platform-tools_{host_pin['version']}-linux.zip"
    assert any("host must be exactly 'dl.google.com'" in error for error in checker.validate_pin(host_pin))

    latest_pin = _pin()
    latest_pin["url"] = "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
    latest_errors = checker.validate_pin(latest_pin)
    assert any("never a latest endpoint" in error for error in latest_errors)
    assert any("versioned archive name" in error for error in latest_errors)

    mismatched_pin = _pin()
    mismatched_pin["url"] = "https://dl.google.com/android/repository/platform-tools_r35.0.2-linux.zip"
    assert any("versioned archive name" in error for error in checker.validate_pin(mismatched_pin))


def test_pin_couples_placeholder_sha256_to_implementation_blocked():
    unblocked_placeholder_pin = _pin()
    unblocked_placeholder_pin["archive_sha256"] = checker.BLOCKED_SHA256_PLACEHOLDER
    unblocked_placeholder_pin["implementation_blocked"] = False
    assert (
        "implementation_blocked must be true while archive_sha256 is the blocked placeholder"
        in checker.validate_pin(unblocked_placeholder_pin)
    )

    blocked_placeholder_pin = _pin()
    blocked_placeholder_pin["archive_sha256"] = checker.BLOCKED_SHA256_PLACEHOLDER
    blocked_placeholder_pin["implementation_blocked"] = True
    assert checker.validate_pin(blocked_placeholder_pin) == []

    malformed_pin = _pin()
    malformed_pin["archive_sha256"] = "A" * 64
    assert any(
        "64 lowercase hexadecimal characters" in error
        for error in checker.validate_pin(malformed_pin)
    )

    real_pin = _pin()
    real_pin["archive_sha256"] = "0" * 64
    real_pin["implementation_blocked"] = False
    assert checker.validate_pin(real_pin) == []


def test_pin_allowlist_requires_notice_adb_normalized_sorted_unique_entries():
    empty_pin = _pin()
    empty_pin["extract_allowlist"] = []
    empty_errors = checker.validate_pin(empty_pin)
    assert "extract_allowlist must not be empty" in empty_errors
    assert "extract_allowlist must include 'platform-tools/NOTICE.txt'" in empty_errors
    assert "extract_allowlist must include 'platform-tools/adb'" in empty_errors

    traversal_pin = _pin()
    traversal_pin["extract_allowlist"] = [
        "/etc/passwd",
        "platform-tools/../adb",
        "platform-tools/NOTICE.txt",
        "platform-tools/adb",
    ]
    traversal_errors = checker.validate_pin(traversal_pin)
    assert any("not a normalized relative path" in error for error in traversal_errors)

    outside_pin = _pin()
    outside_pin["extract_allowlist"] = ["other-tools/adb", "platform-tools/NOTICE.txt"]
    outside_errors = checker.validate_pin(outside_pin)
    assert any("must be under platform-tools/" in error for error in outside_errors)
    assert "extract_allowlist must include 'platform-tools/adb'" in outside_errors

    unsorted_pin = _pin()
    unsorted_pin["extract_allowlist"] = ["platform-tools/adb", "platform-tools/NOTICE.txt"]
    assert (
        "extract_allowlist entries must be sorted lexicographically"
        in checker.validate_pin(unsorted_pin)
    )

    duplicate_pin = _pin()
    duplicate_pin["extract_allowlist"] = [
        "platform-tools/NOTICE.txt",
        "platform-tools/adb",
        "platform-tools/adb",
    ]
    assert "extract_allowlist entries must be unique" in checker.validate_pin(duplicate_pin)


def test_pin_license_fields_and_review_values_are_exact():
    name_pin = _pin()
    name_pin["license_name"] = "Apache-2.0"
    assert any("license_name must be" in error for error in checker.validate_pin(name_pin))

    terms_pin = _pin()
    terms_pin["license_terms_url"] = "http://developer.android.com/studio/terms"
    terms_pin["license_review"]["terms_url"] = "https://example.com/terms"
    terms_errors = checker.validate_pin(terms_pin)
    assert any("license_terms_url must be" in error for error in terms_errors)
    assert any("license_review.terms_url must be" in error for error in terms_errors)

    date_pin = _pin()
    date_pin["license_review"]["reviewed_at"] = "July 25, 2026"
    assert (
        "license_review.reviewed_at must be an ISO date (YYYY-MM-DD)"
        in checker.validate_pin(date_pin)
    )

    record_pin = _pin()
    record_pin["license_review"]["record"] = "docs/does-not-exist.md"
    assert any(
        "license_review.record does not exist" in error
        for error in checker.validate_pin(record_pin)
    )


def test_pin_rejects_wrong_arch_version_and_size_values():
    pin = _pin()
    pin["arch"] = "arm64"
    pin["version"] = "latest"
    pin["max_archive_bytes"] = 0

    errors = checker.validate_pin(pin)

    assert any("arch must be 'x86_64'" in error for error in errors)
    assert "version must be a specific release of the form rMAJOR.MINOR.PATCH" in errors
    assert "max_archive_bytes must be a positive integer" in errors
