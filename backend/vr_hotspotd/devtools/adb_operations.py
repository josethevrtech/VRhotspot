"""Typed ADB operations for the VRhotspot Developer Hub.

This module is the executable ADB boundary used by future API, CLI, Flatpak,
and Web Portal surfaces. Callers choose an operation and provide structured
values; they never supply a command line or arbitrary adb arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import subprocess
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from vr_hotspotd.devtools.platform_tools import collect_devbridge_tools_status


ADB_DEFAULT_PORT = 5555
ADB_OPERATION_TIMEOUT_S = 15.0
ADB_OUTPUT_LIMIT_BYTES = 256 * 1024
PAIRING_CODE_RE = re.compile(r"^[0-9]{6}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")

RESULT_OK = "ok"
RESULT_INVALID_REQUEST = "invalid_request"
RESULT_TOOLS_UNAVAILABLE = "tools_unavailable"
RESULT_TIMEOUT = "timeout"
RESULT_FAILED = "failed"
RESULT_OUTPUT_LIMIT = "output_limit_exceeded"


@dataclass(frozen=True)
class ADBExecution:
    """Normalized subprocess result with bounded, decoded output."""

    returncode: int
    stdout: str
    stderr: str


class ADBOperationError(ValueError):
    """A typed ADB request is malformed or cannot be executed."""


def _valid_port(value: Any) -> int:
    if isinstance(value, bool):
        raise ADBOperationError("port must be an integer between 1 and 65535")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ADBOperationError("port must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ADBOperationError("port must be an integer between 1 and 65535")
    return port


def _valid_ipv4(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ADBOperationError("ip must be an IPv4 address")
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError as exc:
        raise ADBOperationError("ip must be an IPv4 address") from exc


def _valid_pairing_code(value: Any) -> str:
    if not isinstance(value, str) or not PAIRING_CODE_RE.fullmatch(value):
        raise ADBOperationError("pairing_code must contain exactly six digits")
    return value


def _valid_serial(value: Any) -> str:
    if not isinstance(value, str) or not SERIAL_RE.fullmatch(value):
        raise ADBOperationError("serial contains unsupported characters")
    return value


def _target(ip: Any, port: Any = ADB_DEFAULT_PORT) -> str:
    return f"{_valid_ipv4(ip)}:{_valid_port(port)}"


def _effective_adb_path(
    tools_status: Optional[Mapping[str, Any]] = None,
) -> str:
    status = dict(tools_status or collect_devbridge_tools_status())
    adb = status.get("adb")
    if not isinstance(adb, Mapping):
        raise ADBOperationError("ADB tools status is unavailable")
    path = adb.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ADBOperationError("ADB is not installed")
    return path


def _decode_bounded(value: bytes) -> str:
    if len(value) > ADB_OUTPUT_LIMIT_BYTES:
        raise ADBOperationError("ADB output exceeded the configured limit")
    return value.decode("utf-8", errors="replace").rstrip()


def _run(
    argv: Sequence[str],
    *,
    stdin_text: Optional[str] = None,
    timeout_s: float = ADB_OPERATION_TIMEOUT_S,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> ADBExecution:
    """Run one fully constructed argv without a shell or inherited stdin."""

    completed = runner(
        list(argv),
        input=None if stdin_text is None else stdin_text.encode("ascii"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        check=False,
        shell=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    return ADBExecution(
        returncode=int(completed.returncode),
        stdout=_decode_bounded(completed.stdout or b""),
        stderr=_decode_bounded(completed.stderr or b""),
    )


def _result(operation: str, execution: ADBExecution, **data: Any) -> Dict[str, Any]:
    success = execution.returncode == 0
    return {
        "schema_version": 1,
        "operation": operation,
        "success": success,
        "result_code": RESULT_OK if success else RESULT_FAILED,
        "returncode": execution.returncode,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "data": data,
    }


def adb_version(
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    return _result("version", _run((adb, "version"), runner=runner))


def adb_devices(
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    execution = _run((adb, "devices", "-l"), runner=runner)
    devices = []
    if execution.returncode == 0:
        for line in execution.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            serial = parts[0]
            state = parts[1] if len(parts) > 1 else "unknown"
            properties = {}
            for item in parts[2:]:
                if ":" in item:
                    key, value = item.split(":", 1)
                    properties[key] = value
            devices.append({"serial": serial, "state": state, "properties": properties})
    return _result("devices", execution, devices=devices)


def adb_pair(
    ip: Any,
    port: Any,
    pairing_code: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    """Pair using stdin so the one-time pairing code never appears in argv."""

    adb = _effective_adb_path(tools_status)
    target = _target(ip, port)
    code = _valid_pairing_code(pairing_code)
    execution = _run((adb, "pair", target), stdin_text=f"{code}\n", runner=runner)
    return _result("pair", execution, target=target)


def adb_connect(
    ip: Any,
    port: Any = ADB_DEFAULT_PORT,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    target = _target(ip, port)
    return _result("connect", _run((adb, "connect", target), runner=runner), target=target)


def adb_disconnect(
    serial: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated = _valid_serial(serial)
    return _result(
        "disconnect",
        _run((adb, "disconnect", validated), runner=runner),
        serial=validated,
    )


def execute_adb_operation(
    operation: str,
    request: Optional[Mapping[str, Any]] = None,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    """Dispatch one allowlisted operation from a structured request."""

    payload = dict(request or {})
    try:
        if operation == "version":
            return adb_version(tools_status=tools_status, runner=runner)
        if operation == "devices":
            return adb_devices(tools_status=tools_status, runner=runner)
        if operation == "pair":
            return adb_pair(
                payload.get("ip"),
                payload.get("port"),
                payload.get("pairing_code"),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "connect":
            return adb_connect(
                payload.get("ip"),
                payload.get("port", ADB_DEFAULT_PORT),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "disconnect":
            return adb_disconnect(
                payload.get("serial"),
                tools_status=tools_status,
                runner=runner,
            )
        raise ADBOperationError(f"unsupported ADB operation: {operation}")
    except subprocess.TimeoutExpired:
        return {
            "schema_version": 1,
            "operation": operation,
            "success": False,
            "result_code": RESULT_TIMEOUT,
            "returncode": None,
            "stdout": "",
            "stderr": "ADB operation timed out",
            "data": {},
        }
    except ADBOperationError as exc:
        message = str(exc)
        if message == "ADB is not installed" or message == "ADB tools status is unavailable":
            result_code = RESULT_TOOLS_UNAVAILABLE
        elif message == "ADB output exceeded the configured limit":
            result_code = RESULT_OUTPUT_LIMIT
        else:
            result_code = RESULT_INVALID_REQUEST
        return {
            "schema_version": 1,
            "operation": operation,
            "success": False,
            "result_code": result_code,
            "returncode": None,
            "stdout": "",
            "stderr": message,
            "data": {},
        }
