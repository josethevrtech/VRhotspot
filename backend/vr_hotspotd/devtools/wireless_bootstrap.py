"""Guided USB-to-wireless ADB bootstrap for Developer Hub.

The public API accepts only a validated USB ADB serial and optional TCP port.
Every subprocess argv is fixed and allowlisted; callers cannot supply shell
commands or arbitrary adb arguments.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from vr_hotspotd.devtools.adb_operations import (
    ADB_OUTPUT_LIMIT_BYTES,
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.platform_tools import collect_devbridge_tools_status


WIRELESS_DEFAULT_PORT = 5555
WIRELESS_COMMAND_TIMEOUT_S = 15.0
WIRELESS_CONNECT_ATTEMPTS = 4
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_ROUTE_SRC_RE = re.compile(r"(?:^|\s)src\s+([0-9.]+)(?:\s|$)")
_INET_RE = re.compile(r"(?:^|\s)inet\s+([0-9.]+)/\d+(?:\s|$)")


class WirelessBootstrapError(ValueError):
    """The guided wireless request is malformed or cannot be prepared."""


def _valid_serial(value: Any) -> str:
    if not isinstance(value, str) or not SERIAL_RE.fullmatch(value.strip()):
        raise WirelessBootstrapError("serial must be a USB ADB device serial")
    serial = value.strip()
    if ":" in serial:
        raise WirelessBootstrapError("serial must identify a USB-connected headset")
    return serial


def _valid_port(value: Any) -> int:
    if value is None:
        return WIRELESS_DEFAULT_PORT
    if isinstance(value, bool):
        raise WirelessBootstrapError("port must be an integer between 1 and 65535")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise WirelessBootstrapError("port must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise WirelessBootstrapError("port must be an integer between 1 and 65535")
    return port


def _adb_path(tools_status: Optional[Mapping[str, Any]] = None) -> str:
    status = dict(tools_status or collect_devbridge_tools_status())
    adb = status.get("adb")
    if not isinstance(adb, Mapping):
        raise WirelessBootstrapError("ADB tools status is unavailable")
    path = adb.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise WirelessBootstrapError("ADB is not installed")
    return path


def _decode(value: bytes) -> str:
    if len(value) > ADB_OUTPUT_LIMIT_BYTES:
        raise WirelessBootstrapError("ADB output exceeded the configured limit")
    return value.decode("utf-8", errors="replace").rstrip()


def _run(
    argv: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> tuple[int, str, str]:
    completed = runner(
        list(argv),
        input=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=WIRELESS_COMMAND_TIMEOUT_S,
        check=False,
        shell=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    return (
        int(completed.returncode),
        _decode(completed.stdout or b""),
        _decode(completed.stderr or b""),
    )


def _ipv4_from_output(value: str) -> Optional[str]:
    for pattern in (_ROUTE_SRC_RE, _INET_RE):
        match = pattern.search(value)
        if not match:
            continue
        try:
            address = ipaddress.IPv4Address(match.group(1))
        except ipaddress.AddressValueError:
            continue
        if not address.is_loopback and not address.is_unspecified:
            return str(address)
    return None


def _result(
    *,
    success: bool,
    result_code: str,
    stage: str,
    message: str,
    data: Optional[Mapping[str, Any]] = None,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": "enable_wireless",
        "success": success,
        "result_code": result_code,
        "stage": stage,
        "message": message,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "data": dict(data or {}),
    }


def enable_wireless_adb(
    serial: Any,
    port: Any = WIRELESS_DEFAULT_PORT,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Switch one authorized USB headset to TCP ADB and connect to it."""

    try:
        validated_serial = _valid_serial(serial)
        validated_port = _valid_port(port)
        adb = _adb_path(tools_status)
    except WirelessBootstrapError as exc:
        message = str(exc)
        code = RESULT_TOOLS_UNAVAILABLE if "ADB" in message else RESULT_INVALID_REQUEST
        return _result(
            success=False,
            result_code=code,
            stage="validate",
            message=message,
            returncode=2,
        )

    base_data: Dict[str, Any] = {
        "usb_serial": validated_serial,
        "port": validated_port,
    }

    try:
        state_rc, state_out, state_err = _run(
            (adb, "-s", validated_serial, "get-state"),
            runner=runner,
        )
        if state_rc != 0 or state_out.strip() != "device":
            return _result(
                success=False,
                result_code=RESULT_FAILED,
                stage="authorize",
                message=(
                    "The headset is connected by USB but has not authorized this computer. "
                    "Put on the headset and approve the USB debugging prompt."
                ),
                data=base_data,
                stdout=state_out,
                stderr=state_err,
                returncode=state_rc,
            )

        model_rc, model_out, _model_err = _run(
            (adb, "-s", validated_serial, "shell", "getprop", "ro.product.model"),
            runner=runner,
        )
        if model_rc == 0 and model_out.strip():
            base_data["model"] = model_out.strip().replace("_", " ")

        route_rc, route_out, route_err = _run(
            (adb, "-s", validated_serial, "shell", "ip", "route"),
            runner=runner,
        )
        address = _ipv4_from_output(route_out) if route_rc == 0 else None
        if address is None:
            addr_rc, addr_out, addr_err = _run(
                (
                    adb,
                    "-s",
                    validated_serial,
                    "shell",
                    "ip",
                    "-f",
                    "inet",
                    "addr",
                    "show",
                    "wlan0",
                ),
                runner=runner,
            )
            address = _ipv4_from_output(addr_out) if addr_rc == 0 else None
            if address is None:
                return _result(
                    success=False,
                    result_code=RESULT_FAILED,
                    stage="wifi",
                    message=(
                        "The headset is authorized, but no Wi-Fi address was found. "
                        "Connect the headset to Wi-Fi and try again."
                    ),
                    data=base_data,
                    stdout=addr_out or route_out,
                    stderr=addr_err or route_err,
                    returncode=addr_rc if addr_rc != 0 else route_rc,
                )

        base_data["ip"] = address
        target = f"{address}:{validated_port}"
        base_data["target"] = target

        tcp_rc, tcp_out, tcp_err = _run(
            (adb, "-s", validated_serial, "tcpip", str(validated_port)),
            runner=runner,
        )
        if tcp_rc != 0:
            return _result(
                success=False,
                result_code=RESULT_FAILED,
                stage="enable",
                message="The headset did not enable wireless ADB.",
                data=base_data,
                stdout=tcp_out,
                stderr=tcp_err,
                returncode=tcp_rc,
            )

        last_rc = 1
        last_out = ""
        last_err = ""
        for attempt in range(WIRELESS_CONNECT_ATTEMPTS):
            if attempt:
                sleeper(min(0.75 * (attempt + 1), 2.0))
            else:
                sleeper(0.75)
            last_rc, last_out, last_err = _run((adb, "connect", target), runner=runner)
            normalized = f"{last_out}\n{last_err}".lower()
            if last_rc == 0 and (
                "connected to" in normalized or "already connected to" in normalized
            ):
                return _result(
                    success=True,
                    result_code=RESULT_OK,
                    stage="complete",
                    message=f"{base_data.get('model', 'Headset')} is connected wirelessly.",
                    data=base_data,
                    stdout=last_out,
                    stderr=last_err,
                    returncode=last_rc,
                )

        return _result(
            success=False,
            result_code=RESULT_FAILED,
            stage="connect",
            message=(
                "Wireless ADB was enabled, but the headset did not accept the network "
                "connection yet. Keep it awake and try connecting again."
            ),
            data=base_data,
            stdout=last_out,
            stderr=last_err,
            returncode=last_rc,
        )
    except subprocess.TimeoutExpired:
        return _result(
            success=False,
            result_code=RESULT_TIMEOUT,
            stage="timeout",
            message="The headset did not respond before the setup timeout.",
            data=base_data,
            returncode=1,
        )
    except (OSError, WirelessBootstrapError) as exc:
        return _result(
            success=False,
            result_code=RESULT_FAILED,
            stage="execute",
            message=f"Wireless setup could not run: {exc}",
            data=base_data,
            returncode=1,
        )
