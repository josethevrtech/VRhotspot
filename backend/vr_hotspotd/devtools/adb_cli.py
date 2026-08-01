"""Direct command-line client for typed Developer Hub ADB operations.

The daemon owns ADB execution. This client only sends structured, authenticated
requests to the allowlisted Developer Hub API endpoints; it never accepts an
arbitrary adb command line.
"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, Mapping, Optional, Sequence
from urllib.parse import urlencode
from urllib.request import Request
import uuid

from vr_hotspotd import cli as base_cli


ADB_VERSION_PATH = "/v1/devbridge/adb/version"
ADB_DEVICES_PATH = "/v1/devbridge/adb/devices"
ADB_PAIR_PATH = "/v1/devbridge/adb/pair"
ADB_CONNECT_PATH = "/v1/devbridge/adb/connect"
ADB_DISCONNECT_PATH = "/v1/devbridge/adb/disconnect"
ADB_PACKAGES_PATH = "/v1/devbridge/adb/packages"
ADB_INSTALL_PATH = "/v1/devbridge/adb/install"
ADB_LAUNCH_PATH = "/v1/devbridge/adb/launch"
ADB_STOP_PATH = "/v1/devbridge/adb/stop"
ADB_CLEAR_DATA_PATH = "/v1/devbridge/adb/clear-data"
ADB_UNINSTALL_PATH = "/v1/devbridge/adb/uninstall"
ADB_DEFAULT_PORT = 5555
_PAIRING_CODE_RE = re.compile(r"^[0-9]{6}$")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_PACKAGE_NAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)


class ADBCLIError(base_cli.CLIError):
    """Expected Developer Hub CLI error suitable for terminal output."""


def _validated_port_argument(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 65535")
    return port


def _validated_serial_argument(value: str) -> str:
    if not _SERIAL_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(
            "contains unsupported characters; expected an adb serial such as "
            "192.168.68.23:5555"
        )
    return value


def _validated_package_argument(value: str) -> str:
    if not _PACKAGE_NAME_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(
            "must be an Android package name such as com.example.application"
        )
    return value


def _validated_apk_path_argument(value: str) -> str:
    if not value or len(value) > 4096 or "\x00" in value:
        raise argparse.ArgumentTypeError("must be an absolute path ending in .apk")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".apk":
        raise argparse.ArgumentTypeError("must be an absolute path ending in .apk")
    return str(path)


def _read_pairing_code() -> str:
    try:
        if sys.stdin.isatty():
            code = getpass.getpass("Wireless Debugging pairing code: ", stream=sys.stderr)
        else:
            code = sys.stdin.readline(65).rstrip("\r\n")
    except (EOFError, OSError, UnicodeError) as exc:
        raise ADBCLIError("Unable to read the Wireless Debugging pairing code.") from exc
    if not _PAIRING_CODE_RE.fullmatch(code):
        raise ADBCLIError("Wireless Debugging pairing code must contain exactly six digits.")
    return code


def _redact_values(value: object, secrets: Sequence[str]) -> str:
    text = base_cli._redacted_error_text(value, "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _sanitized_cli_error(exc: Exception, secrets: Sequence[str]) -> ADBCLIError:
    return ADBCLIError(_redact_values(exc, secrets))


def _contains_any_secret(value: Any, secrets: Sequence[str]) -> bool:
    return any(secret and base_cli._contains_secret(value, secret) for secret in secrets)


def _request_api_data(
    api_url: str,
    path: str,
    *,
    method: str,
    token: str,
    timeout: float,
    correlation_prefix: str,
    payload_description: str,
    body: Optional[Mapping[str, Any]] = None,
    sensitive_values: Sequence[str] = (),
) -> Dict[str, Any]:
    endpoint = base_cli._validated_api_url(api_url) + path
    token = base_cli._validated_token(token)
    secrets = tuple(value for value in (token, *sensitive_values) if value)
    headers = {
        "Accept": "application/json",
        "User-Agent": "vr-hotspot-adb-cli",
        "X-Correlation-Id": f"{correlation_prefix}-{uuid.uuid4()}",
    }
    request_data = None
    if body is not None:
        request_data = json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Api-Token"] = token

    transport_exc: Optional[Exception] = None
    try:
        request = Request(endpoint, data=request_data, headers=headers, method=method)
        with base_cli._open_preflight_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read()
    except base_cli.CLIError as exc:
        raise _sanitized_cli_error(exc, secrets) from None
    except Exception as exc:
        transport_exc = exc

    if transport_exc is not None:
        transport_error = base_cli._transport_cli_error(
            transport_exc,
            endpoint=endpoint,
            token=token,
        )
        transport_exc = None
        raise _sanitized_cli_error(transport_error, secrets) from None

    if 300 <= status < 400:
        raise _sanitized_cli_error(base_cli._redirect_error(status), secrets) from None
    if status != 200:
        error = base_cli._api_failure_cli_error(status, raw, token=token)
        raise _sanitized_cli_error(error, secrets) from None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ADBCLIError("The VR Hotspot API returned invalid JSON.") from None
    if not isinstance(payload, Mapping):
        raise ADBCLIError("The VR Hotspot API returned an invalid response envelope.")

    result_code = payload.get("result_code")
    if result_code not in (None, "ok"):
        safe_code = _redact_values(result_code, secrets)
        raise ADBCLIError(f"The VR Hotspot API returned {safe_code}.")

    report = payload.get("data")
    if not isinstance(report, Mapping):
        raise ADBCLIError(
            f"The VR Hotspot API response did not contain {payload_description}."
        )
    if _contains_any_secret(report, secrets):
        raise ADBCLIError(
            "The VR Hotspot API returned a response containing a sensitive request "
            "value; refusing to print or export it."
        )
    return dict(report)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    base_cli._add_client_arguments(parser)


def _add_serial_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--serial",
        required=True,
        type=_validated_serial_argument,
        metavar="SERIAL",
        help="Exact adb device serial, usually IPV4:PORT.",
    )


def _add_package_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--package",
        required=True,
        type=_validated_package_argument,
        metavar="PACKAGE",
        help="Android application package name.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vr-hotspot-adb",
        description=(
            "Run explicit ADB device and application operations through the authenticated "
            "VRhotspot Developer Hub daemon."
        ),
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    version_parser = commands.add_parser("version", help="Show the effective adb version.")
    _add_common_arguments(version_parser)

    devices_parser = commands.add_parser(
        "devices",
        help="List devices reported by adb devices -l.",
    )
    _add_common_arguments(devices_parser)

    pair_parser = commands.add_parser(
        "pair",
        help=(
            "Pair with Android Wireless Debugging. The six-digit code is read from "
            "hidden input or stdin and is never accepted in argv."
        ),
    )
    _add_common_arguments(pair_parser)
    pair_parser.add_argument(
        "--ip",
        required=True,
        type=base_cli._validated_ipv4_argument,
        metavar="IPV4",
        help="Headset Wireless Debugging IPv4 address.",
    )
    pair_parser.add_argument(
        "--port",
        required=True,
        type=_validated_port_argument,
        metavar="PORT",
        help="Temporary Wireless Debugging pairing port shown by the headset.",
    )

    connect_parser = commands.add_parser(
        "connect",
        help="Connect adb to a previously paired headset.",
    )
    _add_common_arguments(connect_parser)
    connect_parser.add_argument(
        "--ip",
        required=True,
        type=base_cli._validated_ipv4_argument,
        metavar="IPV4",
        help="Headset IPv4 address.",
    )
    connect_parser.add_argument(
        "--port",
        default=ADB_DEFAULT_PORT,
        type=_validated_port_argument,
        metavar="PORT",
        help=f"ADB connection port (default: {ADB_DEFAULT_PORT}).",
    )

    disconnect_parser = commands.add_parser(
        "disconnect",
        help="Disconnect one adb serial without affecting other devices.",
    )
    _add_common_arguments(disconnect_parser)
    _add_serial_argument(disconnect_parser)

    packages_parser = commands.add_parser(
        "packages",
        help="List installed application packages on one headset.",
    )
    _add_common_arguments(packages_parser)
    _add_serial_argument(packages_parser)
    packages_parser.add_argument(
        "--all",
        action="store_true",
        help="Include system packages instead of listing third-party apps only.",
    )

    install_parser = commands.add_parser(
        "install",
        help="Install or update an APK on one headset.",
    )
    _add_common_arguments(install_parser)
    install_parser.set_defaults(timeout=300.0)
    _add_serial_argument(install_parser)
    install_parser.add_argument(
        "--apk",
        required=True,
        type=_validated_apk_path_argument,
        metavar="PATH",
        help="Absolute host path to the APK file.",
    )
    install_parser.add_argument(
        "--no-reinstall",
        action="store_true",
        help="Do not pass adb install -r; fail if the package is already installed.",
    )
    install_parser.add_argument(
        "--grant-permissions",
        action="store_true",
        help="Grant runtime permissions requested by the APK during installation.",
    )

    launch_parser = commands.add_parser("launch", help="Launch an installed application.")
    _add_common_arguments(launch_parser)
    _add_serial_argument(launch_parser)
    _add_package_argument(launch_parser)

    stop_parser = commands.add_parser("stop", help="Force-stop an installed application.")
    _add_common_arguments(stop_parser)
    _add_serial_argument(stop_parser)
    _add_package_argument(stop_parser)

    clear_parser = commands.add_parser(
        "clear-data",
        help="Clear an application's local data on the selected headset.",
    )
    _add_common_arguments(clear_parser)
    _add_serial_argument(clear_parser)
    _add_package_argument(clear_parser)

    uninstall_parser = commands.add_parser(
        "uninstall",
        help="Uninstall an application from one headset.",
    )
    _add_common_arguments(uninstall_parser)
    _add_serial_argument(uninstall_parser)
    _add_package_argument(uninstall_parser)
    uninstall_parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep application data and cache directories during uninstall.",
    )

    return parser


def _operation_request(
    args: argparse.Namespace,
) -> tuple[str, str, str, Optional[Dict[str, Any]], tuple[str, ...]]:
    operation = args.operation
    if operation == "version":
        return ADB_VERSION_PATH, "GET", "an ADB version result", None, ()
    if operation == "devices":
        return ADB_DEVICES_PATH, "GET", "an ADB device result", None, ()
    if operation == "pair":
        if args.token_stdin:
            raise ADBCLIError(
                "pair cannot use --token-stdin because stdin is reserved for the "
                "six-digit pairing code; use the daemon env file, environment variable, "
                "or --token for API authentication."
            )
        pairing_code = _read_pairing_code()
        return (
            ADB_PAIR_PATH,
            "POST",
            "an ADB pairing result",
            {"ip": args.ip, "port": args.port, "pairing_code": pairing_code},
            (pairing_code,),
        )
    if operation == "connect":
        return (
            ADB_CONNECT_PATH,
            "POST",
            "an ADB connection result",
            {"ip": args.ip, "port": args.port},
            (),
        )
    if operation == "disconnect":
        return (
            ADB_DISCONNECT_PATH,
            "POST",
            "an ADB disconnection result",
            {"serial": args.serial},
            (),
        )
    if operation == "packages":
        query = urlencode(
            {
                "serial": args.serial,
                "third_party_only": "0" if args.all else "1",
            }
        )
        return (
            ADB_PACKAGES_PATH + "?" + query,
            "GET",
            "an installed package result",
            None,
            (),
        )
    if operation == "install":
        return (
            ADB_INSTALL_PATH,
            "POST",
            "an APK installation result",
            {
                "serial": args.serial,
                "apk_path": args.apk,
                "reinstall": not args.no_reinstall,
                "grant_permissions": args.grant_permissions,
            },
            (),
        )
    if operation == "launch":
        return (
            ADB_LAUNCH_PATH,
            "POST",
            "an application launch result",
            {"serial": args.serial, "package": args.package},
            (),
        )
    if operation == "stop":
        return (
            ADB_STOP_PATH,
            "POST",
            "an application stop result",
            {"serial": args.serial, "package": args.package},
            (),
        )
    if operation == "clear-data":
        return (
            ADB_CLEAR_DATA_PATH,
            "POST",
            "an application data-clear result",
            {"serial": args.serial, "package": args.package},
            (),
        )
    if operation == "uninstall":
        return (
            ADB_UNINSTALL_PATH,
            "POST",
            "an application uninstall result",
            {
                "serial": args.serial,
                "package": args.package,
                "keep_data": args.keep_data,
            },
            (),
        )
    raise ADBCLIError(f"unknown ADB operation: {operation}")


def _run(args: argparse.Namespace) -> int:
    path, method, description, body, sensitive_values = _operation_request(args)
    api_url, token = base_cli._resolve_client_settings(args)
    result = _request_api_data(
        api_url,
        path,
        method=method,
        token=token,
        timeout=args.timeout,
        correlation_prefix=f"cli-devhub-adb-{args.operation}",
        payload_description=description,
        body=body,
        sensitive_values=sensitive_values,
    )
    return base_cli._emit_json_result(
        args,
        result,
        token=token,
        description=description,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except base_cli.CLIError as exc:
        parser.exit(1, f"vr-hotspot-adb: error: {exc}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
