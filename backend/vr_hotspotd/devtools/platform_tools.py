"""Platform-Tools pin loading, adb discovery, and tools status."""

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
    "VRhotspot can install the reviewed Android Platform-Tools pin into writable host state.",
    "A system adb remains supported; a verified managed install is preferred when present.",
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

    maximum = pin.get("max_archive_bytes")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        errors.append("max_archive_bytes must be a positive integer")

    allowlist = pin.get("extract_allowlist")
    if (
        not isinstance(allowlist, list)
        or not allowlist
        or not all(_is_nonempty_string(entry) for entry in allowlist)
    ):
        errors.append("extract_allowlist must be a non-empty array of non-empty strings")

    if not _is_nonempty_string(pin.get("license_name")):
        errors.append("license_name must be a non-empty string")
    terms_url = pin.get("license_terms_url")
    if not _is_nonempty_string(terms_url):
        errors.append("license_terms_url must be a non-empty string")
    else:
        parsed_terms = urlsplit(terms_url)
        if parsed_terms.scheme != "https" or parsed_terms.hostname != "developer.android.com":
            errors.append("license_terms_url must use developer.android.com over https")

    return errors


def load_platform_tools_pin(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and validate the reviewed pin metadata."""

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
    """The placeholder hash blocks implementation regardless of the flag."""

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
    """Host name of the pinned archive URL; the full download URL is not reported."""

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


def _managed_manifest_state(managed_path: str) -> Dict[str, Any]:
    managed = Path(managed_path)
    root = managed.parent.parent
    manifest = root / "manifest.json"
    if not manifest.exists() and not manifest.is_symlink():
        return {"verified": None, "version": None, "error": None}
    try:
        from vr_hotspotd.devtools.platform_tools_manager import (
            inspect_managed_platform_tools,
        )

        inspected = inspect_managed_platform_tools(root)
    except Exception:
        return {"verified": False, "version": None, "error": "manifest_invalid"}
    return {
        "verified": inspected.get("verified"),
        "version": inspected.get("version"),
        "error": inspected.get("error"),
    }


def discover_adb(
    *,
    managed_path: str = MANAGED_ADB_PATH,
    which=shutil.which,
) -> Dict[str, Any]:
    """Discover a verified managed adb first, then a system adb."""

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

    managed_state = _managed_manifest_state(managed_path)
    if managed_state["verified"] is False:
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
            "verified": managed_state["verified"],
            "version": managed_state["version"],
            "error": managed_state["error"],
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
    """Pure tools status model shared by API, CLI, UI, and support bundle."""

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
            "license_name": pin.get("license_name"),
            "license_terms_url": pin.get("license_terms_url"),
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
            "implementation_blocked": True,
            "blocked_reason": BLOCKED_REASON_PIN_UNAVAILABLE,
            "license_name": None,
            "license_terms_url": None,
            "error": pin_error or "pin unavailable",
        }
        arch_supported = None
        warnings.append("platform_tools_pin_unavailable")

    managed = discovery.get("managed")
    if isinstance(managed, Mapping) and managed.get("present") and managed.get("verified") is False:
        warnings.append("managed_tools_verification_failed")

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
    """Gather the Dev Bridge tools status. Never raises."""

    pin: Optional[Dict[str, Any]] = None
    pin_error: Optional[str] = None
    try:
        pin = load_platform_tools_pin()
    except PinError as exc:
        pin_error = str(exc)
    except Exception as exc:
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
                "version": None,
                "error": None,
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
