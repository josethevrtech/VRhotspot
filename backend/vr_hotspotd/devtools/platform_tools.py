"""Read-only Platform-Tools pin loading, adb discovery, and tools status.

This module reads the reviewed pin metadata shipped with the package,
discovers whether a VRhotspot-managed or system ``adb`` exists on the host,
and builds the Dev Bridge tools status model.

Hard boundaries, enforced by construction:

- No network access of any kind. The pinned URL is parsed for its host name
  only and is never fetched.
- ``adb`` is never executed; discovery is filesystem/PATH inspection only.
- Nothing is installed, removed, or modified anywhere on the host.
- While the pin's ``archive_sha256`` is the review placeholder, the status
  always reports ``implementation_blocked`` true regardless of the flag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit


TOOLS_STATUS_SCHEMA_VERSION = 1

PIN_FILENAME = "platform_tools_pin.json"
BLOCKED_SHA256_PLACEHOLDER = (
    "BLOCKED-PLACEHOLDER-DO-NOT-IMPLEMENT-DOWNLOADER-SHA256-NOT-INDEPENDENTLY-VERIFIED"
)
PIN_URL_ALLOWED_HOST = "dl.google.com"

DEVTOOLS_ROOT = "/var/lib/vr-hotspot/devtools"
MANAGED_ADB_PATH = DEVTOOLS_ROOT + "/platform-tools/adb"

ADB_SOURCE_MANAGED = "managed"
ADB_SOURCE_SYSTEM = "system"
ADB_SOURCE_MISSING = "missing"

BLOCKED_REASON_PLACEHOLDER_SHA256 = "archive_sha256_placeholder"
BLOCKED_REASON_PIN_FLAG = "implementation_blocked_by_pin"
BLOCKED_REASON_PIN_UNAVAILABLE = "pin_unavailable"

_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
}

_TOOLS_STATUS_NOTES = (
    "Dev Bridge tools status is read-only: nothing is downloaded, installed, "
    "removed, or executed.",
    "The Platform-Tools pin is reviewed metadata only; downloader/installer "
    "code is intentionally not implemented while implementation_blocked is true.",
)


class PinError(ValueError):
    """The Platform-Tools pin metadata is missing, unreadable, or invalid."""


def default_pin_path() -> Path:
    """Path of the pin file shipped inside this package."""

    return Path(__file__).resolve().parent / PIN_FILENAME


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_pin(pin: Mapping[str, Any]) -> List[str]:
    """Runtime schema validation; a subset of tools/ci/platform_tools_pin_check.py."""

    errors: List[str] = []
    if pin.get("schema_version") != 1:
        errors.append("schema_version must be the supported value 1")
    if not _is_nonempty_string(pin.get("version")):
        errors.append("version must be a non-empty string")

    url = pin.get("url")
    if not _is_nonempty_string(url):
        errors.append("url must be a non-empty string")
    else:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            errors.append("url must use https")
        if parsed.netloc != PIN_URL_ALLOWED_HOST:
            errors.append(f"url host must be exactly {PIN_URL_ALLOWED_HOST!r}")

    sha256 = pin.get("archive_sha256")
    if not isinstance(sha256, str):
        errors.append("archive_sha256 must be a string")
    elif sha256 != BLOCKED_SHA256_PLACEHOLDER and not (
        len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256)
    ):
        errors.append(
            "archive_sha256 must be 64 lowercase hex characters or the blocked placeholder"
        )

    if not isinstance(pin.get("implementation_blocked"), bool):
        errors.append("implementation_blocked must be a boolean")
    if not _is_nonempty_string(pin.get("arch")):
        errors.append("arch must be a non-empty string")

    allowlist = pin.get("extract_allowlist")
    if (
        not isinstance(allowlist, list)
        or not allowlist
        or not all(_is_nonempty_string(entry) for entry in allowlist)
    ):
        errors.append("extract_allowlist must be a non-empty array of non-empty strings")

    return errors


def load_platform_tools_pin(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and validate the pin metadata. Raises PinError; never touches the network."""

    pin_path = Path(path) if path is not None else default_pin_path()
    try:
        raw = pin_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PinError(f"cannot read Platform-Tools pin {pin_path}: {exc}") from exc
    try:
        pin = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PinError(f"cannot parse Platform-Tools pin {pin_path}: {exc}") from exc
    if not isinstance(pin, dict):
        raise PinError(
            f"cannot parse Platform-Tools pin {pin_path}: top-level value must be an object"
        )
    errors = _validate_pin(pin)
    if errors:
        raise PinError(
            f"invalid Platform-Tools pin {pin_path}: " + "; ".join(errors)
        )
    return pin


def pin_is_blocked(pin: Mapping[str, Any]) -> bool:
    """Fail safe: the placeholder hash blocks implementation regardless of the flag."""

    if pin.get("archive_sha256") == BLOCKED_SHA256_PLACEHOLDER:
        return True
    return bool(pin.get("implementation_blocked"))


def pin_blocked_reason(pin: Mapping[str, Any]) -> Optional[str]:
    if pin.get("archive_sha256") == BLOCKED_SHA256_PLACEHOLDER:
        return BLOCKED_REASON_PLACEHOLDER_SHA256
    if pin.get("implementation_blocked"):
        return BLOCKED_REASON_PIN_FLAG
    return None


def pin_url_host(pin: Mapping[str, Any]) -> Optional[str]:
    """Host name of the pinned URL only; the full URL is never reported."""

    url = pin.get("url")
    if not _is_nonempty_string(url):
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def normalize_arch(machine: Optional[str]) -> Optional[str]:
    if not _is_nonempty_string(machine):
        return None
    normalized = machine.strip().lower()
    return _ARCH_ALIASES.get(normalized, normalized)


def discover_adb(
    *,
    managed_path: str = MANAGED_ADB_PATH,
    which=shutil.which,
) -> Dict[str, Any]:
    """Filesystem/PATH discovery of adb. Never executes anything."""

    managed = Path(managed_path)
    try:
        managed_present = managed.exists() or managed.is_symlink()
        managed_installed = (
            managed.is_file()
            and not managed.is_symlink()
            and os.access(managed_path, os.X_OK)
        )
    except OSError:
        managed_present = False
        managed_installed = False

    try:
        system_path = which("adb")
    except Exception:
        system_path = None

    if managed_installed:
        source = ADB_SOURCE_MANAGED
        effective_path: Optional[str] = managed_path
    elif system_path:
        source = ADB_SOURCE_SYSTEM
        effective_path = system_path
    else:
        source = ADB_SOURCE_MISSING
        effective_path = None

    return {
        "managed": {
            "path": managed_path,
            "present": managed_present,
            "installed": managed_installed,
            # No managed install exists in the status foundation, so there is
            # no installed-tools ledger to verify bytes against yet.
            "verified": None,
        },
        "system": {
            "present": bool(system_path),
            "path": system_path,
        },
        "source": source,
        "path": effective_path,
    }


def build_devbridge_tools_status(
    *,
    pin: Optional[Mapping[str, Any]],
    pin_error: Optional[str],
    discovery: Mapping[str, Any],
    host_machine: Optional[str],
) -> Dict[str, Any]:
    """Pure read-only tools status model shared by API, CLI, and support bundle."""

    warnings: List[str] = []
    host_arch = normalize_arch(host_machine)

    if pin is not None:
        pin_view: Dict[str, Any] = {
            "available": True,
            "version": pin.get("version"),
            "url_host": pin_url_host(pin),
            "arch": pin.get("arch"),
            "implementation_blocked": pin_is_blocked(pin),
            "blocked_reason": pin_blocked_reason(pin),
            "error": None,
        }
        arch_supported: Optional[bool] = host_arch == normalize_arch(pin.get("arch"))
        if not arch_supported:
            warnings.append("unsupported_arch")
    else:
        pin_view = {
            "available": False,
            "version": None,
            "url_host": None,
            "arch": None,
            # Fail safe: without a readable pin nothing may be implemented.
            "implementation_blocked": True,
            "blocked_reason": BLOCKED_REASON_PIN_UNAVAILABLE,
            "error": pin_error or "pin unavailable",
        }
        arch_supported = None
        warnings.append("platform_tools_pin_unavailable")

    return {
        "schema_version": TOOLS_STATUS_SCHEMA_VERSION,
        "mode": "read_only",
        "platform_tools_pin": pin_view,
        "host": {
            "machine": host_machine,
            "arch": host_arch,
            "arch_supported": arch_supported,
        },
        "adb": dict(discovery),
        "warnings": warnings,
        "notes": list(_TOOLS_STATUS_NOTES),
    }


def collect_devbridge_tools_status() -> Dict[str, Any]:
    """Gather the read-only Dev Bridge tools status. Never raises."""

    pin: Optional[Dict[str, Any]] = None
    pin_error: Optional[str] = None
    try:
        pin = load_platform_tools_pin()
    except PinError as exc:
        pin_error = str(exc)
    except Exception as exc:  # defensive: status must never fail the caller
        pin_error = f"unexpected pin load failure: {type(exc).__name__}"

    try:
        discovery = discover_adb()
    except Exception:
        discovery = {
            "managed": {
                "path": MANAGED_ADB_PATH,
                "present": False,
                "installed": False,
                "verified": None,
            },
            "system": {"present": False, "path": None},
            "source": ADB_SOURCE_MISSING,
            "path": None,
        }

    try:
        host_machine: Optional[str] = platform.machine() or None
    except Exception:
        host_machine = None

    return build_devbridge_tools_status(
        pin=pin,
        pin_error=pin_error,
        discovery=discovery,
        host_machine=host_machine,
    )
