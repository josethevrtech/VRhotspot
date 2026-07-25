#!/usr/bin/env python3
"""Validate the Dev Bridge Platform-Tools pin metadata file.

This CI tool checks the reviewed pin schema offline and deterministically. It never
fetches the pinned archive or the license terms page, and it is metadata validation
only — not downloader, installer, or runtime behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = "backend/vr_hotspotd/devtools/platform_tools_pin.json"
BLOCKED_SHA256_PLACEHOLDER = (
    "BLOCKED-PLACEHOLDER-DO-NOT-IMPLEMENT-DOWNLOADER-SHA256-NOT-INDEPENDENTLY-VERIFIED"
)
EXPECTED_HOST = "dl.google.com"
EXPECTED_LICENSE_NAME = "Android SDK License Agreement"
EXPECTED_LICENSE_TERMS_URL = "https://developer.android.com/studio/terms"
REQUIRED_ALLOWLIST_ENTRIES = ("platform-tools/NOTICE.txt", "platform-tools/adb")

SHA256_RE = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"r[0-9]+\.[0-9]+\.[0-9]+")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

REQUIRED_PIN_FIELDS = frozenset(
    {
        "schema_version",
        "version",
        "url",
        "archive_sha256",
        "implementation_blocked",
        "max_archive_bytes",
        "arch",
        "extract_allowlist",
        "license_name",
        "license_terms_url",
        "license_review",
    }
)
REQUIRED_REVIEW_FIELDS = frozenset(
    {
        "reviewed_at",
        "reviewer",
        "terms_url",
        "decision",
        "rationale",
        "restrictions",
        "record",
    }
)


class PinLoadError(ValueError):
    """The pin file could not be loaded as a JSON object."""


def load_pin(path: Path) -> dict[str, Any]:
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PinLoadError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(pin, dict):
        raise PinLoadError(f"cannot parse {path}: top-level JSON value must be an object")
    return pin


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_string_list(
    errors: list[str], value: Any, label: str, *, require_nonempty: bool = False
) -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array of non-empty strings")
        return
    if require_nonempty and not value:
        errors.append(f"{label} must not be empty")
    for index, item in enumerate(value):
        if not _is_nonempty_string(item):
            errors.append(f"{label}[{index}] must be a non-empty string")


def _validate_url(pin: dict[str, Any], errors: list[str]) -> None:
    url = pin.get("url")
    if not _is_nonempty_string(url):
        errors.append("url must be a non-empty string")
        return
    parsed = urlparse(url)
    if parsed.scheme != "https":
        errors.append(f"url must use https: {url!r}")
    if parsed.netloc != EXPECTED_HOST:
        errors.append(f"url host must be exactly {EXPECTED_HOST!r}: {url!r}")
    if "latest" in url:
        errors.append(f"url must pin a specific release, never a latest endpoint: {url!r}")
    version = pin.get("version")
    if _is_nonempty_string(version) and not url.endswith(f"platform-tools_{version}-linux.zip"):
        errors.append(
            f"url must end with the versioned archive name platform-tools_{version}-linux.zip: "
            f"{url!r}"
        )


def _validate_archive_sha256(pin: dict[str, Any], errors: list[str]) -> None:
    sha256 = pin.get("archive_sha256")
    if not isinstance(sha256, str):
        errors.append("archive_sha256 must be a string")
        return
    if sha256 == BLOCKED_SHA256_PLACEHOLDER:
        if pin.get("implementation_blocked") is not True:
            errors.append(
                "implementation_blocked must be true while archive_sha256 is the "
                "blocked placeholder"
            )
        return
    if SHA256_RE.fullmatch(sha256) is None:
        errors.append(
            "archive_sha256 must be exactly 64 lowercase hexadecimal characters or the "
            f"blocked placeholder {BLOCKED_SHA256_PLACEHOLDER!r}"
        )


def _validate_extract_allowlist(pin: dict[str, Any], errors: list[str]) -> None:
    allowlist = pin.get("extract_allowlist")
    _validate_string_list(errors, allowlist, "extract_allowlist", require_nonempty=True)
    if not isinstance(allowlist, list):
        return
    entries = [entry for entry in allowlist if _is_nonempty_string(entry)]
    for entry in entries:
        parts = entry.split("/")
        if entry.startswith("/") or "\\" in entry or any(part in {"", ".", ".."} for part in parts):
            errors.append(f"extract_allowlist entry is not a normalized relative path: {entry!r}")
        elif not entry.startswith("platform-tools/"):
            errors.append(f"extract_allowlist entry must be under platform-tools/: {entry!r}")
    if len(set(entries)) != len(entries):
        errors.append("extract_allowlist entries must be unique")
    if entries != sorted(entries):
        errors.append("extract_allowlist entries must be sorted lexicographically")
    for required in REQUIRED_ALLOWLIST_ENTRIES:
        if required not in entries:
            errors.append(f"extract_allowlist must include {required!r}")


def _validate_license_review(pin: dict[str, Any], errors: list[str]) -> None:
    review = pin.get("license_review")
    if not isinstance(review, dict):
        errors.append("license_review must be an object")
        return
    for field in sorted(REQUIRED_REVIEW_FIELDS.difference(review)):
        errors.append(f"license_review is missing required field: {field}")

    for field in ("reviewer", "decision", "rationale"):
        if field in review and not _is_nonempty_string(review.get(field)):
            errors.append(f"license_review.{field} must be a non-empty string")

    reviewed_at = review.get("reviewed_at")
    if "reviewed_at" in review and (
        not isinstance(reviewed_at, str) or DATE_RE.fullmatch(reviewed_at) is None
    ):
        errors.append("license_review.reviewed_at must be an ISO date (YYYY-MM-DD)")

    if "terms_url" in review and review.get("terms_url") != EXPECTED_LICENSE_TERMS_URL:
        errors.append(f"license_review.terms_url must be {EXPECTED_LICENSE_TERMS_URL!r}")

    if "restrictions" in review:
        _validate_string_list(
            errors, review.get("restrictions"), "license_review.restrictions", require_nonempty=True
        )

    record = review.get("record")
    if "record" in review:
        if not _is_nonempty_string(record) or record.startswith("/") or ".." in record:
            errors.append("license_review.record must be a normalized repository-relative path")
        elif not (REPO_ROOT / record).is_file():
            errors.append(f"license_review.record does not exist in the repository: {record!r}")


def validate_pin(pin: dict[str, Any]) -> list[str]:
    """Return deterministic validation errors for the pin metadata."""

    errors: list[str] = []
    for field in sorted(REQUIRED_PIN_FIELDS.difference(pin)):
        errors.append(f"pin is missing required field: {field}")

    if pin.get("schema_version") != 1:
        errors.append("schema_version must be the supported value 1")

    version = pin.get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        errors.append("version must be a specific release of the form rMAJOR.MINOR.PATCH")

    _validate_url(pin, errors)
    _validate_archive_sha256(pin, errors)

    if type(pin.get("implementation_blocked")) is not bool:
        errors.append("implementation_blocked must be a boolean")

    max_archive_bytes = pin.get("max_archive_bytes")
    if type(max_archive_bytes) is not int or max_archive_bytes <= 0:
        errors.append("max_archive_bytes must be a positive integer")

    if pin.get("arch") != "x86_64":
        errors.append("arch must be 'x86_64' (Linux Platform-Tools are published for x86_64 only)")

    _validate_extract_allowlist(pin, errors)

    if pin.get("license_name") != EXPECTED_LICENSE_NAME:
        errors.append(f"license_name must be {EXPECTED_LICENSE_NAME!r}")
    if pin.get("license_terms_url") != EXPECTED_LICENSE_TERMS_URL:
        errors.append(f"license_terms_url must be {EXPECTED_LICENSE_TERMS_URL!r}")

    _validate_license_review(pin, errors)

    if "notes" in pin:
        _validate_string_list(errors, pin.get("notes"), "notes")

    return errors


def main() -> int:
    pin_file = REPO_ROOT / PIN_PATH
    try:
        pin = load_pin(pin_file)
    except PinLoadError as exc:
        print(f"platform-tools pin validation failed: {exc}", file=sys.stderr)
        return 1

    errors = validate_pin(pin)
    if errors:
        print("platform-tools pin validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"platform-tools pin validation passed: {PIN_PATH}")
    if pin["archive_sha256"] == BLOCKED_SHA256_PLACEHOLDER:
        print(
            "downloader implementation remains blocked: archive_sha256 is the placeholder "
            "and implementation_blocked is true"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
