"""Bounded vendor provenance reporting for diagnostics support bundles only."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Dict, Iterator, Optional, Tuple


VENDOR_PROVENANCE_REPORT_SCHEMA_VERSION = 1
VENDOR_MANIFEST_PATH = "backend/vendor/VENDOR_MANIFEST.json"
VENDOR_POLICY_DOC_PATH = "docs/VENDOR_PROVENANCE_SBOM_PLAN.md"
VENDOR_PROVENANCE_ENFORCEMENT_BOUNDARY = "reporting_only"

_VENDOR_PATH_PREFIX = "backend/vendor/"
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_MANIFEST_ENTRIES = 512
_MAX_VENDOR_FILE_BYTES = 64 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_MANIFEST_STRING_LENGTH = 4096
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REPORTED_ENTRY_FIELDS = (
    "path",
    "file_type",
    "executable",
    "purpose",
    "source_project",
    "version",
    "license",
    "provenance_status",
    "sha256",
)


class _MissingLocalFile(Exception):
    pass


class _UnreadableLocalFile(Exception):
    pass


def collect_vendor_provenance(
    *,
    repository_root: Optional[Path] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Report manifest claims and local checksum state without enforcing either."""

    report = _empty_report(generated_at=generated_at)
    root = (
        Path(repository_root)
        if repository_root is not None
        else Path(__file__).resolve().parents[3]
    )

    try:
        raw_manifest = _read_bounded_regular_file(
            root,
            VENDOR_MANIFEST_PATH,
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
    except _MissingLocalFile:
        report["manifest_status"] = "missing"
        return report
    except _UnreadableLocalFile:
        report["manifest_status"] = "unreadable"
        return report

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
        entries = _validated_manifest_entries(manifest)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        report["manifest_status"] = "invalid"
        return report

    report["manifest_status"] = "present"
    report["manifest_schema_version"] = manifest["schema_version"]
    report["policy_doc_path"] = manifest["policy_doc"]
    report["total_manifest_entries"] = len(entries)

    reported_entries = []
    for entry in entries:
        reported = {field: entry[field] for field in _REPORTED_ENTRY_FIELDS}
        local_status, checksum_status, local_sha256 = _local_checksum_status(
            root,
            entry["path"],
            entry["sha256"],
        )
        reported["local_file_status"] = local_status
        reported["checksum_status"] = checksum_status
        if local_sha256 is not None:
            reported["local_computed_sha256"] = local_sha256
        reported_entries.append(reported)

    report["entries"] = reported_entries
    report["summary"] = _summary(reported_entries)
    return report


def unavailable_vendor_provenance_report(
    *,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a safe report shape when the provenance collector itself fails."""

    return _empty_report(generated_at=generated_at)


def _empty_report(*, generated_at: Optional[datetime]) -> Dict[str, Any]:
    when = generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    generated_at_text = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "report_schema_version": VENDOR_PROVENANCE_REPORT_SCHEMA_VERSION,
        "generated_at": generated_at_text,
        "manifest_status": "unreadable",
        "manifest_schema_version": None,
        "manifest_path": VENDOR_MANIFEST_PATH,
        "policy_doc_path": VENDOR_POLICY_DOC_PATH,
        "enforcement_boundary": VENDOR_PROVENANCE_ENFORCEMENT_BOUNDARY,
        "total_manifest_entries": 0,
        "entries": [],
        "summary": _summary([]),
    }


def _validated_manifest_entries(manifest: Any) -> list[Dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if not _bounded_string(manifest.get("schema_version")):
        raise ValueError("invalid schema version")
    if manifest.get("policy_doc") != VENDOR_POLICY_DOC_PATH:
        raise ValueError("invalid policy document path")

    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > _MAX_MANIFEST_ENTRIES:
        raise ValueError("invalid manifest file list")
    scope = manifest.get("manifest_scope")
    if not isinstance(scope, dict) or scope.get("entry_count") != len(files):
        raise ValueError("manifest entry count mismatch")

    entries: list[Dict[str, Any]] = []
    seen_paths = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("invalid manifest entry")
        path = entry.get("path")
        if not _safe_vendor_path(path) or path in seen_paths:
            raise ValueError("invalid or duplicate vendor path")
        seen_paths.add(path)

        if type(entry.get("executable")) is not bool:
            raise ValueError("invalid executable field")
        for field in (
            "file_type",
            "purpose",
            "source_project",
            "license",
            "provenance_status",
        ):
            if not _bounded_string(entry.get(field)):
                raise ValueError(f"invalid {field} field")
        version = entry.get("version")
        if version is not None and not _bounded_string(version):
            raise ValueError("invalid version field")
        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError("invalid sha256 field")
        entries.append(entry)

    return sorted(entries, key=lambda item: item["path"])


def _bounded_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= _MAX_MANIFEST_STRING_LENGTH
    )


def _safe_vendor_path(path: Any) -> bool:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > _MAX_MANIFEST_STRING_LENGTH
        or "\\" in path
        or "\x00" in path
    ):
        return False
    parsed = PurePosixPath(path)
    return (
        not parsed.is_absolute()
        and path == parsed.as_posix()
        and all(part not in {".", ".."} for part in parsed.parts)
        and path.startswith(_VENDOR_PATH_PREFIX)
        and path != _VENDOR_PATH_PREFIX.rstrip("/")
        and path != VENDOR_MANIFEST_PATH
    )


def _local_checksum_status(
    repository_root: Path,
    path: str,
    declared_sha256: str,
) -> Tuple[str, str, Optional[str]]:
    try:
        local_sha256 = _sha256_bounded_regular_file(
            repository_root,
            path,
            maximum_bytes=_MAX_VENDOR_FILE_BYTES,
        )
    except _MissingLocalFile:
        return "missing", "not_checked", None
    except _UnreadableLocalFile:
        return "unreadable", "not_checked", None

    if local_sha256 is None:
        return "present", "not_checked", None
    if local_sha256 == declared_sha256:
        return "present", "hash_match", local_sha256
    return "present", "hash_mismatch", local_sha256


def _summary(entries: list[Dict[str, Any]]) -> Dict[str, int]:
    summary = {
        "present": 0,
        "missing": 0,
        "unreadable": 0,
        "hash_match": 0,
        "hash_mismatch": 0,
        "not_checked": 0,
    }
    for entry in entries:
        summary[entry["local_file_status"]] += 1
        summary[entry["checksum_status"]] += 1
    return summary


def _read_bounded_regular_file(
    repository_root: Path,
    path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    with _open_regular_file_no_follow(repository_root, path) as (fd, size):
        if size > maximum_bytes:
            raise _UnreadableLocalFile
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(_HASH_CHUNK_BYTES, maximum_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise _UnreadableLocalFile
            chunks.append(chunk)
        return b"".join(chunks)


def _sha256_bounded_regular_file(
    repository_root: Path,
    path: str,
    *,
    maximum_bytes: int,
) -> Optional[str]:
    with _open_regular_file_no_follow(repository_root, path) as (fd, size):
        if size > maximum_bytes:
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                return None
            digest.update(chunk)
        return digest.hexdigest()


@contextmanager
def _open_regular_file_no_follow(
    repository_root: Path,
    path: str,
) -> Iterator[Tuple[int, int]]:
    if not _safe_repository_path(path):
        raise _UnreadableLocalFile

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors = []
    try:
        current_fd = os.open(repository_root, directory_flags)
        descriptors.append(current_fd)
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            current_fd = os.open(part, directory_flags, dir_fd=current_fd)
            descriptors.append(current_fd)
        file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        descriptors.append(file_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise _UnreadableLocalFile
        yield file_fd, file_stat.st_size
    except FileNotFoundError as exc:
        raise _MissingLocalFile from exc
    except _UnreadableLocalFile:
        raise
    except OSError as exc:
        raise _UnreadableLocalFile from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _safe_repository_path(path: str) -> bool:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    parsed = PurePosixPath(path)
    return (
        not parsed.is_absolute()
        and path == parsed.as_posix()
        and all(part not in {".", ".."} for part in parsed.parts)
    )
