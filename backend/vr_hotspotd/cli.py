"""Command-line diagnostics client for the VR Hotspot daemon API."""

from __future__ import annotations

import argparse
import errno
import getpass
import ipaddress
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid

from .diagnostics.streaming import MAX_REPORT_BYTES


DEFAULT_API_URL = "http://127.0.0.1:8732"
DEFAULT_ENV_FILE = "/etc/vr-hotspot/env"
PREFLIGHT_PATH = "/v1/diagnostics/preflight"
DEVBRIDGE_STATUS_PATH = "/v1/devbridge/status"
DEVBRIDGE_DEVICES_PATH = "/v1/devbridge/devices"
DEVBRIDGE_ADB_PATH = "/v1/devbridge/adb"
DEVBRIDGE_READINESS_PATH = "/v1/devbridge/readiness"
DEVBRIDGE_TOOLS_STATUS_PATH = "/v1/devbridge/tools/status"
STREAMING_PATH = "/v1/diagnostics/streaming"
STREAMING_RESPONSE_LIMIT = MAX_REPORT_BYTES + 65536

_ENV_KEYS = {
    "VR_HOTSPOTD_API_TOKEN",
    "VR_HOTSPOTD_API_URL",
    "VR_HOTSPOTD_HOST",
    "VR_HOTSPOTD_PORT",
}


class CLIError(RuntimeError):
    """Expected client-side or API error suitable for concise terminal output."""


def _redirect_error(status: int) -> CLIError:
    return CLIError(
        f"API request was redirected (HTTP {status}); redirects are not allowed for "
        "the vr-hotspot CLI."
    )


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Reject every redirect before urllib can forward authentication headers."""

    def _reject(self, _request, response, status, _message, _headers):
        try:
            response.close()
        except OSError:
            pass
        raise _redirect_error(status)

    http_error_301 = _reject
    http_error_302 = _reject
    http_error_303 = _reject
    http_error_307 = _reject
    http_error_308 = _reject


def _read_env_file(path: Path) -> Dict[str, str]:
    """Read the daemon's simple KEY=VALUE file without executing shell code."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return {}
    except OSError as exc:
        raise CLIError(f"Unable to read environment file {path}: {exc}") from exc

    values: Dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in _ENV_KEYS:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_client_settings(env_file: Path) -> Dict[str, str]:
    settings = _read_env_file(env_file)
    for key in _ENV_KEYS:
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def _validated_api_url(value: str) -> str:
    candidate = (value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise CLIError("API URL contains an invalid host or port.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise CLIError("API URL must be an absolute http:// or https:// URL.")
    if parsed.username is not None or parsed.password is not None:
        raise CLIError("API URL must not include user credentials.")
    if parsed.path not in {"", "/"}:
        raise CLIError("API URL must contain only the origin, without a path.")
    if parsed.query or parsed.fragment:
        raise CLIError("API URL must not include a query string or fragment.")
    return candidate


def _api_url_from_settings(settings: Mapping[str, str]) -> str:
    configured_url = settings.get("VR_HOTSPOTD_API_URL")
    if configured_url:
        return _validated_api_url(configured_url)

    host = (settings.get("VR_HOTSPOTD_HOST") or "127.0.0.1").strip()
    port_text = (settings.get("VR_HOTSPOTD_PORT") or "8732").strip()
    try:
        port = int(port_text)
    except ValueError as exc:
        raise CLIError(f"Invalid VR_HOTSPOTD_PORT value: {port_text}") from exc
    if not 1 <= port <= 65535:
        raise CLIError(f"Invalid VR_HOTSPOTD_PORT value: {port_text}")

    if host in {"", "0.0.0.0", "*"}:
        host = "127.0.0.1"
    elif host in {"::", "[::]"}:
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return _validated_api_url(f"http://{host}:{port}")


def _api_error_detail(raw: bytes, *, secret: str = "") -> Optional[str]:
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(payload, Mapping):
        return None
    result_code = payload.get("result_code")
    detail = str(result_code) if result_code else None
    if detail and secret and secret in detail:
        return None
    return detail


def _api_failure_cli_error(status: int, raw: bytes, *, token: str) -> CLIError:
    detail = _api_error_detail(raw, secret=token)
    suffix = f": {detail}" if detail else ""
    if detail == "api_token_missing":
        return CLIError(
            f"API request failed (HTTP {status}{suffix}). The daemon has no configured "
            f"API token; configure VR_HOTSPOTD_API_TOKEN in {DEFAULT_ENV_FILE} and "
            "restart vr-hotspotd."
        )
    auth_hint = (
        " Use VR_HOTSPOTD_API_TOKEN or --token-stdin, or run with permission "
        f"to read {DEFAULT_ENV_FILE}."
        if status in {401, 403}
        else ""
    )
    return CLIError(f"API request failed (HTTP {status}{suffix}).{auth_hint}")


def _contains_secret(value: Any, secret: str) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(
            _contains_secret(key, secret) or _contains_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item, secret) for item in value)
    return False


def _redacted_error_text(value: object, secret: str) -> str:
    try:
        text = str(value)
    except Exception:
        text = "unexpected error"
    return text.replace(secret, "[redacted]") if secret else text


def _validated_token(value: str) -> str:
    if not isinstance(value, str):
        raise CLIError("API token must be text.")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise CLIError("API token contains characters that are unsafe for an HTTP header.")
    return value


def _open_preflight_request(request: Request, *, timeout: float):
    opener = build_opener(_RejectRedirectHandler())
    return opener.open(request, timeout=timeout)


def _transport_cli_error(
    exc: Exception,
    *,
    endpoint: str,
    token: str,
) -> CLIError:
    if isinstance(exc, HTTPError):
        try:
            raw = exc.read(STREAMING_RESPONSE_LIMIT + 1)
        except Exception as read_exc:
            safe_error = _redacted_error_text(read_exc, token)
            return CLIError(f"Unable to read the VR Hotspot API response: {safe_error}")
        if 300 <= exc.code < 400:
            return _redirect_error(exc.code)
        return _api_failure_cli_error(exc.code, raw, token=token)
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", exc)
        safe_reason = _redacted_error_text(reason, token)
        return CLIError(f"Unable to reach the VR Hotspot API at {endpoint}: {safe_reason}")
    if isinstance(exc, TimeoutError):
        return CLIError(f"Timed out waiting for the VR Hotspot API at {endpoint}.")
    if isinstance(exc, OSError):
        safe_error = _redacted_error_text(exc, token)
        return CLIError(f"Unable to read the VR Hotspot API response: {safe_error}")
    return CLIError(
        "Unable to complete the VR Hotspot API request due to an unexpected "
        f"transport error ({type(exc).__name__})."
    )


def _fetch_api_data(
    api_url: str,
    path: str,
    *,
    token: str = "",
    timeout: float = 15.0,
    correlation_prefix: str = "cli",
    payload_description: str = "a response payload",
    method: str = "GET",
    request_data: Optional[Mapping[str, Any]] = None,
    max_response_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Request an endpoint and return only data from the API envelope."""

    endpoint = _validated_api_url(api_url) + path
    token = _validated_token(token)
    headers = {
        "Accept": "application/json",
        "User-Agent": "vr-hotspot-cli",
        "X-Correlation-Id": f"{correlation_prefix}-{uuid.uuid4()}",
    }
    if token:
        headers["X-Api-Token"] = token
    body = None
    if request_data is not None:
        body = json.dumps(request_data, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    transport_exc: Optional[Exception] = None
    try:
        request = Request(endpoint, data=body, headers=headers, method=method)
        with _open_preflight_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read(max_response_bytes + 1) if max_response_bytes is not None else response.read()
            if max_response_bytes is not None and len(raw) > max_response_bytes:
                raise CLIError("The VR Hotspot API response exceeded the size limit.")
    except CLIError:
        raise
    except Exception as exc:
        transport_exc = exc
    if transport_exc is not None:
        transport_error = _transport_cli_error(
            transport_exc,
            endpoint=endpoint,
            token=token,
        )
        transport_exc = None
        raise transport_error

    if 300 <= status < 400:
        raise _redirect_error(status)
    if status != 200:
        raise _api_failure_cli_error(status, raw, token=token)

    invalid_json = False
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        invalid_json = True
    if invalid_json:
        raise CLIError("The VR Hotspot API returned invalid JSON.")
    if not isinstance(payload, Mapping):
        raise CLIError("The VR Hotspot API returned an invalid response envelope.")
    result_code = payload.get("result_code")
    if result_code not in (None, "ok"):
        safe_result_code = _redacted_error_text(result_code, token)
        raise CLIError(f"The VR Hotspot API returned {safe_result_code}.")
    report = payload.get("data")
    if not isinstance(report, Mapping):
        raise CLIError(
            f"The VR Hotspot API response did not contain {payload_description}."
        )
    try:
        contains_token = _contains_secret(report, token)
    except RecursionError:
        raise CLIError("The VR Hotspot API returned an excessively nested response.") from None
    if contains_token:
        raise CLIError(
            "The VR Hotspot API returned a report containing the authentication token; "
            "refusing to print or export it."
        )
    return dict(report)


def fetch_preflight_report(
    api_url: str,
    *,
    token: str = "",
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Fetch and return only the canonical preflight report from the API envelope."""

    return _fetch_api_data(
        api_url,
        PREFLIGHT_PATH,
        token=token,
        timeout=timeout,
        correlation_prefix="cli-preflight",
        payload_description="a preflight report",
    )


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 120:
        raise argparse.ArgumentTypeError("timeout must be finite and greater than zero, up to 120 seconds")
    return timeout


def _add_client_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-url",
        help=f"Daemon API base URL (default: {DEFAULT_API_URL}).",
    )
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        metavar="TOKEN",
        help=(
            "API token (warning: visible in process arguments and shell history; prefer "
            "the daemon env file, VR_HOTSPOTD_API_TOKEN, or --token-stdin)."
        ),
    )
    token_group.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the API token from stdin without echoing it on an interactive terminal.",
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("VR_HOTSPOTD_ENV_FILE", DEFAULT_ENV_FILE),
        help=f"Daemon environment file (default: {DEFAULT_ENV_FILE}).",
    )
    parser.add_argument(
        "--output",
        default="-",
        metavar="PATH",
        help=(
            "Write the JSON result to a new mode-0600 PATH; existing paths and "
            "symlinks are refused. Use - for stdout."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=15.0,
        metavar="SECONDS",
        help="HTTP request timeout (default: 15).",
    )


def _validated_ipv4_argument(value: str) -> str:
    try:
        ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be an IPv4 address, for example 192.168.68.23"
        ) from exc
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vr-hotspot",
        description="VR Hotspot status and passive streaming diagnostics. Never changes radio settings.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser(
        "preflight",
        help="Print or export the daemon's canonical preflight diagnostics report.",
    )
    _add_client_arguments(preflight_parser)

    status_parser = commands.add_parser("status", help="Show the hotspot status without changing it.")
    _add_client_arguments(status_parser)

    diagnostics_parser = commands.add_parser("diagnostics", help="Passive VR streaming diagnostics.")
    diagnostic_commands = diagnostics_parser.add_subparsers(dest="diagnostics_command", required=True)
    streaming_parser = diagnostic_commands.add_parser(
        "streaming", help="Capture passive evidence without scans, traffic, or hotspot changes."
    )
    streaming_commands = streaming_parser.add_subparsers(dest="streaming_command", required=True)
    capture_parser = streaming_commands.add_parser("capture", help="Capture and export a streaming session.")
    _add_client_arguments(capture_parser)
    capture_parser.add_argument("--duration", type=_streaming_duration, default=120, metavar="SECONDS",
                                help="Capture duration: integer 10..600 seconds (default: 120).")
    capture_parser.add_argument("--detach", action="store_true",
                                help="Start the capture and return its ID immediately; export later with report.")
    streaming_status = streaming_commands.add_parser("status", help="Show the current or most recent capture.")
    _add_client_arguments(streaming_status)
    for name, help_text in (
        ("mark", "Mark a freeze in a running capture; no free-text or private notes are stored."),
        ("stop", "Stop a specific capture, retaining its report for export."),
        ("report", "Export a specific capture, including partial results while it is running."),
    ):
        command_parser = streaming_commands.add_parser(name, help=help_text)
        _add_client_arguments(command_parser)
        command_parser.add_argument("--capture-id", required=True, type=_capture_id_argument,
                                    metavar="UUID", help="The exact capture ID returned by capture or status.")

    devbridge_parser = commands.add_parser(
        "devbridge",
        help=(
            "Read-only ADB Dev Bridge helpers for standalone VR headset development. "
            "Never executes adb; only prints copyable commands."
        ),
    )
    devbridge_commands = devbridge_parser.add_subparsers(
        dest="devbridge_command",
        required=True,
    )

    devbridge_status_parser = devbridge_commands.add_parser(
        "status",
        help="Print the Dev Bridge status: hotspot state, subnet, and detected devices.",
    )
    _add_client_arguments(devbridge_status_parser)

    devbridge_scan_parser = devbridge_commands.add_parser(
        "scan",
        help=(
            "Discover devices on the hotspot network and check ADB TCP reachability "
            "on port 5555."
        ),
    )
    _add_client_arguments(devbridge_scan_parser)
    devbridge_scan_parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip the TCP reachability check on the ADB port.",
    )

    devbridge_adb_parser = devbridge_commands.add_parser(
        "adb-command",
        help=(
            "Print copyable adb connect and Wireless Debugging pairing commands; "
            "nothing is executed."
        ),
    )
    _add_client_arguments(devbridge_adb_parser)
    devbridge_adb_parser.add_argument(
        "--ip",
        type=_validated_ipv4_argument,
        metavar="IPV4",
        help="Target headset IPv4 address to generate commands for.",
    )

    devbridge_tools_parser = devbridge_commands.add_parser(
        "tools",
        help=(
            "Read-only Dev Bridge developer-tools helpers. Nothing is downloaded, "
            "installed, or executed."
        ),
    )
    devbridge_tools_commands = devbridge_tools_parser.add_subparsers(
        dest="devbridge_tools_command",
        required=True,
    )
    devbridge_tools_status_parser = devbridge_tools_commands.add_parser(
        "status",
        help=(
            "Print the Dev Bridge tools status: Platform-Tools pin metadata, "
            "managed/system adb discovery, and the effective adb source."
        ),
    )
    _add_client_arguments(devbridge_tools_status_parser)

    devbridge_logcat_parser = devbridge_commands.add_parser(
        "logcat-command",
        help="Print copyable logcat helper commands; logcat is never collected for you.",
    )
    _add_client_arguments(devbridge_logcat_parser)
    devbridge_logcat_parser.add_argument(
        "--ip",
        type=_validated_ipv4_argument,
        metavar="IPV4",
        help="Target headset IPv4 address to generate commands for.",
    )

    return parser


def _streaming_duration(value: str) -> int:
    try:
        duration = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("duration must be an integer from 10 to 600 seconds") from exc
    if not 10 <= duration <= 600:
        raise argparse.ArgumentTypeError("duration must be an integer from 10 to 600 seconds")
    return duration


def _capture_id_argument(value: str) -> str:
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError("capture ID must be a canonical UUID")
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, AttributeError):
        pass
    raise argparse.ArgumentTypeError("capture ID must be a canonical UUID")


def _read_token_from_stdin() -> str:
    read_failed = False
    try:
        if sys.stdin.isatty():
            token = getpass.getpass("VR Hotspot API token: ", stream=sys.stderr)
        else:
            token = sys.stdin.readline().rstrip("\r\n")
    except (EOFError, OSError, UnicodeError):
        read_failed = True
    if read_failed:
        raise CLIError("Unable to read the API token from stdin.")
    if not token:
        raise CLIError("No API token was provided on stdin.")
    return token


def _write_new_private_file(path: Path, rendered: str, *, token: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    safe_path = _redacted_error_text(path, token)
    descriptor: Optional[int] = None
    write_error: Optional[CLIError] = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        output_file = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with output_file:
            output_file.write(rendered)
    except FileExistsError:
        write_error = CLIError(
            "Output path already exists or is a symlink; refusing to overwrite it: "
            f"{safe_path}"
        )
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ELOOP}:
            write_error = CLIError(
                "Output path already exists or is a symlink; refusing to overwrite it: "
                f"{safe_path}"
            )
        else:
            safe_error = _redacted_error_text(exc, token)
            write_error = CLIError(
                f"Unable to write report to {safe_path}: {safe_error}"
            )
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if write_error is not None:
        raise write_error


def _resolve_client_settings(args: argparse.Namespace) -> Tuple[str, str]:
    settings = _load_client_settings(Path(args.env_file))
    api_url = _validated_api_url(args.api_url) if args.api_url else _api_url_from_settings(settings)
    if args.token_stdin:
        token = _read_token_from_stdin()
    elif args.token is not None:
        token = args.token
    else:
        token = settings.get("VR_HOTSPOTD_API_TOKEN", "")
    return api_url, token


def _emit_json_result(
    args: argparse.Namespace,
    report: Mapping[str, Any],
    *,
    token: str,
    description: str,
) -> int:
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    if args.output == "-":
        sys.stdout.write(rendered)
        return 0

    output_path = Path(args.output)
    _write_new_private_file(output_path, rendered, token=token)
    safe_output_path = _redacted_error_text(output_path, token)
    sys.stderr.write(f"Wrote {description} to {safe_output_path}\n")
    return 0


def _run_preflight(args: argparse.Namespace) -> int:
    api_url, token = _resolve_client_settings(args)
    report = fetch_preflight_report(api_url, token=token, timeout=args.timeout)
    return _emit_json_result(
        args,
        report,
        token=token,
        description="canonical preflight report",
    )


def _devbridge_request(args: argparse.Namespace) -> Tuple[str, str, str]:
    """Return (path, correlation prefix, payload description) for a devbridge command."""

    subcommand = args.devbridge_command
    if subcommand == "status":
        return DEVBRIDGE_STATUS_PATH, "cli-devbridge-status", "a Dev Bridge status report"
    if subcommand == "scan":
        path = DEVBRIDGE_DEVICES_PATH
        if args.no_probe:
            path += "?" + urlencode({"probe": "0"})
        return path, "cli-devbridge-scan", "a Dev Bridge device scan"
    if subcommand == "tools":
        tools_subcommand = getattr(args, "devbridge_tools_command", None)
        if tools_subcommand == "status":
            return (
                DEVBRIDGE_TOOLS_STATUS_PATH,
                "cli-devbridge-tools-status",
                "a Dev Bridge tools status report",
            )
        raise CLIError(f"unknown devbridge tools command: {tools_subcommand}")
    if subcommand in ("adb-command", "logcat-command"):
        kind = "connect" if subcommand == "adb-command" else "logcat"
        query = {"kind": kind}
        if args.ip:
            query["ip"] = args.ip
        return (
            DEVBRIDGE_ADB_PATH + "?" + urlencode(query),
            f"cli-devbridge-{kind}",
            "Dev Bridge copyable commands",
        )
    raise CLIError(f"unknown devbridge command: {subcommand}")


def _run_devbridge(args: argparse.Namespace) -> int:
    api_url, token = _resolve_client_settings(args)
    path, correlation_prefix, description = _devbridge_request(args)
    report = _fetch_api_data(
        api_url,
        path,
        token=token,
        timeout=args.timeout,
        correlation_prefix=correlation_prefix,
        payload_description=description,
    )
    return _emit_json_result(args, report, token=token, description=description)


def _streaming_request(api_url: str, token: str, timeout: float, *,
                       operation: str = "status", capture_id: Optional[str] = None,
                       duration: int = 120) -> Dict[str, Any]:
    path = STREAMING_PATH
    method = "GET"
    data = None
    if operation == "capture":
        method, data = "POST", {"duration_s": duration}
    elif operation in {"mark", "stop"}:
        path += "/" + operation
        method, data = "POST", {"capture_id": capture_id}
    elif operation == "report":
        path += "/report?" + urlencode({"capture_id": capture_id})
    return _fetch_api_data(
        api_url, path, token=token, timeout=timeout, correlation_prefix="cli-streaming",
        payload_description="a streaming capture", method=method, request_data=data,
        max_response_bytes=STREAMING_RESPONSE_LIMIT,
    )


def _run_streaming(args: argparse.Namespace) -> int:
    api_url, token = _resolve_client_settings(args)
    operation = args.streaming_command
    capture_id = getattr(args, "capture_id", None)
    if args.output != "-" and os.path.lexists(args.output):
        raise CLIError("Output path already exists or is a symlink; refusing to overwrite it: "
                       + _redacted_error_text(args.output, token))

    def request(op: str) -> Dict[str, Any]:
        return _streaming_request(api_url, token, args.timeout, operation=op,
                                  capture_id=capture_id, duration=getattr(args, "duration", 120))

    result = request(operation)
    if operation in {"mark", "stop", "report"} and result.get("capture_id") != capture_id:
        raise CLIError("The API returned a different capture ID; refusing to export another session.")
    if operation != "capture":
        return _emit_json_result(args, result, token=token, description="streaming capture")
    try:
        capture_id = _capture_id_argument(result.get("capture_id"))
    except argparse.ArgumentTypeError:
        raise CLIError("The API returned an invalid streaming capture ID.") from None
    if args.detach:
        return _emit_json_result(args, result, token=token, description="streaming capture")
    sys.stderr.write(f"Passive capture {capture_id} started. Ctrl-C stops and exports partial evidence.\n")
    cancelled = False
    try:
        deadline = time.monotonic() + args.duration + 60
        while result.get("state") == "running":
            if time.monotonic() >= deadline:
                raise CLIError("Capture did not finish within the expected time; use streaming stop/report "
                               f"with --capture-id {capture_id} to recover it.")
            time.sleep(1.0)
            result = request("status")
            if result.get("capture_id") != capture_id:
                raise CLIError("The active capture changed; refusing to operate on another session. "
                               f"Try streaming report --capture-id {capture_id}.")
    except KeyboardInterrupt:
        cancelled = True
        try:
            request("stop")
        except CLIError:
            raise CLIError("Unable to stop the capture after Ctrl-C. It is duration-bounded; "
                           f"recover it with streaming report --capture-id {capture_id}.") from None
    result = request("report")
    if result.get("capture_id") != capture_id:
        raise CLIError("The API returned a different capture ID; refusing to export another session.")
    _emit_json_result(args, result, token=token, description="partial streaming capture" if cancelled else "streaming capture")
    return 130 if cancelled else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            return _run_preflight(args)
        if args.command == "devbridge":
            return _run_devbridge(args)
        if args.command == "status":
            api_url, token = _resolve_client_settings(args)
            report = _fetch_api_data(api_url, "/v1/status", token=token, timeout=args.timeout,
                                     max_response_bytes=STREAMING_RESPONSE_LIMIT)
            return _emit_json_result(args, report, token=token, description="hotspot status")
        if args.command == "diagnostics" and args.diagnostics_command == "streaming":
            return _run_streaming(args)
    except CLIError as exc:
        parser.exit(1, f"vr-hotspot: error: {exc}\n")
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
