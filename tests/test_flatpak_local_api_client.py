import inspect
import json

import pytest

from flatpak_client import (
    DEFAULT_BASE_URL,
    AuthenticationError,
    ConnectionFailure,
    DaemonApiError,
    DaemonTokenMissingError,
    HttpResponse,
    InvalidBaseUrlError,
    InvalidJsonError,
    InvalidResponseError,
    LocalApiClient,
    RedirectRejectedError,
)


def _envelope(data=None, **overrides):
    payload = {
        "correlation_id": "test-correlation-id",
        "result_code": "ok",
        "warnings": [],
        "data": data or {},
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class FakeTransport:
    def __init__(self, responses):
        if isinstance(responses, list):
            self.responses = list(responses)
        else:
            self.responses = [responses]
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_default_origin_is_loopback_only_and_health_is_unauthenticated():
    transport = FakeTransport(HttpResponse(status=200, body=b"ok\n"))
    client = LocalApiClient(token="explicit-token", transport=transport)

    assert client.base_url == DEFAULT_BASE_URL
    assert client.health() is True
    assert transport.requests[0].url == "http://127.0.0.1:8732/healthz"
    assert transport.requests[0].method == "GET"
    assert "X-Api-Token" not in transport.requests[0].headers


@pytest.mark.parametrize(
    ("include_logs", "path"),
    (
        (False, "/v1/status"),
        (True, "/v1/status?include_logs=1"),
    ),
)
def test_status_uses_only_fixed_privacy_compatible_routes(include_logs, path):
    transport = FakeTransport(
        HttpResponse(status=200, body=_envelope({"phase": "stopped"}))
    )
    client = LocalApiClient(token="status-token", transport=transport)

    client.status(include_logs=include_logs)

    request = transport.requests[0]
    assert request.url == f"http://127.0.0.1:8732{path}"
    assert request.method == "GET"
    assert request.body == b""
    assert "status-token" not in request.url


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:8732",
        "http://192.168.1.20:8732",
        "http://0.0.0.0:8732",
        "http://example.com:8732",
        "https://127.0.0.1:8732",
        "http://127.0.0.1:8732/v1",
        "http://user:password@127.0.0.1:8732",
        "http://127.0.0.1:0",
    ),
)
def test_non_loopback_or_non_origin_base_urls_are_rejected(base_url):
    with pytest.raises(InvalidBaseUrlError):
        LocalApiClient(token="explicit-token", base_url=base_url)


def test_ipv4_and_ipv6_loopback_origins_are_accepted_and_normalized():
    ipv4 = LocalApiClient(token="", base_url="http://127.0.0.2:9000/")
    ipv6 = LocalApiClient(token="", base_url="http://[0:0:0:0:0:0:0:1]:9000")

    assert ipv4.base_url == "http://127.0.0.2:9000"
    assert ipv6.base_url == "http://[::1]:9000"


def test_token_header_is_added_without_exposure_in_repr_or_transport_errors():
    secret = "flatpak-client-secret-token"

    class FailingTransport:
        def __init__(self):
            self.request = None

        def send(self, request):
            self.request = request
            raise RuntimeError(f"transport failed with {secret}; request={request!r}")

    transport = FailingTransport()
    client = LocalApiClient(token=secret, transport=transport)

    with pytest.raises(ConnectionFailure) as exc_info:
        client.preflight_report()

    assert transport.request.headers["X-Api-Token"] == secret
    assert secret not in repr(transport.request)
    assert secret not in repr(client)
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_redirect_is_rejected_without_a_followup_request():
    transport = FakeTransport(
        HttpResponse(
            status=302,
            headers={"Location": "http://127.0.0.1:9999/elsewhere"},
        )
    )
    client = LocalApiClient(token="explicit-token", transport=transport)

    with pytest.raises(RedirectRejectedError) as exc_info:
        client.preflight_report()

    assert exc_info.value.status == 302
    assert len(transport.requests) == 1


def test_read_only_json_methods_parse_and_preserve_daemon_envelopes():
    transport = FakeTransport(
        [
            HttpResponse(
                status=200,
                body=_envelope(
                    {"overall_readiness": "ready"},
                    warnings=["example_warning"],
                ),
            ),
            HttpResponse(
                status=200,
                body=_envelope(
                    {
                        "recommended": "wlan1",
                        "summary": {"readiness_state": "ready"},
                    }
                ),
            ),
        ]
    )
    client = LocalApiClient(token="explicit-token", transport=transport)

    preflight = client.preflight_report()
    readiness = client.adapter_readiness()

    assert preflight.correlation_id == "test-correlation-id"
    assert preflight.result_code == "ok"
    assert preflight.warnings == ("example_warning",)
    assert preflight.data == {"overall_readiness": "ready"}
    assert readiness.data["recommended"] == "wlan1"
    assert [request.url for request in transport.requests] == [
        "http://127.0.0.1:8732/v1/diagnostics/preflight",
        "http://127.0.0.1:8732/v1/adapters/readiness",
    ]
    assert all(request.method == "GET" for request in transport.requests)


@pytest.mark.parametrize("status", (401, 403))
def test_authentication_failures_have_a_distinct_safe_error(status):
    secret = "rejected-token"
    transport = FakeTransport(
        HttpResponse(
            status=status,
            body=_envelope(
                {"reflected": secret},
                result_code="unauthorized",
            ),
        )
    )
    client = LocalApiClient(token=secret, transport=transport)

    with pytest.raises(AuthenticationError) as exc_info:
        client.adapter_readiness()

    assert exc_info.value.status == status
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_missing_daemon_token_503_has_a_distinct_error():
    transport = FakeTransport(
        HttpResponse(
            status=503,
            body=_envelope(
                {"hint": "configure the daemon"},
                result_code="api_token_missing",
                warnings=["api_token_not_configured"],
            ),
        )
    )
    client = LocalApiClient(token="explicit-token", transport=transport)

    with pytest.raises(DaemonTokenMissingError) as exc_info:
        client.preflight_report()

    assert exc_info.value.status == 503
    assert exc_info.value.result_code == "api_token_missing"


def test_invalid_json_has_a_distinct_error_without_response_body_exposure():
    malformed = b'{"result_code":"ok","data":'
    transport = FakeTransport(HttpResponse(status=200, body=malformed))
    client = LocalApiClient(token="explicit-token", transport=transport)

    with pytest.raises(InvalidJsonError) as exc_info:
        client.preflight_report()

    assert malformed.decode("utf-8") not in str(exc_info.value)


def test_error_body_snippet_is_bounded_normalized_and_token_redacted():
    secret = "bounded-body-secret-token"
    body = (f"{secret}\n" + ("x" * 10_000)).encode("utf-8")
    transport = FakeTransport(HttpResponse(status=500, body=body))
    client = LocalApiClient(token=secret, transport=transport)

    with pytest.raises(DaemonApiError) as exc_info:
        client.preflight_report()

    error = exc_info.value
    assert error.status == 500
    assert len(error.body_snippet) <= 257
    assert error.body_snippet.endswith("…")
    assert "\n" not in error.body_snippet
    assert secret not in error.body_snippet
    assert secret not in str(error)
    assert "x" * 300 not in str(error)


def test_success_response_reflecting_token_is_discarded():
    secret = "must-not-enter-api-response"
    transport = FakeTransport(
        HttpResponse(status=200, body=_envelope({"reflected": secret}))
    )
    client = LocalApiClient(token=secret, transport=transport)

    with pytest.raises(InvalidResponseError) as exc_info:
        client.preflight_report()

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_client_exposes_only_reviewed_routes_and_no_generic_public_request_method():
    public_methods = {
        name
        for name, value in inspect.getmembers(LocalApiClient, inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == {
        "adapter_readiness",
        "config",
        "health",
        "preflight_report",
        "repair_network",
        "restart_service",
        "set_hotspot_autostart",
        "set_share_internet",
        "start_hotspot",
        "status",
        "stop_hotspot",
    }
    assert public_methods.isdisjoint(
        {
            "delete",
            "get",
            "patch",
            "post",
            "put",
            "request",
            "save_config",
            "start",
            "stop",
            "restart",
            "repair",
            "update_config",
        }
    )


@pytest.mark.parametrize(
    ("method_name", "result_code", "path"),
    (
        ("start_hotspot", "started", "/v1/start"),
        ("stop_hotspot", "stopped", "/v1/stop"),
        ("restart_service", "restarted:started", "/v1/restart"),
        ("repair_network", "repaired", "/v1/repair"),
    ),
)
def test_reviewed_lifecycle_methods_use_exact_authenticated_post_routes(
    method_name,
    result_code,
    path,
):
    secret = "exact-route-token"
    transport = FakeTransport(
        HttpResponse(
            status=200,
            body=_envelope({"phase": "running"}, result_code=result_code),
        )
    )
    client = LocalApiClient(token=secret, transport=transport)

    response = getattr(client, method_name)()

    assert response.result_code == result_code
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == f"http://127.0.0.1:8732{path}"
    assert request.method == "POST"
    assert json.loads(request.body) == {}
    assert request.headers["X-Api-Token"] == secret
    assert secret not in request.url
    assert secret not in repr(request)
    assert secret not in repr(response)


@pytest.mark.parametrize(
    ("method_name", "enabled", "path", "expected_body", "result_code"),
    (
        (
            "set_share_internet",
            False,
            "/v1/config",
            {"config": {"enable_internet": False}},
            "config_saved",
        ),
        (
            "set_hotspot_autostart",
            True,
            "/v1/autostart",
            {"enabled": True},
            "autostart_enabled",
        ),
    ),
)
def test_reviewed_boolean_settings_use_only_their_canonical_api_representation(
    method_name,
    enabled,
    path,
    expected_body,
    result_code,
):
    transport = FakeTransport(
        HttpResponse(
            status=200,
            body=_envelope(expected_body, result_code=result_code),
        )
    )
    client = LocalApiClient(token="setting-token", transport=transport)

    response = getattr(client, method_name)(enabled)

    request = transport.requests[0]
    assert response.result_code == result_code
    assert request.url == f"http://127.0.0.1:8732{path}"
    assert request.method == "POST"
    assert json.loads(request.body) == expected_body


@pytest.mark.parametrize(
    "method_name",
    ("set_share_internet", "set_hotspot_autostart"),
)
def test_reviewed_boolean_settings_reject_non_boolean_values(method_name):
    client = LocalApiClient(
        token="setting-token",
        transport=FakeTransport([]),
    )

    with pytest.raises(Exception) as exc_info:
        getattr(client, method_name)("true")

    assert "boolean" in str(exc_info.value).casefold()
