"""Token pairing and first-run state for the future Flatpak control app."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .client import (
    ApiResponse,
    AuthenticationError,
    ConnectionFailure,
    DaemonTokenMissingError,
    LocalApiClient,
    LocalApiClientError,
)


class FirstRunState(str, Enum):
    """Stable, non-secret outcomes a future first-run UI can render."""

    DAEMON_UNREACHABLE = "daemon_unreachable"
    DAEMON_REACHABLE_UNPAIRED = "daemon_reachable_unpaired"
    TOKEN_ACCEPTED = "token_accepted"
    TOKEN_REJECTED = "token_rejected"
    DAEMON_TOKEN_MISSING = "daemon_token_missing"
    INVALID_RESPONSE = "invalid_response"


_STATE_MESSAGES = {
    FirstRunState.DAEMON_UNREACHABLE: (
        "The local VRhotspot daemon could not be reached."
    ),
    FirstRunState.DAEMON_REACHABLE_UNPAIRED: (
        "The local daemon is reachable. Enter an API token to continue."
    ),
    FirstRunState.TOKEN_ACCEPTED: "The supplied API token was accepted.",
    FirstRunState.TOKEN_REJECTED: "The supplied API token was rejected.",
    FirstRunState.DAEMON_TOKEN_MISSING: (
        "The local daemon has no configured API token."
    ),
    FirstRunState.INVALID_RESPONSE: (
        "The local daemon returned an invalid or unsupported response."
    ),
}

_STATE_DETAIL_CODES = {
    FirstRunState.DAEMON_UNREACHABLE: "connection_failed",
    FirstRunState.DAEMON_REACHABLE_UNPAIRED: "token_required",
    FirstRunState.TOKEN_ACCEPTED: "authenticated_read_only_check_succeeded",
    FirstRunState.TOKEN_REJECTED: "authentication_failed",
    FirstRunState.DAEMON_TOKEN_MISSING: "api_token_missing",
    FirstRunState.INVALID_RESPONSE: "unexpected_daemon_response",
}


@dataclass(frozen=True, repr=False)
class FirstRunResult:
    """A fixed, token-free first-run result."""

    state: FirstRunState

    @property
    def message(self) -> str:
        return _STATE_MESSAGES[self.state]

    @property
    def detail_code(self) -> str:
        return _STATE_DETAIL_CODES[self.state]

    @property
    def paired(self) -> bool:
        return self.state is FirstRunState.TOKEN_ACCEPTED

    def __repr__(self) -> str:
        return f"FirstRunResult(state={self.state.value!r})"


class PairingClient(Protocol):
    """Read-only client behavior used by the first-run controller."""

    def health(self) -> bool:
        """Return whether the daemon's public health contract is valid."""

    def adapter_readiness(self) -> ApiResponse:
        """Call an authenticated, read-only endpoint."""


class PairingClientFactory(Protocol):
    """Create a local API client using a caller-supplied token."""

    def __call__(self, *, token: str) -> PairingClient:
        """Return a loopback-only, read-only client."""


class TokenPairingController:
    """Map daemon reachability and token validation to safe first-run state."""

    def __init__(self, client_factory: PairingClientFactory = LocalApiClient):
        self._client_factory = client_factory

    def __repr__(self) -> str:
        return "TokenPairingController(client_factory_configured=True)"

    def evaluate(self, *, token: str | None = None) -> FirstRunResult:
        """Evaluate first-run state without discovering or retaining a token."""

        health_result = self._check_health()
        if health_result is not None:
            return health_result

        if token is None or token == "":
            return FirstRunResult(FirstRunState.DAEMON_REACHABLE_UNPAIRED)
        if not isinstance(token, str):
            return FirstRunResult(FirstRunState.TOKEN_REJECTED)

        try:
            client = self._client_factory(token=token)
        except LocalApiClientError:
            return FirstRunResult(FirstRunState.TOKEN_REJECTED)
        except Exception:
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)

        try:
            response = client.adapter_readiness()
        except AuthenticationError:
            return FirstRunResult(FirstRunState.TOKEN_REJECTED)
        except DaemonTokenMissingError:
            return FirstRunResult(FirstRunState.DAEMON_TOKEN_MISSING)
        except ConnectionFailure:
            return FirstRunResult(FirstRunState.DAEMON_UNREACHABLE)
        except LocalApiClientError:
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)
        except Exception:
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)

        if not isinstance(response, ApiResponse):
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)
        return FirstRunResult(FirstRunState.TOKEN_ACCEPTED)

    def _check_health(self) -> FirstRunResult | None:
        try:
            client = self._client_factory(token="")
            reachable = client.health()
        except ConnectionFailure:
            return FirstRunResult(FirstRunState.DAEMON_UNREACHABLE)
        except Exception:
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)

        if reachable is not True:
            return FirstRunResult(FirstRunState.INVALID_RESPONSE)
        return None
