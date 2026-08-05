"""VRhotspot-only USB-to-wireless ADB bootstrap for Developer Hub.

The public API accepts only a validated USB ADB serial and optional TCP port.
The daemon owns hotspot configuration and credentials. Wireless ADB is never
enabled until the headset address and route are verified against the active
VRhotspot network.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import subprocess
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from vr_hotspotd.config import load_config, validate_network_config
from vr_hotspotd.devtools.adb_operations import (
    ADB_OUTPUT_LIMIT_BYTES,
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_TIMEOUT,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.platform_tools import collect_devbridge_tools_status
from vr_hotspotd.state import load_state


WIRELESS_DEFAULT_PORT = 5555
WIRELESS_COMMAND_TIMEOUT_S = 15.0
WIRELESS_CONNECT_ATTEMPTS = 4
WIRELESS_NETWORK_POLL_ATTEMPTS = 12
WIRELESS_NETWORK_POLL_INTERVAL_S = 1.0
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_INET_RE = re.compile(r"(?:^|\s)inet\s+([0-9.]+)/\d+(?:\s|$)")
_ROUTE_SRC_RE = re.compile(r"(?:^|\s)src\s+([0-9.]+)(?:\s|$)")
_ROUTE_VIA_RE = re.compile(r"(?:^|\s)via\s+([0-9.]+)(?:\s|$)")
_ROUTE_DEV_RE = re.compile(r"(?:^|\s)dev\s+([A-Za-z0-9_.-]+)(?:\s|$)")

RESULT_HOTSPOT_NOT_RUNNING = "hotspot_not_running"
RESULT_HOTSPOT_CONFIGURATION_INCOMPLETE = "hotspot_configuration_incomplete"
RESULT_WIFI_CONTROL_UNAVAILABLE = "wifi_control_unavailable"
RESULT_HEADSET_NOT_ON_VRHOTSPOT = "headset_not_on_vrhotspot"
RESULT_HEADSET_ADDRESS_OUTSIDE_SUBNET = "headset_address_outside_hotspot_subnet"
RESULT_HEADSET_ROUTE_MISMATCH = "headset_route_mismatch"
RESULT_WIRELESS_ADB_ENABLE_FAILED = "wireless_adb_enable_failed"
RESULT_WIRELESS_ADB_CONNECT_FAILED = "wireless_adb_connect_failed"


class WirelessBootstrapError(ValueError):
    """The guided wireless request is malformed or cannot be prepared."""


@dataclass(frozen=True)
class HotspotContext:
    ssid: str
    passphrase: str
    security: str
    ap_interface: str
    gateway: ipaddress.IPv4Address
    network: ipaddress.IPv4Network


@dataclass(frozen=True)
class HeadsetNetwork:
    address: Optional[ipaddress.IPv4Address]
    gateway: Optional[ipaddress.IPv4Address]
    interface: Optional[str]
    route_source: Optional[ipaddress.IPv4Address]
    address_output: str
    route_output: str


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


def _safe_text(value: str, secret: str = "") -> str:
    text = str(value or "")
    if secret:
        text = text.replace(secret, "<redacted>")
    return text


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
    secret: str = "",
) -> Dict[str, Any]:
    public_data = dict(data or {})
    public_data.pop("passphrase", None)
    return {
        "schema_version": 2,
        "operation": "enable_wireless",
        "success": success,
        "result_code": result_code,
        "stage": stage,
        "message": _safe_text(message, secret),
        "returncode": returncode,
        "stdout": _safe_text(stdout, secret),
        "stderr": _safe_text(stderr, secret),
        "data": public_data,
    }


def _hotspot_context(
    config: Optional[Mapping[str, Any]] = None,
    state: Optional[Mapping[str, Any]] = None,
) -> HotspotContext:
    cfg = dict(config if config is not None else load_config())
    runtime = dict(state if state is not None else load_state())
    phase = str(runtime.get("phase") or "").strip().lower()
    if not bool(runtime.get("running")) and phase != "running":
        raise WirelessBootstrapError(RESULT_HOTSPOT_NOT_RUNNING)

    validation_errors = validate_network_config(cfg)
    ssid = cfg.get("ssid")
    passphrase = cfg.get("wpa2_passphrase")
    security = str(cfg.get("ap_security") or "wpa2").strip().lower()
    ap_interface = str(
        runtime.get("ap_interface")
        or runtime.get("precreated_ap_ifname")
        or ""
    ).strip()

    if (
        validation_errors
        or not isinstance(ssid, str)
        or not ssid.strip()
        or not isinstance(passphrase, str)
        or not passphrase
        or security not in {"wpa2", "wpa3_sae"}
        or not ap_interface
    ):
        raise WirelessBootstrapError(RESULT_HOTSPOT_CONFIGURATION_INCOMPLETE)

    try:
        gateway = ipaddress.IPv4Address(str(cfg.get("lan_gateway_ip") or ""))
        network = ipaddress.IPv4Network(f"{gateway}/24", strict=False)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise WirelessBootstrapError(
            RESULT_HOTSPOT_CONFIGURATION_INCOMPLETE
        ) from exc

    if (
        gateway.is_loopback
        or gateway.is_link_local
        or gateway == network.network_address
        or gateway == network.broadcast_address
    ):
        raise WirelessBootstrapError(RESULT_HOTSPOT_CONFIGURATION_INCOMPLETE)

    return HotspotContext(
        ssid=ssid.strip(),
        passphrase=passphrase,
        security=security,
        ap_interface=ap_interface,
        gateway=gateway,
        network=network,
    )


def _ipv4(value: Optional[str]) -> Optional[ipaddress.IPv4Address]:
    if not value:
        return None
    try:
        return ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None


def _first_address(value: str) -> Optional[ipaddress.IPv4Address]:
    for match in _INET_RE.finditer(value):
        address = _ipv4(match.group(1))
        if (
            address is not None
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_unspecified
        ):
            return address
    return None


def _route_value(pattern: re.Pattern[str], value: str) -> Optional[str]:
    match = pattern.search(value)
    return match.group(1) if match else None


def _inspect_headset_network(
    adb: str,
    serial: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> HeadsetNetwork:
    addr_rc, addr_out, _addr_err = _run(
        (
            adb,
            "-s",
            serial,
            "shell",
            "ip",
            "-4",
            "addr",
            "show",
            "wlan0",
        ),
        runner=runner,
    )
    route_rc, route_out, _route_err = _run(
        (
            adb,
            "-s",
            serial,
            "shell",
            "ip",
            "route",
            "show",
            "default",
        ),
        runner=runner,
    )
    address = _first_address(addr_out) if addr_rc == 0 else None
    route_source = _ipv4(_route_value(_ROUTE_SRC_RE, route_out)) if route_rc == 0 else None
    if address is None:
        address = route_source
    gateway = _ipv4(_route_value(_ROUTE_VIA_RE, route_out)) if route_rc == 0 else None
    interface = _route_value(_ROUTE_DEV_RE, route_out) if route_rc == 0 else None
    return HeadsetNetwork(
        address=address,
        gateway=gateway,
        interface=interface,
        route_source=route_source,
        address_output=addr_out,
        route_output=route_out,
    )


def _network_failure(
    context: HotspotContext,
    snapshot: HeadsetNetwork,
) -> Optional[str]:
    address = snapshot.address
    if address is None:
        return RESULT_HEADSET_NOT_ON_VRHOTSPOT
    if (
        address not in context.network
        or address in {
            context.network.network_address,
            context.network.broadcast_address,
            context.gateway,
        }
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    ):
        return RESULT_HEADSET_ADDRESS_OUTSIDE_SUBNET
    if snapshot.gateway is not None and snapshot.gateway != context.gateway:
        return RESULT_HEADSET_ROUTE_MISMATCH
    if snapshot.interface is not None and snapshot.interface != "wlan0":
        return RESULT_HEADSET_ROUTE_MISMATCH
    if snapshot.route_source is not None and snapshot.route_source not in context.network:
        return RESULT_HEADSET_ROUTE_MISMATCH
    return None


def _public_context(context: HotspotContext) -> Dict[str, Any]:
    return {
        "ssid": context.ssid,
        "security": context.security,
        "ap_interface": context.ap_interface,
        "gateway": str(context.gateway),
        "subnet": str(context.network),
    }


def _wifi_control_available(
    adb: str,
    serial: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> bool:
    rc, stdout, stderr = _run(
        (adb, "-s", serial, "shell", "cmd", "wifi", "help"),
        runner=runner,
    )
    help_text = f"{stdout}\n{stderr}".lower()
    return rc == 0 and "connect-network" in help_text


def _join_security(context: HotspotContext) -> str:
    return "wpa3" if context.security == "wpa3_sae" else "wpa2"


def _manual_join_result(
    context: HotspotContext,
    base_data: Mapping[str, Any],
    *,
    message: str,
) -> Dict[str, Any]:
    data = dict(base_data)
    data.update(_public_context(context))
    data.update({"requires_manual_join": True})
    return _result(
        success=False,
        result_code=RESULT_WIFI_CONTROL_UNAVAILABLE,
        stage="join_vrhotspot",
        message=message,
        data=data,
        returncode=1,
        secret=context.passphrase,
    )


def enable_wireless_adb(
    serial: Any,
    port: Any = WIRELESS_DEFAULT_PORT,
    *,
    tools_status: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    state: Optional[Mapping[str, Any]] = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Join the active VRhotspot network, then enable wireless ADB."""

    try:
        validated_serial = _valid_serial(serial)
        validated_port = _valid_port(port)
    except WirelessBootstrapError as exc:
        return _result(
            success=False,
            result_code=RESULT_INVALID_REQUEST,
            stage="validate",
            message=str(exc),
            returncode=2,
        )

    try:
        context = _hotspot_context(config, state)
    except WirelessBootstrapError as exc:
        code = str(exc)
        message = (
            "Start VRhotspot before setting up a wireless headset."
            if code == RESULT_HOTSPOT_NOT_RUNNING
            else "The active VRhotspot configuration is incomplete."
        )
        return _result(
            success=False,
            result_code=code,
            stage="hotspot",
            message=message,
            returncode=2,
        )

    try:
        adb = _adb_path(tools_status)
    except WirelessBootstrapError as exc:
        return _result(
            success=False,
            result_code=RESULT_TOOLS_UNAVAILABLE,
            stage="tools",
            message=str(exc),
            returncode=2,
        )

    base_data: Dict[str, Any] = {
        "usb_serial": validated_serial,
        "port": validated_port,
        **_public_context(context),
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
                secret=context.passphrase,
            )

        model_rc, model_out, _model_err = _run(
            (adb, "-s", validated_serial, "shell", "getprop", "ro.product.model"),
            runner=runner,
        )
        if model_rc == 0 and model_out.strip():
            base_data["model"] = model_out.strip().replace("_", " ")

        snapshot = _inspect_headset_network(adb, validated_serial, runner=runner)
        network_failure = _network_failure(context, snapshot)

        if network_failure is not None:
            if not _wifi_control_available(adb, validated_serial, runner=runner):
                return _manual_join_result(
                    context,
                    base_data,
                    message=(
                        f"Select {context.ssid} in the headset Wi-Fi settings. "
                        "Developer Hub will continue automatically after the headset "
                        "joins the dedicated VRhotspot network."
                    ),
                )

            join_rc, _join_out, _join_err = _run(
                (
                    adb,
                    "-s",
                    validated_serial,
                    "shell",
                    "cmd",
                    "wifi",
                    "connect-network",
                    context.ssid,
                    _join_security(context),
                    context.passphrase,
                ),
                runner=runner,
            )
            if join_rc != 0:
                return _manual_join_result(
                    context,
                    base_data,
                    message=(
                        "Automatic Wi-Fi setup is unavailable on this headset. "
                        f"Select {context.ssid} in the headset Wi-Fi settings."
                    ),
                )

            snapshot = HeadsetNetwork(None, None, None, None, "", "")
            network_failure = RESULT_HEADSET_NOT_ON_VRHOTSPOT
            for attempt in range(WIRELESS_NETWORK_POLL_ATTEMPTS):
                if attempt:
                    sleeper(WIRELESS_NETWORK_POLL_INTERVAL_S)
                snapshot = _inspect_headset_network(adb, validated_serial, runner=runner)
                network_failure = _network_failure(context, snapshot)
                if network_failure is None:
                    break

            if network_failure is not None:
                data = dict(base_data)
                data.update({"requires_manual_join": True})
                return _result(
                    success=False,
                    result_code=network_failure,
                    stage="verify_vrhotspot",
                    message=(
                        f"The headset has not joined {context.ssid} yet. "
                        "Keep it awake, select the VRhotspot network, and try again."
                    ),
                    data=data,
                    returncode=1,
                    secret=context.passphrase,
                )

        # Re-read immediately before mutating ADB transport. This prevents a
        # stale address from passing validation and then being used after a
        # network transition.
        verified = _inspect_headset_network(adb, validated_serial, runner=runner)
        verified_failure = _network_failure(context, verified)
        if verified_failure is not None:
            return _result(
                success=False,
                result_code=verified_failure,
                stage="verify_vrhotspot",
                message=(
                    "The headset network changed before wireless ADB could be enabled. "
                    "Reconnect it to VRhotspot and try again."
                ),
                data=base_data,
                returncode=1,
                secret=context.passphrase,
            )

        address = verified.address
        if address is None:  # Defensive; _network_failure already rejects it.
            return _result(
                success=False,
                result_code=RESULT_HEADSET_NOT_ON_VRHOTSPOT,
                stage="verify_vrhotspot",
                message="The headset does not have a verified VRhotspot address.",
                data=base_data,
                returncode=1,
                secret=context.passphrase,
            )

        base_data["ip"] = str(address)
        target = f"{address}:{validated_port}"
        base_data["target"] = target
        base_data["transport"] = "vrhotspot"

        tcp_rc, tcp_out, tcp_err = _run(
            (adb, "-s", validated_serial, "tcpip", str(validated_port)),
            runner=runner,
        )
        if tcp_rc != 0:
            return _result(
                success=False,
                result_code=RESULT_WIRELESS_ADB_ENABLE_FAILED,
                stage="enable_wireless",
                message="The headset did not enable wireless ADB.",
                data=base_data,
                stdout=tcp_out,
                stderr=tcp_err,
                returncode=tcp_rc,
                secret=context.passphrase,
            )

        last_rc = 1
        last_out = ""
        last_err = ""
        for attempt in range(WIRELESS_CONNECT_ATTEMPTS):
            sleeper(0.75 if attempt == 0 else min(0.75 * (attempt + 1), 2.0))
            last_rc, last_out, last_err = _run(
                (adb, "connect", target),
                runner=runner,
            )
            normalized = f"{last_out}\n{last_err}".lower()
            if last_rc == 0 and (
                "connected to" in normalized or "already connected to" in normalized
            ):
                return _result(
                    success=True,
                    result_code=RESULT_OK,
                    stage="complete",
                    message=(
                        f"{base_data.get('model', 'Headset')} is connected wirelessly "
                        f"through {context.ssid}."
                    ),
                    data=base_data,
                    stdout=last_out,
                    stderr=last_err,
                    returncode=last_rc,
                    secret=context.passphrase,
                )

        return _result(
            success=False,
            result_code=RESULT_WIRELESS_ADB_CONNECT_FAILED,
            stage="connect",
            message=(
                "Wireless ADB was enabled on the verified VRhotspot address, "
                "but the headset did not accept the connection yet."
            ),
            data=base_data,
            stdout=last_out,
            stderr=last_err,
            returncode=last_rc,
            secret=context.passphrase,
        )
    except subprocess.TimeoutExpired:
        return _result(
            success=False,
            result_code=RESULT_TIMEOUT,
            stage="timeout",
            message="The headset did not respond before the setup timeout.",
            data=base_data,
            returncode=1,
            secret=context.passphrase,
        )
    except (OSError, WirelessBootstrapError):
        return _result(
            success=False,
            result_code=RESULT_FAILED,
            stage="execute",
            message="Wireless setup could not complete.",
            data=base_data,
            returncode=1,
            secret=context.passphrase,
        )
