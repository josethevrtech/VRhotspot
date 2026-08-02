"""Collect a bounded, read-only headset overview through typed ADB commands."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from vr_hotspotd.devtools.adb_operations import (
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_OUTPUT_LIMIT,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.platform_tools import collect_devbridge_tools_status


ADB_OVERVIEW_TIMEOUT_S = 15.0
ADB_OUTPUT_LIMIT_BYTES = 256 * 1024
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
PROPERTY_RE = re.compile(r"^\[([^]]+)\]: \[(.*)\]$")


class DeviceOverviewError(ValueError):
    """A headset overview request is invalid or cannot be executed."""


def _valid_serial(value: Any) -> str:
    if not isinstance(value, str) or not SERIAL_RE.fullmatch(value):
        raise DeviceOverviewError("serial contains unsupported characters")
    return value


def _effective_adb_path(tools_status: Optional[Mapping[str, Any]] = None) -> str:
    status = dict(tools_status or collect_devbridge_tools_status())
    adb = status.get("adb")
    if not isinstance(adb, Mapping):
        raise DeviceOverviewError("ADB tools status is unavailable")
    path = adb.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise DeviceOverviewError("ADB is not installed")
    return path


def _decode_bounded(value: bytes) -> str:
    if len(value) > ADB_OUTPUT_LIMIT_BYTES:
        raise DeviceOverviewError("ADB output exceeded the configured limit")
    return value.decode("utf-8", errors="replace").rstrip()


def _run(
    argv: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[int, str, str]:
    completed = runner(
        list(argv),
        input=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=ADB_OVERVIEW_TIMEOUT_S,
        check=False,
        shell=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    return (
        int(completed.returncode),
        _decode_bounded(completed.stdout or b""),
        _decode_bounded(completed.stderr or b""),
    )


def _device_command(adb: str, serial: str, *arguments: str) -> tuple[str, ...]:
    return (adb, "-s", serial, "shell", *arguments)


def _parse_properties(text: str) -> Dict[str, str]:
    properties: Dict[str, str] = {}
    for line in text.splitlines():
        match = PROPERTY_RE.match(line.strip())
        if match:
            properties[match.group(1)] = match.group(2)
    return properties


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_battery(text: str) -> Dict[str, Any]:
    values: Dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip()

    level = _parse_int(values.get("level"))
    scale = _parse_int(values.get("scale")) or 100
    percent = None
    if level is not None and scale > 0:
        percent = max(0, min(100, round((level / scale) * 100)))

    status_code = _parse_int(values.get("status"))
    status_names = {
        1: "unknown",
        2: "charging",
        3: "discharging",
        4: "not charging",
        5: "full",
    }
    sources = []
    for key, label in (
        ("ac powered", "AC"),
        ("usb powered", "USB"),
        ("wireless powered", "wireless"),
    ):
        if values.get(key, "").lower() == "true":
            sources.append(label)

    temperature_raw = _parse_int(values.get("temperature"))
    voltage_mv = _parse_int(values.get("voltage"))
    return {
        "percent": percent,
        "status": status_names.get(status_code, "unknown"),
        "charging": status_code in {2, 5} or bool(sources),
        "power_sources": sources,
        "temperature_c": (
            round(temperature_raw / 10.0, 1) if temperature_raw is not None else None
        ),
        "voltage_mv": voltage_mv,
    }


def _parse_storage(text: str) -> Dict[str, Any]:
    for line in reversed(text.splitlines()):
        parts = line.split()
        if len(parts) < 6 or parts[-1] != "/data":
            continue
        total_kib = _parse_int(parts[1])
        used_kib = _parse_int(parts[2])
        available_kib = _parse_int(parts[3])
        if total_kib is None or used_kib is None or available_kib is None:
            break
        return {
            "total_bytes": total_kib * 1024,
            "used_bytes": used_kib * 1024,
            "available_bytes": available_kib * 1024,
            "used_percent": _parse_int(parts[4].rstrip("%")),
        }
    return {}


def _first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> Optional[str]:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _parse_wifi(status_text: str, route_text: str) -> Dict[str, Any]:
    enabled_match = re.search(r"Wi-?Fi is (enabled|disabled)", status_text, re.IGNORECASE)
    ssid = _first_match(r"SSID:\s*(?:\"([^\"]+)\"|([^,\n]+))", status_text)
    if ssid is None:
        alternate = re.search(r"SSID:\s*([^,\n]+)", status_text, re.IGNORECASE)
        ssid = alternate.group(1).strip().strip('"') if alternate else None
    if ssid and ssid.lower() in {"<unknown ssid>", "unknown ssid", "<none>"}:
        ssid = None

    rssi = _parse_int(_first_match(r"RSSI:\s*(-?\d+)", status_text))
    frequency = _parse_int(_first_match(r"Frequency:\s*(\d+)", status_text))
    link_speed = _parse_int(
        _first_match(r"(?:Tx )?Link speed:\s*(\d+)\s*Mbps", status_text)
    )
    bssid = _first_match(r"BSSID:\s*([0-9A-Fa-f:]{17})", status_text)
    ip_address = _first_match(r"\bsrc\s+((?:\d{1,3}\.){3}\d{1,3})", route_text)
    gateway = _first_match(r"\bdefault via\s+((?:\d{1,3}\.){3}\d{1,3})", route_text)

    return {
        "enabled": enabled_match.group(1).lower() == "enabled" if enabled_match else None,
        "ssid": ssid,
        "bssid": bssid,
        "rssi_dbm": rssi,
        "frequency_mhz": frequency,
        "link_speed_mbps": link_speed,
        "ip_address": ip_address,
        "gateway": gateway,
    }


def _parse_uptime(text: str) -> Optional[int]:
    try:
        return max(0, int(float(text.split()[0])))
    except (IndexError, TypeError, ValueError):
        return None


def _parse_controllers(text: str) -> list[Dict[str, Any]]:
    controllers = []
    for line in text.splitlines():
        if "Paired device:" not in line:
            continue
        fields: Dict[str, str] = {}
        for part in line.split(","):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            fields[key.strip()] = value.strip()
        side = fields.get("Type")
        if not side:
            continue
        battery_text = fields.get("Battery", "").rstrip("%")
        controllers.append(
            {
                "side": side,
                "battery_percent": _parse_int(battery_text),
                "firmware": fields.get("Firmware"),
                "status": fields.get("Status"),
                "external_status": fields.get("ExternalStatus"),
                "tracking_status": fields.get("TrackingStatus"),
                "brightness": fields.get("BrightnessLevel"),
            }
        )
    return controllers


def _controller_service_available(text: str) -> bool:
    if not text:
        return False
    missing = re.search(
        r"(?:can't find|not found|unknown)\s+(?:service\s*:?\s*)?OVRRemoteService",
        text,
        re.IGNORECASE,
    )
    return missing is None


def _failure(operation: str, result_code: str, message: str) -> Dict[str, Any]:
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


def collect_device_overview(
    serial: Any,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Dict[str, Any]:
    """Collect a normalized MDM-style snapshot without arbitrary shell input."""

    operation = "device_overview"
    try:
        validated_serial = _valid_serial(serial)
        adb = _effective_adb_path(tools_status)

        getprop_code, getprop_stdout, getprop_stderr = _run(
            _device_command(adb, validated_serial, "getprop"),
            runner=runner,
        )
        if getprop_code != 0:
            return {
                "schema_version": 1,
                "operation": operation,
                "success": False,
                "result_code": RESULT_FAILED,
                "returncode": getprop_code,
                "stdout": "",
                "stderr": getprop_stderr or "Unable to read headset properties",
                "data": {},
            }

        unavailable = []

        def optional(name: str, *arguments: str) -> str:
            code, stdout, _stderr = _run(
                _device_command(adb, validated_serial, *arguments),
                runner=runner,
            )
            if code != 0:
                unavailable.append(name)
                return ""
            return stdout

        properties = _parse_properties(getprop_stdout)
        battery_text = optional("battery", "dumpsys", "battery")
        storage_text = optional("storage", "df", "-k", "/data")
        wifi_text = optional("wifi", "cmd", "wifi", "status")
        route_text = optional("network_route", "ip", "route")
        uptime_text = optional("uptime", "cat", "/proc/uptime")
        controllers_text = optional(
            "controllers",
            "dumpsys",
            "OVRRemoteService",
        )

        data = {
            "serial": validated_serial,
            "transport": "wireless" if ":" in validated_serial else "usb",
            "device": {
                "manufacturer": properties.get("ro.product.manufacturer"),
                "model": properties.get("ro.product.model"),
                "product": properties.get("ro.product.name"),
                "device": properties.get("ro.product.device"),
                "android_release": properties.get("ro.build.version.release"),
                "android_sdk": _parse_int(properties.get("ro.build.version.sdk")),
                "build": properties.get("ro.build.display.id"),
                "security_patch": properties.get("ro.build.version.security_patch"),
            },
            "battery": _parse_battery(battery_text) if battery_text else {},
            "storage": _parse_storage(storage_text) if storage_text else {},
            "wifi": _parse_wifi(wifi_text, route_text),
            "uptime_seconds": _parse_uptime(uptime_text),
            "controller_service_available": _controller_service_available(
                controllers_text
            ),
            "controllers": _parse_controllers(controllers_text),
            "unavailable": unavailable,
        }
        return {
            "schema_version": 1,
            "operation": operation,
            "success": True,
            "result_code": RESULT_OK,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "data": data,
        }
    except subprocess.TimeoutExpired:
        return _failure(operation, RESULT_TIMEOUT, "ADB device overview timed out")
    except DeviceOverviewError as exc:
        message = str(exc)
        if message in {"ADB is not installed", "ADB tools status is unavailable"}:
            result_code = RESULT_TOOLS_UNAVAILABLE
        elif message == "ADB output exceeded the configured limit":
            result_code = RESULT_OUTPUT_LIMIT
        else:
            result_code = RESULT_INVALID_REQUEST
        return _failure(operation, result_code, message)
