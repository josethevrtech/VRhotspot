import builtins
import inspect
import json
import os
from pathlib import Path

import pytest

from flatpak_client import (
    ApiResponse,
    ConnectionFailure,
    FirstRunState,
    HttpResponse,
    LocalApiClient,
    TokenPairingController,
)


def _envelope(data=None, **overrides):
    payload = {
        "correlation_id": "pairing-test-correlation-id",
        "result_code": "ok",
        "warnings": [],
        "data": data or {},
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class LocalClientFactory:
    def __init__(self, responses):
        self.transport = FakeTransport(responses)
        self.tokens = []

    def __call__(self, *, token):
        self.tokens.append(token)
        return LocalApiClient(token=token, transport=self.transport)


def _controller(*responses):
    factory = LocalClientFactory(responses)
    return TokenPairingController(factory), factory


def test_reachable_health_without_token_is_unpaired_not_paired():
    controller, factory = _controller(HttpResponse(status=200, body=b"ok\n"))

    result = controller.evaluate()

    assert result.state is FirstRunState.DAEMON_REACHABLE_UNPAIRED
    assert result.paired is False
    assert factory.tokens == [""]
    assert len(factory.transport.requests) == 1
    request = factory.transport.requests[0]
    assert request.url == "http://127.0.0.1:8732/healthz"
    assert "X-Api-Token" not in request.headers


def test_unreachable_daemon_has_a_distinct_first_run_state():
    controller, _factory = _controller(ConnectionRefusedError("offline"))

    result = controller.evaluate(token="supplied-token")

    assert result.state is FirstRunState.DAEMON_UNREACHABLE
    assert result.detail_code == "connection_failed"
    assert result.paired is False


def test_supplied_token_is_accepted_by_authenticated_read_only_endpoint():
    secret = "accepted-pairing-token"
    controller, factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        HttpResponse(
            status=200,
            body=_envelope({"summary": {"readiness_state": "ready"}}),
        ),
    )

    result = controller.evaluate(token=secret)

    assert result.state is FirstRunState.TOKEN_ACCEPTED
    assert result.paired is True
    assert factory.tokens == ["", secret]
    assert len(factory.transport.requests) == 2
    request = factory.transport.requests[1]
    assert request.url == "http://127.0.0.1:8732/v1/adapters/readiness"
    assert request.method == "GET"
    assert request.headers["X-Api-Token"] == secret


def test_supplied_token_rejected_on_401():
    controller, _factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        HttpResponse(
            status=401,
            body=_envelope(result_code="unauthorized"),
        ),
    )

    result = controller.evaluate(token="rejected-pairing-token")

    assert result.state is FirstRunState.TOKEN_REJECTED
    assert result.detail_code == "authentication_failed"
    assert result.paired is False


def test_daemon_token_missing_503_has_a_distinct_first_run_state():
    controller, _factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        HttpResponse(
            status=503,
            body=_envelope(
                result_code="api_token_missing",
                warnings=["api_token_not_configured"],
            ),
        ),
    )

    result = controller.evaluate(token="caller-supplied-token")

    assert result.state is FirstRunState.DAEMON_TOKEN_MISSING
    assert result.detail_code == "api_token_missing"
    assert result.paired is False


@pytest.mark.parametrize(
    "body",
    (
        b'{"result_code":"ok","data":',
        json.dumps({"result_code": "ok", "data": {}}).encode("utf-8"),
    ),
)
def test_invalid_json_or_envelope_maps_to_safe_invalid_response(body):
    controller, _factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        HttpResponse(status=200, body=body),
    )

    result = controller.evaluate(token="caller-supplied-token")

    assert result.state is FirstRunState.INVALID_RESPONSE
    assert result.detail_code == "unexpected_daemon_response"
    assert result.paired is False


def test_token_never_appears_in_controller_or_result_surfaces(caplog):
    secret = "pairing-secret-must-never-escape"

    class SecretFailingClient:
        def health(self):
            return True

        def adapter_readiness(self):
            raise RuntimeError(f"unexpected failure involving {secret}")

    class SecretFactory:
        def __call__(self, *, token):
            return SecretFailingClient()

        def __repr__(self):
            return f"SecretFactory(token={secret!r})"

    controller = TokenPairingController(SecretFactory())

    result = controller.evaluate(token=secret)

    exposed_surfaces = (
        repr(controller),
        str(controller),
        repr(result),
        str(result),
        result.state.value,
        result.message,
        result.detail_code,
        caplog.text,
    )
    assert result.state is FirstRunState.INVALID_RESPONSE
    assert all(secret not in surface for surface in exposed_surfaces)


def test_token_is_not_persisted_or_read_from_files(monkeypatch, tmp_path):
    secret = "in-memory-only-pairing-token"
    controller, factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        HttpResponse(status=200, body=_envelope({"readiness": "ready"})),
    )
    monkeypatch.chdir(tmp_path)
    before = list(tmp_path.rglob("*"))

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("pairing flow must not access files")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    monkeypatch.setattr(os, "open", forbidden_open)
    monkeypatch.setattr(Path, "open", forbidden_open)
    monkeypatch.setattr(Path, "read_text", forbidden_open)
    monkeypatch.setattr(Path, "read_bytes", forbidden_open)
    monkeypatch.setattr(Path, "write_text", forbidden_open)
    monkeypatch.setattr(Path, "write_bytes", forbidden_open)

    result = controller.evaluate(token=secret)

    assert result.state is FirstRunState.TOKEN_ACCEPTED
    assert list(tmp_path.rglob("*")) == before
    assert not hasattr(controller, "token")
    assert not hasattr(controller, "_token")
    assert factory.tokens == ["", secret]


def test_pairing_flow_wires_only_health_and_adapter_readiness():
    calls = []

    class ReadOnlyFakeClient:
        def __init__(self, token):
            self.token = token

        def health(self):
            calls.append(("health", bool(self.token)))
            return True

        def adapter_readiness(self):
            calls.append(("adapter_readiness", bool(self.token)))
            return ApiResponse(
                correlation_id="pairing-test-correlation-id",
                result_code="ok",
                warnings=(),
                data={},
            )

    controller = TokenPairingController(
        lambda *, token: ReadOnlyFakeClient(token)
    )

    result = controller.evaluate(token="explicit-token")

    public_client_methods = {
        name
        for name, value in inspect.getmembers(LocalApiClient, inspect.isfunction)
        if not name.startswith("_")
    }
    assert result.state is FirstRunState.TOKEN_ACCEPTED
    assert calls == [("health", False), ("adapter_readiness", True)]
    assert public_client_methods == {
        "health",
        "preflight_report",
        "adapter_readiness",
    }
    assert public_client_methods.isdisjoint(
        {
            "start",
            "stop",
            "restart",
            "repair",
            "save_config",
            "update_config",
            "request",
            "post",
        }
    )


def test_authenticated_connection_loss_does_not_return_paired():
    controller, _factory = _controller(
        HttpResponse(status=200, body=b"ok\n"),
        ConnectionFailure("connection lost"),
    )

    result = controller.evaluate(token="explicit-token")

    assert result.state is FirstRunState.DAEMON_UNREACHABLE
    assert result.paired is False
