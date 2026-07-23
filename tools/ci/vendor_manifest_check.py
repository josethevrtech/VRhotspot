#!/usr/bin/env python3
"""Validate the source-tree vendor manifest and emit a deterministic vendor-only SBOM.

This CI tool intentionally validates only SHA-256 syntax. It does not hash or compare
vendored payload bytes and is not installer or runtime security enforcement.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_ROOT = "backend/vendor/"
MANIFEST_PATH = "backend/vendor/VENDOR_MANIFEST.json"
POLICY_PATH = "docs/VENDOR_PROVENANCE_SBOM_PLAN.md"
DEFAULT_SBOM_PATH = Path("/tmp/vrhotspot-vendor-sbom.json")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

REQUIRED_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "maintained_by",
        "policy_doc",
        "enforcement_status",
        "notes",
        "manifest_scope",
        "hashing",
        "files",
    }
)
REQUIRED_SCOPE_FIELDS = frozenset({"root", "entry_count", "coverage", "excluded_paths"})
REQUIRED_HASHING_FIELDS = frozenset({"algorithm", "input", "status"})
REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "path",
        "file_type",
        "executable",
        "purpose",
        "source_project",
        "upstream_url",
        "version",
        "version_evidence",
        "license",
        "license_evidence",
        "license_status",
        "sha256",
        "allowed_platforms",
        "runtime_trust_boundary",
        "update_process",
        "provenance_status",
        "reviewer_notes",
    }
)


class ManifestLoadError(ValueError):
    """The manifest could not be loaded as a JSON object."""


class VendorTreeError(RuntimeError):
    """The tracked vendor tree could not be inspected."""


@dataclass(frozen=True)
class TrackedFile:
    path: str
    git_mode: str

    @property
    def executable(self) -> bool:
        return self.git_mode == "100755"

    @property
    def regular(self) -> bool:
        return self.git_mode in {"100644", "100755"}


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestLoadError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestLoadError(f"cannot parse {path}: top-level JSON value must be an object")
    return manifest


def discover_tracked_vendor_files(repo_root: Path) -> list[TrackedFile]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--stage", "-z", "--", VENDOR_ROOT.rstrip("/")],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise VendorTreeError(f"cannot inspect tracked vendor files: {exc}") from exc
    if completed.returncode != 0:
        detail = os.fsdecode(completed.stderr).strip() or "git ls-files failed"
        raise VendorTreeError(detail)

    tracked: list[TrackedFile] = []
    for raw_record in completed.stdout.split(b"\0"):
        if not raw_record:
            continue
        record = os.fsdecode(raw_record)
        try:
            metadata, path = record.split("\t", 1)
            mode, _object_id, stage = metadata.split(" ")
        except ValueError as exc:
            raise VendorTreeError(f"unexpected git ls-files record: {record!r}") from exc
        if stage != "0":
            raise VendorTreeError(f"unmerged vendor path is not valid manifest input: {path}")
        tracked.append(TrackedFile(path=path, git_mode=mode))
    return sorted(tracked, key=lambda item: item.path)


def _missing_fields(value: dict[str, Any], required: frozenset[str]) -> list[str]:
    return sorted(required.difference(value))


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


def _validate_repository_path(path: Any, label: str, errors: list[str]) -> bool:
    if not _is_nonempty_string(path):
        errors.append(f"{label} must be a non-empty repository-relative path")
        return False
    if "\\" in path:
        errors.append(f"{label} must use forward slashes: {path!r}")
        return False

    parsed = PurePosixPath(path)
    normalized = parsed.as_posix()
    if parsed.is_absolute() or path != normalized or any(part in {".", ".."} for part in parsed.parts):
        errors.append(f"{label} is not a normalized repository-relative path: {path!r}")
        return False
    if not path.startswith(VENDOR_ROOT) or path == VENDOR_ROOT.rstrip("/"):
        errors.append(f"{label} points outside {VENDOR_ROOT}: {path!r}")
        return False
    return True


def _validate_top_level(manifest: dict[str, Any], errors: list[str]) -> None:
    for field in _missing_fields(manifest, REQUIRED_TOP_LEVEL_FIELDS):
        errors.append(f"manifest is missing required top-level field: {field}")

    if manifest.get("schema_version") != "1.0.0":
        errors.append("schema_version must be the supported value '1.0.0'")
    if not _is_nonempty_string(manifest.get("maintained_by")):
        errors.append("maintained_by must be a non-empty string")
    if manifest.get("policy_doc") != POLICY_PATH:
        errors.append(f"policy_doc must be {POLICY_PATH!r}")
    if manifest.get("enforcement_status") != "inventory_only_not_enforced":
        errors.append("enforcement_status must remain 'inventory_only_not_enforced' for PR #74")
    _validate_string_list(errors, manifest.get("notes"), "notes", require_nonempty=True)


def _validate_scope(manifest: dict[str, Any], errors: list[str]) -> None:
    scope = manifest.get("manifest_scope")
    if not isinstance(scope, dict):
        errors.append("manifest_scope must be an object")
        return
    for field in _missing_fields(scope, REQUIRED_SCOPE_FIELDS):
        errors.append(f"manifest_scope is missing required field: {field}")

    if scope.get("root") != VENDOR_ROOT:
        errors.append(f"manifest_scope.root must be {VENDOR_ROOT!r}")
    if not _is_nonempty_string(scope.get("coverage")):
        errors.append("manifest_scope.coverage must be a non-empty string")

    files = manifest.get("files")
    expected_count = len(files) if isinstance(files, list) else None
    entry_count = scope.get("entry_count")
    if type(entry_count) is not int:
        errors.append("manifest_scope.entry_count must be an integer")
    elif expected_count is not None and entry_count != expected_count:
        errors.append(
            "manifest_scope.entry_count does not match files length: "
            f"{entry_count} != {expected_count}"
        )

    excluded = scope.get("excluded_paths")
    if not isinstance(excluded, list):
        errors.append("manifest_scope.excluded_paths must be an array")
        return

    excluded_paths: list[str] = []
    for index, record in enumerate(excluded):
        label = f"manifest_scope.excluded_paths[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{label} must be an object")
            continue
        path = record.get("path")
        if _validate_repository_path(path, f"{label}.path", errors):
            excluded_paths.append(path)
        if not _is_nonempty_string(record.get("reason")):
            errors.append(f"{label}.reason must be a non-empty string")

    if excluded_paths != [MANIFEST_PATH]:
        errors.append(
            "manifest_scope.excluded_paths must contain only the manifest control file "
            f"{MANIFEST_PATH!r}"
        )


def _validate_hashing(manifest: dict[str, Any], errors: list[str]) -> None:
    hashing = manifest.get("hashing")
    if not isinstance(hashing, dict):
        errors.append("hashing must be an object")
        return
    for field in _missing_fields(hashing, REQUIRED_HASHING_FIELDS):
        errors.append(f"hashing is missing required field: {field}")
    if hashing.get("algorithm") != "SHA-256":
        errors.append("hashing.algorithm must be 'SHA-256'")
    if hashing.get("input") != "exact_file_bytes":
        errors.append("hashing.input must be 'exact_file_bytes'")
    if hashing.get("status") != "recorded_not_enforced":
        errors.append("hashing.status must remain 'recorded_not_enforced' for PR #74")


def _validate_entry(entry: Any, index: int, errors: list[str]) -> Optional[str]:
    label = f"files[{index}]"
    if not isinstance(entry, dict):
        errors.append(f"{label} must be an object")
        return None

    for field in _missing_fields(entry, REQUIRED_ENTRY_FIELDS):
        errors.append(f"{label} is missing required field: {field}")

    path = entry.get("path")
    valid_path = _validate_repository_path(path, f"{label}.path", errors)

    for field in (
        "file_type",
        "purpose",
        "source_project",
        "license",
        "license_status",
        "runtime_trust_boundary",
        "update_process",
        "provenance_status",
    ):
        if not _is_nonempty_string(entry.get(field)):
            errors.append(f"{label}.{field} must be a non-empty string")

    if type(entry.get("executable")) is not bool:
        errors.append(f"{label}.executable must be a boolean")
    for field in ("upstream_url", "version"):
        value = entry.get(field)
        if value is not None and not _is_nonempty_string(value):
            errors.append(f"{label}.{field} must be null or a non-empty string")
    for field in ("version_evidence", "license_evidence", "reviewer_notes"):
        _validate_string_list(errors, entry.get(field), f"{label}.{field}")
    _validate_string_list(
        errors, entry.get("allowed_platforms"), f"{label}.allowed_platforms", require_nonempty=True
    )

    sha256 = entry.get("sha256")
    if not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None:
        errors.append(f"{label}.sha256 must be exactly 64 lowercase hexadecimal characters")
    return path if valid_path else None


def _worktree_executable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _validate_coverage_and_modes(
    manifest: dict[str, Any],
    tracked_files: list[TrackedFile],
    repo_root: Path,
    entry_paths: list[str],
    errors: list[str],
) -> None:
    tracked_by_path = {item.path: item for item in tracked_files}
    manifest_control = tracked_by_path.get(MANIFEST_PATH)
    if manifest_control is None:
        errors.append(f"manifest control file is not tracked: {MANIFEST_PATH}")
    elif not manifest_control.regular:
        errors.append(f"manifest control file must be a regular Git file: {MANIFEST_PATH}")

    if MANIFEST_PATH in entry_paths:
        errors.append(f"manifest control file must be excluded from files entries: {MANIFEST_PATH}")

    expected_paths = set(tracked_by_path).difference({MANIFEST_PATH})
    actual_paths = set(entry_paths)
    for path in sorted(expected_paths.difference(actual_paths)):
        errors.append(f"tracked vendor file is missing a manifest entry: {path}")
    for path in sorted(actual_paths.difference(expected_paths)):
        errors.append(f"manifest entry is not a tracked non-manifest vendor file: {path}")

    entries = manifest.get("files")
    entries_by_path = {}
    if isinstance(entries, list):
        entries_by_path = {
            entry.get("path"): entry
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        }

    for path in sorted(expected_paths):
        tracked = tracked_by_path[path]
        if not tracked.regular:
            errors.append(f"tracked vendor path is not a regular Git file: {path} ({tracked.git_mode})")
            continue

        worktree_path = repo_root.joinpath(*PurePosixPath(path).parts)
        if worktree_path.is_symlink():
            errors.append(f"tracked vendor path must not be a symlink: {path}")
            continue
        if not worktree_path.is_file():
            errors.append(f"tracked vendor file is missing from the working tree: {path}")
            continue

        worktree_executable = _worktree_executable(worktree_path)
        if worktree_executable != tracked.executable:
            errors.append(
                f"working-tree executable mode differs from Git mode for {path}: "
                f"working tree={worktree_executable}, git={tracked.executable}"
            )

        entry = entries_by_path.get(path)
        if isinstance(entry, dict) and type(entry.get("executable")) is bool:
            declared = entry["executable"]
            if declared != tracked.executable:
                errors.append(
                    f"manifest executable does not match Git mode for {path}: "
                    f"manifest={declared}, git={tracked.executable}"
                )
            if declared != worktree_executable:
                errors.append(
                    f"manifest executable does not match working-tree mode for {path}: "
                    f"manifest={declared}, working tree={worktree_executable}"
                )


def validate_manifest(
    manifest: dict[str, Any], tracked_files: list[TrackedFile], repo_root: Path
) -> list[str]:
    """Return deterministic structural and source-tree consistency errors."""

    errors: list[str] = []
    _validate_top_level(manifest, errors)
    _validate_scope(manifest, errors)
    _validate_hashing(manifest, errors)

    files = manifest.get("files")
    entry_paths: list[str] = []
    if not isinstance(files, list):
        errors.append("files must be an array")
    else:
        for index, entry in enumerate(files):
            path = _validate_entry(entry, index, errors)
            if path is not None:
                entry_paths.append(path)

    duplicate_paths = sorted(path for path, count in Counter(entry_paths).items() if count > 1)
    for path in duplicate_paths:
        errors.append(f"manifest path appears more than once: {path}")
    if entry_paths != sorted(entry_paths):
        errors.append("manifest files entries must be sorted lexicographically by path")

    _validate_coverage_and_modes(manifest, tracked_files, repo_root, entry_paths, errors)
    return errors


def _component_properties(entry: dict[str, Any]) -> list[dict[str, str]]:
    values = {
        "vrhotspot:allowed-platforms": json.dumps(
            entry["allowed_platforms"], ensure_ascii=False, separators=(",", ":")
        ),
        "vrhotspot:executable": str(entry["executable"]).lower(),
        "vrhotspot:file-type": entry["file_type"],
        "vrhotspot:license-status": entry["license_status"],
        "vrhotspot:provenance-status": entry["provenance_status"],
        "vrhotspot:runtime-trust-boundary": entry["runtime_trust_boundary"],
        "vrhotspot:sha256-verification": "declared-syntax-only-not-compared",
        "vrhotspot:source-project": entry["source_project"],
    }
    return [{"name": name, "value": values[name]} for name in sorted(values)]


def generate_sbom(manifest: dict[str, Any]) -> dict[str, Any]:
    """Generate deterministic CycloneDX JSON data covering only backend/vendor."""

    components: list[dict[str, Any]] = []
    for entry in sorted(manifest["files"], key=lambda item: item["path"]):
        component: dict[str, Any] = {
            "bom-ref": entry["path"],
            "description": entry["purpose"],
            "hashes": [{"alg": "SHA-256", "content": entry["sha256"]}],
            "licenses": [{"license": {"name": entry["license"]}}],
            "name": entry["path"],
            "properties": _component_properties(entry),
            "type": "file",
        }
        if entry["upstream_url"] is not None:
            component["externalReferences"] = [
                {"type": "website", "url": entry["upstream_url"]}
            ]
        if entry["version"] is not None:
            component["version"] = entry["version"]
        components.append(component)

    component_refs = [component["bom-ref"] for component in components]
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "components": components,
        "dependencies": [
            {
                "dependsOn": component_refs,
                "ref": "vrhotspot:backend/vendor-inventory",
            }
        ],
        "metadata": {
            "component": {
                "bom-ref": "vrhotspot:backend/vendor-inventory",
                "name": "VRhotspot backend/vendor inventory",
                "properties": [
                    {
                        "name": "vrhotspot:coverage",
                        "value": "backend/vendor only; not a full-project SBOM",
                    },
                    {
                        "name": "vrhotspot:manifest-schema-version",
                        "value": manifest["schema_version"],
                    },
                    {"name": "vrhotspot:source-manifest", "value": MANIFEST_PATH},
                    {
                        "name": "vrhotspot:sha256-verification",
                        "value": "syntax-only; payload bytes not compared",
                    },
                ],
                "type": "data",
            }
        },
        "specVersion": "1.6",
        "version": 1,
    }


def render_sbom(manifest: dict[str, Any]) -> str:
    return json.dumps(generate_sbom(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sbom-output",
        type=Path,
        default=DEFAULT_SBOM_PATH,
        help=f"deterministic generated SBOM path (default: {DEFAULT_SBOM_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    manifest_file = REPO_ROOT / MANIFEST_PATH
    try:
        manifest = load_manifest(manifest_file)
        tracked_files = discover_tracked_vendor_files(REPO_ROOT)
    except (ManifestLoadError, VendorTreeError) as exc:
        print(f"vendor manifest validation failed: {exc}", file=sys.stderr)
        return 1

    errors = validate_manifest(manifest, tracked_files, REPO_ROOT)
    if errors:
        print("vendor manifest validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    sbom_text = render_sbom(manifest)
    try:
        args.sbom_output.write_text(sbom_text, encoding="utf-8")
    except OSError as exc:
        print(f"could not write deterministic vendor SBOM to {args.sbom_output}: {exc}", file=sys.stderr)
        return 1

    vendor_file_count = len(manifest["files"])
    print(f"vendor manifest validation passed: {vendor_file_count} tracked files covered")
    print(
        f"deterministic vendor-only SBOM written: {args.sbom_output} "
        f"({vendor_file_count} file components)"
    )
    print("SHA-256 validation: syntax only; payload bytes were not compared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
