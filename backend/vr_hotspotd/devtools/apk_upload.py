"""Authenticated streaming APK uploads for the Developer Hub."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Set
from urllib.parse import unquote

from vr_hotspotd.devtools.adb_operations import (
    APK_MAX_BYTES,
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_OUTPUT_LIMIT,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
    execute_adb_operation,
)


log = logging.getLogger("vr_hotspotd.devhub_apk_upload")

APK_UPLOAD_PATH = "/v1/devbridge/adb/install-upload"
APK_UPLOAD_DIR = Path("/var/lib/vr-hotspot/uploads")
APK_UPLOAD_CHUNK_BYTES = 1024 * 1024
APK_UPLOAD_CONTENT_TYPES = {
    "application/octet-stream",
    "application/vnd.android.package-archive",
}

_RESULT_HTTP_STATUS = {
    RESULT_OK: 200,
    RESULT_INVALID_REQUEST: 400,
    RESULT_TOOLS_UNAVAILABLE: 503,
    RESULT_TIMEOUT: 504,
    RESULT_OUTPUT_LIMIT: 502,
    RESULT_FAILED: 409,
}


def _respond_invalid(handler: Any, cid: str, warning: str, *, status: int = 400) -> None:
    handler._respond(
        status,
        handler._envelope(
            correlation_id=cid,
            result_code=RESULT_INVALID_REQUEST,
            warnings=[warning],
            data={},
        ),
    )


def _header_bool(handler: Any, name: str, default: bool) -> bool:
    value = (handler.headers.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "y"}


def _package_inventory(serial: str) -> Optional[Set[str]]:
    """Return the current third-party package set when ADB can report it."""

    result = execute_adb_operation(
        "packages",
        {
            "serial": serial,
            "third_party_only": True,
        },
    )
    if not result.get("success"):
        return None
    raw_data = result.get("data")
    if not isinstance(raw_data, Mapping):
        return None
    packages = raw_data.get("packages")
    if not isinstance(packages, list):
        return None
    return {value for value in packages if isinstance(value, str) and value}


def _deployment_details(
    *,
    install_succeeded: bool,
    before_packages: Optional[Set[str]],
    after_packages: Optional[Set[str]],
) -> dict[str, Any]:
    if not install_succeeded:
        return {}
    if before_packages is None or after_packages is None:
        return {"deployment_action": "installed_or_updated"}

    added = sorted(after_packages - before_packages)
    if added:
        details: dict[str, Any] = {"deployment_action": "installed"}
        if len(added) == 1:
            details["deployment_package"] = added[0]
        return details
    return {"deployment_action": "updated"}


def _public_result(
    result: Mapping[str, Any],
    *,
    apk_name: str,
    apk_size_bytes: int,
    before_packages: Optional[Set[str]],
    after_packages: Optional[Set[str]],
) -> dict[str, Any]:
    public = dict(result)
    raw_data = public.get("data")
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    data.pop("apk_path", None)
    data["apk_name"] = apk_name
    data["apk_size_bytes"] = apk_size_bytes
    data.update(
        _deployment_details(
            install_succeeded=bool(public.get("success")),
            before_packages=before_packages,
            after_packages=after_packages,
        )
    )
    public["data"] = data
    return public


def handle_apk_upload(handler: Any, cid: str) -> None:
    """Stream one authenticated APK body to disk, install it, then remove it."""

    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        _respond_invalid(handler, cid, "apk_upload_empty")
        return
    if length > APK_MAX_BYTES:
        _respond_invalid(handler, cid, "body_too_large", status=413)
        return

    content_type = (
        (handler.headers.get("Content-Type") or "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    if content_type not in APK_UPLOAD_CONTENT_TYPES:
        _respond_invalid(handler, cid, "apk_upload_content_type_invalid")
        return

    serial = (handler.headers.get("X-VRhotspot-Serial") or "").strip()
    raw_name = unquote((handler.headers.get("X-VRhotspot-Apk-Name") or "").strip())
    apk_name = Path(raw_name).name
    if not serial:
        _respond_invalid(handler, cid, "apk_upload_serial_missing")
        return
    if not apk_name or not apk_name.lower().endswith(".apk"):
        _respond_invalid(handler, cid, "apk_upload_filename_invalid")
        return

    reinstall = _header_bool(handler, "X-VRhotspot-Reinstall", True)
    grant_permissions = _header_bool(
        handler,
        "X-VRhotspot-Grant-Permissions",
        False,
    )

    try:
        APK_UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(APK_UPLOAD_DIR, 0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".apk-upload-",
            suffix=".apk",
            dir=APK_UPLOAD_DIR,
        )
    except OSError:
        handler._respond(
            500,
            handler._envelope(
                correlation_id=cid,
                result_code=RESULT_FAILED,
                warnings=["apk_upload_staging_failed"],
                data={},
            ),
        )
        return

    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        remaining = length
        with os.fdopen(fd, "wb") as upload:
            while remaining:
                chunk = handler.rfile.read(min(APK_UPLOAD_CHUNK_BYTES, remaining))
                if not chunk:
                    raise OSError(
                        "APK upload ended before Content-Length bytes were received"
                    )
                upload.write(chunk)
                remaining -= len(chunk)
            upload.flush()
            os.fsync(upload.fileno())

        before_packages = _package_inventory(serial)
        result = execute_adb_operation(
            "install",
            {
                "serial": serial,
                "apk_path": str(temporary_path),
                "reinstall": reinstall,
                "grant_permissions": grant_permissions,
            },
        )
        after_packages = _package_inventory(serial) if result.get("success") else None
        public_result = _public_result(
            result,
            apk_name=apk_name,
            apk_size_bytes=length,
            before_packages=before_packages,
            after_packages=after_packages,
        )
        result_code = str(public_result.get("result_code") or RESULT_FAILED)
        handler._respond(
            _RESULT_HTTP_STATUS.get(result_code, 500),
            handler._envelope(
                correlation_id=cid,
                result_code=result_code,
                data=public_result,
                warnings=[],
            ),
        )
    except OSError:
        _respond_invalid(handler, cid, "apk_upload_read_failed")
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning(
                "temporary APK cleanup failed",
                extra={"correlation_id": cid, "path": str(temporary_path)},
            )
