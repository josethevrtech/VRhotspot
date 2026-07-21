"""Read-only command-line client for the VR Hotspot daemon API."""

from __future__ import annotations

import argparse
import errno
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid


DEFAULT_API_URL = "http://127.0.0.1:8732"
DEFAULT_ENV_FILE = "/etc/vr-hotspot/env"
PREFLIGHT_PATH = "/v1/diagnostics/preflight"

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
        "vr-hotspot preflight."
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
    except (UnicodeDecodeError, json.JSONDecodeError):
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
            raw = exc.read()
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


def fetch_preflight_report(
    api_url: str,
    *,
    token: str = "",
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Fetch and return only the canonical report from the API envelope."""

    endpoint = _validated_api_url(api_url) + PREFLIGHT_PATH
    token = _validated_token(token)
    headers = {
        "Accept": "application/json",
        "User-Agent": "vr-hotspot-cli",
        "X-Correlation-Id": f"cli-preflight-{uuid.uuid4()}",
    }
    if token:
        headers["X-Api-Token"] = token
    transport_exc: Optional[Exception] = None
    try:
        request = Request(endpoint, headers=headers, method="GET")
        with _open_preflight_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read()
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
    except (UnicodeDecodeError, json.JSONDecodeError):
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
        raise CLIError("The VR Hotspot API response did not contain a preflight report.")
    if _contains_secret(report, token):
        raise CLIError(
            "The VR Hotspot API returned a report containing the authentication token; "
            "refusing to print or export it."
        )
    return dict(report)


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vr-hotspot",
        description="Read-only client for the VR Hotspot daemon API.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser(
        "preflight",
        help="Print or export the daemon's canonical preflight diagnostics report.",
    )
    preflight_parser.add_argument(
        "--api-url",
        help=f"Daemon API base URL (default: {DEFAULT_API_URL}).",
    )
    token_group = preflight_parser.add_mutually_exclusive_group()
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
    preflight_parser.add_argument(
        "--env-file",
        default=os.environ.get("VR_HOTSPOTD_ENV_FILE", DEFAULT_ENV_FILE),
        help=f"Daemon environment file (default: {DEFAULT_ENV_FILE}).",
    )
    preflight_parser.add_argument(
        "--output",
        default="-",
        metavar="PATH",
        help=(
            "Write canonical report JSON to a new mode-0600 PATH; existing paths and "
            "symlinks are refused. Use - for stdout."
        ),
    )
    preflight_parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=15.0,
        metavar="SECONDS",
        help="HTTP request timeout (default: 15).",
    )
    return parser


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
                f"Unable to write preflight report to {safe_path}: {safe_error}"
            )
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if write_error is not None:
        raise write_error


def _run_preflight(args: argparse.Namespace) -> int:
    settings = _load_client_settings(Path(args.env_file))
    api_url = _validated_api_url(args.api_url) if args.api_url else _api_url_from_settings(settings)
    if args.token_stdin:
        token = _read_token_from_stdin()
    elif args.token is not None:
        token = args.token
    else:
        token = settings.get("VR_HOTSPOTD_API_TOKEN", "")
    report = fetch_preflight_report(api_url, token=token, timeout=args.timeout)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    if args.output == "-":
        sys.stdout.write(rendered)
        return 0

    output_path = Path(args.output)
    _write_new_private_file(output_path, rendered, token=token)
    safe_output_path = _redacted_error_text(output_path, token)
    sys.stderr.write(f"Wrote canonical preflight report to {safe_output_path}\n")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            return _run_preflight(args)
    except CLIError as exc:
        parser.exit(1, f"vr-hotspot: error: {exc}\n")
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
