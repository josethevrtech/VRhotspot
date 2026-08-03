"""Authenticated Developer Hub routes for assets, tools, and ADB operations."""

from __future__ import annotations

from http.cookies import SimpleCookie
import json
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Mapping, Optional

from vr_hotspotd.api import APIHandler, _resolve_asset_path
from vr_hotspotd.devtools.adb_operations import (
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_OUTPUT_LIMIT,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
    execute_adb_operation,
)
from vr_hotspotd.devtools.apk_upload import APK_UPLOAD_PATH, handle_apk_upload
from vr_hotspotd.devtools.device_overview import collect_device_overview
from vr_hotspotd.devtools.platform_tools_manager import (
    RESULT_ARCHIVE_INVALID,
    RESULT_ARCHIVE_TOO_LARGE,
    RESULT_CHECKSUM_MISMATCH,
    RESULT_DOWNLOAD_FAILED,
    RESULT_IMPLEMENTATION_BLOCKED,
    RESULT_INSTALL_BUSY,
    RESULT_INSTALL_FAILED,
    RESULT_LICENSE_REQUIRED,
    RESULT_MANIFEST_INVALID,
    RESULT_PIN_UNAVAILABLE,
    RESULT_REMOVE_FAILED,
    RESULT_UNSUPPORTED_ARCH,
    install_managed_platform_tools,
    remove_managed_platform_tools,
)
from vr_hotspotd.devtools.wireless_bootstrap import enable_wireless_adb


log = logging.getLogger("vr_hotspotd.devhub_api")

DEVICE_OVERVIEW_PATH = "/v1/devbridge/adb/device-overview"
WIRELESS_BOOTSTRAP_PATH = "/v1/devbridge/adb/enable-wireless"
BROWSER_SESSION_PATH = "/v1/auth/browser-session"
BROWSER_SESSION_LOGOUT_PATH = "/v1/auth/browser-session/logout"
_BROWSER_SESSION_COOKIE = "vrhs_browser_session"
_BROWSER_SESSION_TTL_S = 8 * 60 * 60
_BROWSER_SESSIONS: dict[str, float] = {}
_BROWSER_SESSIONS_LOCK = threading.Lock()

_DEVHUB_ASSET_TYPES = {
    "/assets/basic_layout.css": "text/css; charset=utf-8",
    "/assets/basic_guided.css": "text/css; charset=utf-8",
    "/assets/basic_guided.js": "application/javascript; charset=utf-8",
    "/assets/devhub.css": "text/css; charset=utf-8",
    "/assets/devhub.js": "application/javascript; charset=utf-8",
    "/assets/devhub_upload.css": "text/css; charset=utf-8",
    "/assets/devhub_upload.js": "application/javascript; charset=utf-8",
    "/assets/browser_session.js": "application/javascript; charset=utf-8",
    "/assets/pro_guided_workflow.js": "application/javascript; charset=utf-8",
    "/assets/pro_guided_workflow.css": "text/css; charset=utf-8",
    "/assets/devhub_workspace.css": "text/css; charset=utf-8",
    "/assets/devhub_workspace.js": "application/javascript; charset=utf-8",
    "/assets/devhub_device_overview.css": "text/css; charset=utf-8",
    "/assets/devhub_device_overview.js": "application/javascript; charset=utf-8",
    "/assets/devhub_device_identity.css": "text/css; charset=utf-8",
    "/assets/devhub_device_identity.js": "application/javascript; charset=utf-8",
    "/assets/devhub_connection_wizard.css": "text/css; charset=utf-8",
    "/assets/devhub_connection_wizard.js": "application/javascript; charset=utf-8",
}

_GET_OPERATIONS = {
    "/v1/devbridge/adb/version": "version",
    "/v1/devbridge/adb/devices": "devices",
    "/v1/devbridge/adb/packages": "packages",
}

_POST_OPERATIONS = {
    "/v1/devbridge/adb/pair": "pair",
    "/v1/devbridge/adb/connect": "connect",
    "/v1/devbridge/adb/disconnect": "disconnect",
    "/v1/devbridge/adb/install": "install",
    "/v1/devbridge/adb/launch": "launch",
    "/v1/devbridge/adb/stop": "stop",
    "/v1/devbridge/adb/clear-data": "clear_data",
    "/v1/devbridge/adb/uninstall": "uninstall",
}

_TOOLS_POST_OPERATIONS = {
    "/v1/devbridge/tools/install": "install",
    "/v1/devbridge/tools/remove": "remove",
}

_RESULT_HTTP_STATUS = {
    RESULT_OK: 200,
    RESULT_INVALID_REQUEST: 400,
    RESULT_TOOLS_UNAVAILABLE: 503,
    RESULT_TIMEOUT: 504,
    RESULT_OUTPUT_LIMIT: 502,
    RESULT_FAILED: 409,
}

_TOOLS_RESULT_HTTP_STATUS = {
    RESULT_OK: 200,
    RESULT_LICENSE_REQUIRED: 400,
    RESULT_PIN_UNAVAILABLE: 503,
    RESULT_IMPLEMENTATION_BLOCKED: 503,
    RESULT_UNSUPPORTED_ARCH: 422,
    RESULT_INSTALL_BUSY: 409,
    RESULT_DOWNLOAD_FAILED: 502,
    RESULT_ARCHIVE_TOO_LARGE: 502,
    RESULT_CHECKSUM_MISMATCH: 502,
    RESULT_ARCHIVE_INVALID: 502,
    RESULT_MANIFEST_INVALID: 409,
    RESULT_INSTALL_FAILED: 500,
    RESULT_REMOVE_FAILED: 500,
}

_BODY_ERROR_WARNINGS = {
    "body_too_large",
    "body_read_failed",
    "body_not_object",
    "body_json_parse_failed",
}


def _prune_browser_sessions(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [
        session_id
        for session_id, expires_at in _BROWSER_SESSIONS.items()
        if expires_at <= current
    ]
    for session_id in expired:
        _BROWSER_SESSIONS.pop(session_id, None)


def _create_browser_session() -> str:
    session_id = secrets.token_urlsafe(32)
    with _BROWSER_SESSIONS_LOCK:
        _prune_browser_sessions()
        _BROWSER_SESSIONS[session_id] = time.monotonic() + _BROWSER_SESSION_TTL_S
    return session_id


def _browser_session_is_valid(session_id: str) -> bool:
    if not session_id:
        return False
    with _BROWSER_SESSIONS_LOCK:
        _prune_browser_sessions()
        expires_at = _BROWSER_SESSIONS.get(session_id)
        if expires_at is None:
            return False
        _BROWSER_SESSIONS[session_id] = time.monotonic() + _BROWSER_SESSION_TTL_S
        return True


def _revoke_browser_session(session_id: str) -> None:
    if not session_id:
        return
    with _BROWSER_SESSIONS_LOCK:
        _BROWSER_SESSIONS.pop(session_id, None)


class DevHubAPIHandler(APIHandler):
    """Extend the daemon API with Developer Hub assets and operations."""

    def _browser_session_id(self) -> str:
        raw_cookie = self.headers.get("Cookie") or ""
        if not raw_cookie:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return ""
        morsel = cookie.get(_BROWSER_SESSION_COOKIE)
        return morsel.value.strip() if morsel else ""

    def _is_authorized(self) -> bool:
        return super()._is_authorized() or _browser_session_is_valid(
            self._browser_session_id()
        )

    def _respond_browser_session(
        self,
        *,
        cid: str,
        code: int,
        result_code: str,
        session_id: str = "",
        clear: bool = False,
    ) -> None:
        payload = self._envelope(
            correlation_id=cid,
            result_code=result_code,
            data={"authenticated": code == 200 and not clear},
        )
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self._send_common_headers("application/json; charset=utf-8", len(raw))
        if clear:
            self.send_header(
                "Set-Cookie",
                f"{_BROWSER_SESSION_COOKIE}=; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=0",
            )
        elif session_id:
            self.send_header(
                "Set-Cookie",
                f"{_BROWSER_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; "
                f"SameSite=Strict; Max-Age={_BROWSER_SESSION_TTL_S}",
            )
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_devhub_asset(self, path: str) -> bool:
        content_type = _DEVHUB_ASSET_TYPES.get(path)
        if content_type is None:
            return False
        asset_name = path.rsplit("/", 1)[-1]
        asset_path = _resolve_asset_path(asset_name)
        if not asset_path:
            self._respond_raw(404, b"Not Found", "text/plain; charset=utf-8")
            return True
        try:
            payload = Path(asset_path).read_bytes()
        except OSError:
            self._respond_raw(404, b"Not Found", "text/plain; charset=utf-8")
            return True
        self._respond_raw(200, payload, content_type)
        return True

    def _respond_adb_operation(
        self,
        *,
        cid: str,
        operation: str,
        request: Optional[Mapping[str, Any]] = None,
        warnings: Optional[list[str]] = None,
    ) -> None:
        result = execute_adb_operation(operation, request)
        result_code = str(result.get("result_code") or RESULT_FAILED)
        status = _RESULT_HTTP_STATUS.get(result_code, 500)
        self._respond(
            status,
            self._envelope(
                correlation_id=cid,
                result_code=result_code,
                data=result,
                warnings=list(warnings or []),
            ),
        )

    def _respond_device_overview(self, *, cid: str, serial: Any) -> None:
        result = collect_device_overview(serial)
        result_code = str(result.get("result_code") or RESULT_FAILED)
        status = _RESULT_HTTP_STATUS.get(result_code, 500)
        self._respond(
            status,
            self._envelope(
                correlation_id=cid,
                result_code=result_code,
                data=result,
                warnings=[],
            ),
        )

    def _respond_wireless_bootstrap(
        self,
        *,
        cid: str,
        request: Mapping[str, Any],
        warnings: Optional[list[str]] = None,
    ) -> None:
        result = enable_wireless_adb(
            request.get("serial"),
            request.get("port", 5555),
        )
        result_code = str(result.get("result_code") or RESULT_FAILED)
        status = _RESULT_HTTP_STATUS.get(result_code, 500)
        self._respond(
            status,
            self._envelope(
                correlation_id=cid,
                result_code=result_code,
                data=result,
                warnings=list(warnings or []),
            ),
        )

    def _respond_tools_operation(
        self,
        *,
        cid: str,
        operation: str,
        request: Mapping[str, Any],
        warnings: Optional[list[str]] = None,
    ) -> None:
        if operation == "install":
            result = install_managed_platform_tools(
                license_accepted=request.get("license_accepted")
            )
        else:
            result = remove_managed_platform_tools()
        result_code = str(result.get("result_code") or RESULT_INSTALL_FAILED)
        status = _TOOLS_RESULT_HTTP_STATUS.get(result_code, 500)
        self._respond(
            status,
            self._envelope(
                correlation_id=cid,
                result_code=result_code,
                data=result,
                warnings=list(warnings or []),
            ),
        )

    def _respond_invalid_body(self, cid: str, warnings: list[str]) -> None:
        status = 413 if "body_too_large" in warnings else 400
        self._respond(
            status,
            self._envelope(
                correlation_id=cid,
                result_code=RESULT_INVALID_REQUEST,
                warnings=warnings,
                data={},
            ),
        )

    def do_GET(self):
        cid = self._cid()
        path, qs = self._parse_url()
        if self._serve_devhub_asset(path):
            return
        if path == BROWSER_SESSION_PATH:
            if _browser_session_is_valid(self._browser_session_id()):
                self._respond_browser_session(
                    cid=cid,
                    code=200,
                    result_code="browser_session_active",
                )
            else:
                self._respond_browser_session(
                    cid=cid,
                    code=401,
                    result_code="browser_session_missing",
                    clear=True,
                )
            return
        operation = _GET_OPERATIONS.get(path)
        is_device_overview = path == DEVICE_OVERVIEW_PATH
        if operation is None and not is_device_overview:
            super().do_GET()
            return

        log.info(
            "request",
            extra={"correlation_id": cid, "method": "GET", "path": self.path},
        )
        if not self._require_auth(cid):
            return

        if is_device_overview:
            self._respond_device_overview(cid=cid, serial=qs.get("serial"))
            return

        request: Optional[Mapping[str, Any]] = None
        if operation == "packages":
            request = {
                "serial": qs.get("serial"),
                "third_party_only": self._qbool(qs, "third_party_only", True),
            }
        self._respond_adb_operation(
            cid=cid,
            operation=operation or "",
            request=request,
        )

    def do_POST(self):
        cid = self._cid()
        path, _qs = self._parse_url()
        if path == BROWSER_SESSION_PATH:
            if not super()._is_authorized():
                self._respond_browser_session(
                    cid=cid,
                    code=401,
                    result_code="browser_session_rejected",
                    clear=True,
                )
                return
            self._respond_browser_session(
                cid=cid,
                code=200,
                result_code="browser_session_created",
                session_id=_create_browser_session(),
            )
            return
        if path == BROWSER_SESSION_LOGOUT_PATH:
            _revoke_browser_session(self._browser_session_id())
            self._respond_browser_session(
                cid=cid,
                code=200,
                result_code="browser_session_cleared",
                clear=True,
            )
            return

        operation = _POST_OPERATIONS.get(path)
        tools_operation = _TOOLS_POST_OPERATIONS.get(path)
        is_apk_upload = path == APK_UPLOAD_PATH
        is_wireless_bootstrap = path == WIRELESS_BOOTSTRAP_PATH
        if (
            operation is None
            and tools_operation is None
            and not is_apk_upload
            and not is_wireless_bootstrap
        ):
            super().do_POST()
            return

        log.info(
            "request",
            extra={"correlation_id": cid, "method": "POST", "path": self.path},
        )
        if not self._require_auth(cid):
            return

        if is_apk_upload:
            handle_apk_upload(self, cid)
            return

        body, warnings = self._read_json_body()
        if any(warning in _BODY_ERROR_WARNINGS for warning in warnings):
            self._respond_invalid_body(cid, warnings)
            return

        if is_wireless_bootstrap:
            self._respond_wireless_bootstrap(
                cid=cid,
                request=body,
                warnings=warnings,
            )
            return

        if tools_operation is not None:
            self._respond_tools_operation(
                cid=cid,
                operation=tools_operation,
                request=body,
                warnings=warnings,
            )
            return

        self._respond_adb_operation(
            cid=cid,
            operation=operation or "",
            request=body,
            warnings=warnings,
        )
