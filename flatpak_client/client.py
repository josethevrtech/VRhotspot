"""Small, read-only HTTP client for the local VRhotspot daemon API."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    build_opener,
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
)
import uuid


DEFAULT_BASE_URL = "http://127.0.0.1:8732"

_HEALTH_PATH = "/healthz"
_PREFLIGHT_PATH = "/v1/diagnostics/preflight"
_ADAPTER_READINESS_PATH = "/v1/adapters/readiness"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_ERROR_BODY_BYTES = 4_096
_MAX_ERROR_SNIPPET_CHARS = 256
_REDACTED = "[redacted]"
_AUTH_HEADER_NAMES = frozenset({"authorization", "x-api-token"})


class LocalApiClientError(RuntimeError):
    """Base class for expected, sanitized client failures."""


class InvalidBaseUrlError(LocalApiClientError):
    """The configured daemon origin is not a loopback-only HTTP origin."""


class ConnectionFailure(LocalApiClientError):
    """The local daemon could not be reached."""


class RedirectRejectedError(LocalApiClientError):
    """The daemon returned a redirect, which the client never follows."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(
            f"Local daemon API redirect rejected (HTTP {status}); redirects are not allowed."
        )


class AuthenticationError(LocalApiClientError):
    """The daemon rejected the explicit API token."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Local daemon API authentication failed (HTTP {status}).")


class DaemonTokenMissingError(LocalApiClientError):
    """The daemon is fail-closed because it has no configured API token."""

    def __init__(self):
        self.status = 503
        self.result_code = "api_token_missing"
        super().__init__(
            "The local daemon has no configured API token "
            "(HTTP 503; result_code=api_token_missing)."
        )


class DaemonApiError(LocalApiClientError):
    """A non-success HTTP status or API result code from the daemon."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        result_code: Optional[str] = None,
        body_snippet: str = "",
    ):
        self.status = status
        self.result_code = result_code
        self.body_snippet = body_snippet
        super().__init__(message)


class InvalidJsonError(LocalApiClientError):
    """The daemon returned a response that was not valid JSON."""


class InvalidResponseError(LocalApiClientError):
    """The daemon returned JSON that did not match the response contract."""


class ResponseTooLargeError(LocalApiClientError):
    """The daemon returned more data than this prototype accepts."""


@dataclass(frozen=True, repr=False)
class HttpRequest:
    """A GET request passed to the injectable transport."""

    url: str
    method: str
    headers: Mapping[str, str]
    timeout: float

    def __repr__(self) -> str:
        safe_headers = {
            key: (_REDACTED if key.lower() in _AUTH_HEADER_NAMES else value)
            for key, value in self.headers.items()
        }
        return (
            "HttpRequest("
            f"url={self.url!r}, method={self.method!r}, "
            f"headers={safe_headers!r}, timeout={self.timeout!r})"
        )


@dataclass(frozen=True, repr=False)
class HttpResponse:
    """A bounded response returned by the injectable transport."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    body_truncated: bool = False

    def __repr__(self) -> str:
        header_names = tuple(sorted(str(key).lower() for key in self.headers))
        return (
            "HttpResponse("
            f"status={self.status!r}, header_names={header_names!r}, "
            f"body_bytes={len(self.body)!r}, body_truncated={self.body_truncated!r})"
        )


@dataclass(frozen=True, repr=False)
class ApiResponse:
    """The validated daemon envelope returned by a read-only JSON method."""

    correlation_id: str
    result_code: str
    warnings: Tuple[str, ...]
    data: Dict[str, Any]

    def __repr__(self) -> str:
        return (
            "ApiResponse("
            f"correlation_id={self.correlation_id!r}, "
            f"result_code={self.result_code!r}, "
            f"warning_count={len(self.warnings)!r}, "
            f"data_keys={tuple(sorted(self.data))!r})"
        )


class Transport(Protocol):
    """Minimal injectable transport used by the offline unit tests."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send one request without following redirects."""


class _RedirectSignal(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(status)


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Stop redirects before urllib can forward authentication headers."""

    def _reject(self, _request, response, status, _message, _headers):
        try:
            response.close()
        except OSError:
            pass
        raise _RedirectSignal(status)

    http_error_301 = _reject
    http_error_302 = _reject
    http_error_303 = _reject
    http_error_307 = _reject
    http_error_308 = _reject


def _read_bounded(stream, limit: int) -> Tuple[bytes, bool]:
    raw = stream.read(limit + 1)
    return raw[:limit], len(raw) > limit


class UrlLibTransport:
    """Standard-library HTTP transport with proxies and redirects disabled."""

    def __init__(self, opener: Optional[OpenerDirector] = None):
        self._opener = opener or build_opener(ProxyHandler({}), _RejectRedirectHandler())

    def __repr__(self) -> str:
        return "UrlLibTransport(redirects=False, proxies=False)"

    def send(self, request: HttpRequest) -> HttpResponse:
        urllib_request = Request(
            request.url,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(urllib_request, timeout=request.timeout) as response:
                body, truncated = _read_bounded(response, _MAX_RESPONSE_BYTES)
                return HttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=body,
                    body_truncated=truncated,
                )
        except _RedirectSignal as exc:
            return HttpResponse(status=exc.status)
        except HTTPError as exc:
            try:
                body, truncated = _read_bounded(exc, _MAX_ERROR_BODY_BYTES)
                headers = dict(exc.headers.items()) if exc.headers else {}
                return HttpResponse(
                    status=int(exc.code),
                    headers=headers,
                    body=body,
                    body_truncated=truncated,
                )
            finally:
                exc.close()


def _validated_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidBaseUrlError("Base URL must be a loopback-only HTTP origin.")

    parsed = urlsplit(value.strip())
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise InvalidBaseUrlError(
            "Base URL contains an invalid loopback host or port."
        ) from None

    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise InvalidBaseUrlError(
            "Base URL must be an origin such as http://127.0.0.1:8732."
        )

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        raise InvalidBaseUrlError(
            "Base URL host must be an IPv4 or IPv6 loopback address."
        ) from None
    if not address.is_loopback:
        raise InvalidBaseUrlError("Base URL host must be a loopback address.")
    if port == 0:
        raise InvalidBaseUrlError("Base URL port must be between 1 and 65535.")

    rendered_host = (
        f"[{address.compressed}]" if address.version == 6 else address.compressed
    )
    rendered_port = f":{port}" if port is not None else ""
    return f"http://{rendered_host}{rendered_port}"


def _validated_token(value: str) -> str:
    if not isinstance(value, str):
        raise LocalApiClientError("API token must be supplied explicitly as text.")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise LocalApiClientError(
            "API token contains characters that are unsafe for an HTTP header."
        )
    return value


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool):
        raise LocalApiClientError("HTTP timeout must be a finite positive number.")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise LocalApiClientError(
            "HTTP timeout must be a finite positive number."
        ) from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise LocalApiClientError("HTTP timeout must be a finite positive number.")
    return timeout


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


class LocalApiClient:
    """GET-only client for the future unprivileged Flatpak control app."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[Transport] = None,
    ):
        self._base_url = _validated_base_url(base_url)
        self._token = _validated_token(token)
        self._timeout = _validated_timeout(timeout)
        self._transport = transport or UrlLibTransport()

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def token_configured(self) -> bool:
        return bool(self._token)

    def __repr__(self) -> str:
        return (
            "LocalApiClient("
            f"base_url={self._base_url!r}, timeout={self._timeout!r}, "
            f"token_configured={bool(self._token)!r}, "
            f"transport={type(self._transport).__name__})"
        )

    def health(self) -> bool:
        """Return true only for the daemon's exact unauthenticated health response."""

        response = self._get(_HEALTH_PATH, authenticated=False)
        self._raise_for_status(response)
        self._ensure_success_body_is_bounded(response)
        if response.body.strip() != b"ok":
            raise InvalidResponseError(
                "The local daemon returned an invalid health response."
            )
        return True

    def preflight_report(self) -> ApiResponse:
        """Fetch the daemon-owned canonical preflight report."""

        return self._get_api_response(_PREFLIGHT_PATH)

    def adapter_readiness(self) -> ApiResponse:
        """Fetch the daemon-owned adapter readiness model."""

        return self._get_api_response(_ADAPTER_READINESS_PATH)

    def _get_api_response(self, path: str) -> ApiResponse:
        response = self._get(path, authenticated=True)
        self._raise_for_status(response)
        self._ensure_success_body_is_bounded(response)
        return self._parse_envelope(response.body)

    def _get(self, path: str, *, authenticated: bool) -> HttpResponse:
        headers = {
            "Accept": "application/json",
            "User-Agent": "vrhotspot-flatpak-client-prototype",
            "X-Correlation-Id": f"flatpak-client-{uuid.uuid4()}",
        }
        if authenticated and self._token:
            headers["X-Api-Token"] = self._token
        request = HttpRequest(
            url=self._base_url + path,
            method="GET",
            headers=headers,
            timeout=self._timeout,
        )

        connection_error: Optional[ConnectionFailure] = None
        try:
            response = self._transport.send(request)
        except Exception as exc:
            reason = getattr(exc, "reason", exc) if isinstance(exc, URLError) else exc
            safe_detail = self._safe_text(reason)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                message = "Timed out while connecting to the local daemon API."
            else:
                message = "Unable to connect to the local daemon API."
                if safe_detail:
                    message += f" {safe_detail}"
            connection_error = ConnectionFailure(message)
            exc = None
            reason = None
        if connection_error is not None:
            raise connection_error
        if not isinstance(response, HttpResponse):
            raise InvalidResponseError(
                "The local API transport returned an invalid response object."
            )
        return response

    def _raise_for_status(self, response: HttpResponse) -> None:
        status = response.status
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            raise RedirectRejectedError(status)
        if status in {401, 403}:
            raise AuthenticationError(status)

        result_code = self._error_result_code(response.body)
        if status == 503 and result_code == "api_token_missing":
            raise DaemonTokenMissingError()

        snippet = self._error_body_snippet(response.body, response.body_truncated)
        detail_parts = [f"HTTP {status}"]
        if result_code:
            detail_parts.append(f"result_code={result_code}")
        message = f"Local daemon API request failed ({'; '.join(detail_parts)})."
        if snippet:
            message += f" Response: {snippet}"
        raise DaemonApiError(
            message,
            status=status,
            result_code=result_code,
            body_snippet=snippet,
        )

    def _ensure_success_body_is_bounded(self, response: HttpResponse) -> None:
        if response.body_truncated or len(response.body) > _MAX_RESPONSE_BYTES:
            raise ResponseTooLargeError(
                "The local daemon API response exceeded the client size limit."
            )

    def _parse_envelope(self, body: bytes) -> ApiResponse:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InvalidJsonError(
                "The local daemon API returned invalid JSON."
            ) from None
        if not isinstance(payload, Mapping):
            raise InvalidResponseError(
                "The local daemon API returned an invalid response envelope."
            )
        if _contains_secret(payload, self._token):
            raise InvalidResponseError(
                "The local daemon API reflected the authentication token; "
                "the response was discarded."
            )

        correlation_id = payload.get("correlation_id")
        result_code = payload.get("result_code")
        warnings = payload.get("warnings")
        data = payload.get("data")
        if (
            not isinstance(correlation_id, str)
            or not isinstance(result_code, str)
            or not isinstance(warnings, list)
            or not all(isinstance(warning, str) for warning in warnings)
            or not isinstance(data, Mapping)
        ):
            raise InvalidResponseError(
                "The local daemon API returned an invalid response envelope."
            )
        if result_code != "ok":
            safe_result_code = self._safe_text(result_code)
            raise DaemonApiError(
                f"The local daemon API returned result_code={safe_result_code}.",
                status=200,
                result_code=safe_result_code,
            )
        return ApiResponse(
            correlation_id=correlation_id,
            result_code=result_code,
            warnings=tuple(warnings),
            data=dict(data),
        )

    def _error_result_code(self, body: bytes) -> Optional[str]:
        try:
            payload = json.loads(body[:_MAX_ERROR_BODY_BYTES].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        result_code = payload.get("result_code")
        if not isinstance(result_code, str) or not result_code:
            return None
        return self._safe_text(result_code)

    def _error_body_snippet(self, body: bytes, was_truncated: bool) -> str:
        bounded = body[:_MAX_ERROR_BODY_BYTES]
        text = bounded.decode("utf-8", "replace")
        text = " ".join(text.split())
        text = self._redact(text)
        truncated = was_truncated or len(body) > _MAX_ERROR_BODY_BYTES
        if len(text) > _MAX_ERROR_SNIPPET_CHARS:
            text = text[:_MAX_ERROR_SNIPPET_CHARS]
            truncated = True
        return text + ("…" if text and truncated else "")

    def _safe_text(self, value: object) -> str:
        try:
            text = str(value)
        except Exception:
            text = type(value).__name__
        text = " ".join(text.split())
        text = self._redact(text)
        if len(text) > _MAX_ERROR_SNIPPET_CHARS:
            return text[:_MAX_ERROR_SNIPPET_CHARS] + "…"
        return text

    def _redact(self, text: str) -> str:
        if not self._token:
            return text
        variants = {
            self._token,
            json.dumps(self._token, ensure_ascii=True)[1:-1],
        }
        for variant in variants:
            if variant:
                text = text.replace(variant, _REDACTED)
        return text
