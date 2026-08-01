"""Typed ADB operations for the VRhotspot Developer Hub.

This module is the executable ADB boundary used by the API, CLI, Flatpak,
and Web Portal surfaces. Callers choose an operation and provide structured
values; they never supply a command line or arbitrary adb arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from vr_hotspotd.devtools.platform_tools import collect_devbridge_tools_status


ADB_DEFAULT_PORT = 5555
ADB_OPERATION_TIMEOUT_S = 15.0
ADB_INSTALL_TIMEOUT_S = 300.0
ADB_APP_OPERATION_TIMEOUT_S = 60.0
ADB_OUTPUT_LIMIT_BYTES = 256 * 1024
APK_MAX_BYTES = 8 * 1024 * 1024 * 1024
PAIRING_CODE_RE = re.compile(r"^[0-9]{6}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
PACKAGE_NAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)

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


def _valid_package_name(value: Any) -> str:
    if not isinstance(value, str) or not PACKAGE_NAME_RE.fullmatch(value):
        raise ADBOperationError("package must be a valid Android package name")
    return value


def _valid_bool(value: Any, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ADBOperationError(f"{name} must be a boolean")
    return value


def _valid_apk_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        raise ADBOperationError("apk_path must be an absolute path to an APK file")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".apk":
        raise ADBOperationError("apk_path must be an absolute path to an APK file")
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise ADBOperationError("apk_path does not exist or is not readable") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ADBOperationError("apk_path must reference a regular non-symlink file")
    if file_stat.st_size <= 0:
        raise ADBOperationError("apk_path must not be empty")
    if file_stat.st_size > APK_MAX_BYTES:
        raise ADBOperationError("apk_path exceeds the supported size limit")
    return str(path)


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


def _device_argv(adb: str, serial: Any, *arguments: str) -> tuple[str, ...]:
    return (adb, "-s", _valid_serial(serial), *arguments)


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


def adb_packages(
    serial: Any,
    third_party_only: Any = True,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    only_third_party = _valid_bool(
        third_party_only,
        name="third_party_only",
        default=True,
    )
    argv = list(_device_argv(adb, validated_serial, "shell", "pm", "list", "packages"))
    if only_third_party:
        argv.append("-3")
    execution = _run(argv, timeout_s=ADB_APP_OPERATION_TIMEOUT_S, runner=runner)
    packages = []
    if execution.returncode == 0:
        for line in execution.stdout.splitlines():
            value = line.strip()
            if value.startswith("package:"):
                package = value[len("package:") :]
                if PACKAGE_NAME_RE.fullmatch(package):
                    packages.append(package)
    return _result(
        "packages",
        execution,
        serial=validated_serial,
        third_party_only=only_third_party,
        packages=packages,
    )


def adb_install(
    serial: Any,
    apk_path: Any,
    reinstall: Any = True,
    grant_permissions: Any = False,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    validated_path = _valid_apk_path(apk_path)
    should_reinstall = _valid_bool(reinstall, name="reinstall", default=True)
    should_grant = _valid_bool(
        grant_permissions,
        name="grant_permissions",
        default=False,
    )
    argv = list(_device_argv(adb, validated_serial, "install"))
    if should_reinstall:
        argv.append("-r")
    if should_grant:
        argv.append("-g")
    argv.append(validated_path)
    execution = _run(argv, timeout_s=ADB_INSTALL_TIMEOUT_S, runner=runner)
    return _result(
        "install",
        execution,
        serial=validated_serial,
        apk_path=validated_path,
        reinstall=should_reinstall,
        grant_permissions=should_grant,
    )


def adb_launch(
    serial: Any,
    package: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    validated_package = _valid_package_name(package)
    argv = _device_argv(
        adb,
        validated_serial,
        "shell",
        "monkey",
        "-p",
        validated_package,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    )
    execution = _run(argv, timeout_s=ADB_APP_OPERATION_TIMEOUT_S, runner=runner)
    return _result(
        "launch",
        execution,
        serial=validated_serial,
        package=validated_package,
    )


def adb_stop(
    serial: Any,
    package: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    validated_package = _valid_package_name(package)
    execution = _run(
        _device_argv(
            adb,
            validated_serial,
            "shell",
            "am",
            "force-stop",
            validated_package,
        ),
        timeout_s=ADB_APP_OPERATION_TIMEOUT_S,
        runner=runner,
    )
    return _result(
        "stop",
        execution,
        serial=validated_serial,
        package=validated_package,
    )


def adb_clear_data(
    serial: Any,
    package: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    validated_package = _valid_package_name(package)
    execution = _run(
        _device_argv(
            adb,
            validated_serial,
            "shell",
            "pm",
            "clear",
            validated_package,
        ),
        timeout_s=ADB_APP_OPERATION_TIMEOUT_S,
        runner=runner,
    )
    return _result(
        "clear_data",
        execution,
        serial=validated_serial,
        package=validated_package,
    )


def adb_uninstall(
    serial: Any,
    package: Any,
    keep_data: Any = False,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    adb = _effective_adb_path(tools_status)
    validated_serial = _valid_serial(serial)
    validated_package = _valid_package_name(package)
    should_keep_data = _valid_bool(keep_data, name="keep_data", default=False)
    argv = list(_device_argv(adb, validated_serial, "uninstall"))
    if should_keep_data:
        argv.append("-k")
    argv.append(validated_package)
    execution = _run(argv, timeout_s=ADB_APP_OPERATION_TIMEOUT_S, runner=runner)
    return _result(
        "uninstall",
        execution,
        serial=validated_serial,
        package=validated_package,
        keep_data=should_keep_data,
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
        if operation == "packages":
            return adb_packages(
                payload.get("serial"),
                payload.get("third_party_only", True),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "install":
            return adb_install(
                payload.get("serial"),
                payload.get("apk_path"),
                payload.get("reinstall", True),
                payload.get("grant_permissions", False),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "launch":
            return adb_launch(
                payload.get("serial"),
                payload.get("package"),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "stop":
            return adb_stop(
                payload.get("serial"),
                payload.get("package"),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "clear_data":
            return adb_clear_data(
                payload.get("serial"),
                payload.get("package"),
                tools_status=tools_status,
                runner=runner,
            )
        if operation == "uninstall":
            return adb_uninstall(
                payload.get("serial"),
                payload.get("package"),
                payload.get("keep_data", False),
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
