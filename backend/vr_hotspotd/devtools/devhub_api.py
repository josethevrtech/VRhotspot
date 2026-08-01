"""Authenticated Developer Hub API routes for typed ADB operations.

The main VRhotspot API remains the base handler. This subclass intercepts only
Developer Hub ADB routes and delegates every other request to the established
APIHandler implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from vr_hotspotd.api import APIHandler
from vr_hotspotd.devtools.adb_operations import (
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_OUTPUT_LIMIT,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
    execute_adb_operation,
)


log = logging.getLogger("vr_hotspotd.devhub_api")

_GET_OPERATIONS = {
    "/v1/devbridge/adb/version": "version",
    "/v1/devbridge/adb/devices": "devices",
}

_POST_OPERATIONS = {
    "/v1/devbridge/adb/pair": "pair",
    "/v1/devbridge/adb/connect": "connect",
    "/v1/devbridge/adb/disconnect": "disconnect",
}

_RESULT_HTTP_STATUS = {
    RESULT_OK: 200,
    RESULT_INVALID_REQUEST: 400,
    RESULT_TOOLS_UNAVAILABLE: 503,
    RESULT_TIMEOUT: 504,
    RESULT_OUTPUT_LIMIT: 502,
    RESULT_FAILED: 409,
}

_BODY_ERROR_WARNINGS = {
    "body_too_large",
    "body_read_failed",
    "body_not_object",
    "body_json_parse_failed",
}


class DevHubAPIHandler(APIHandler):
    """Extend the daemon API with executable Developer Hub operations."""

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
        path, _qs = self._parse_url()
        operation = _GET_OPERATIONS.get(path)
        if operation is None:
            super().do_GET()
            return

        log.info(
            "request",
            extra={"correlation_id": cid, "method": "GET", "path": self.path},
        )
        if not self._require_auth(cid):
            return
        self._respond_adb_operation(cid=cid, operation=operation)

    def do_POST(self):
        cid = self._cid()
        path, _qs = self._parse_url()
        operation = _POST_OPERATIONS.get(path)
        if operation is None:
            super().do_POST()
            return

        log.info(
            "request",
            extra={"correlation_id": cid, "method": "POST", "path": self.path},
        )
        if not self._require_auth(cid):
            return

        body, warnings = self._read_json_body()
        if any(warning in _BODY_ERROR_WARNINGS for warning in warnings):
            self._respond_invalid_body(cid, warnings)
            return

        self._respond_adb_operation(
            cid=cid,
            operation=operation,
            request=body,
            warnings=warnings,
        )
