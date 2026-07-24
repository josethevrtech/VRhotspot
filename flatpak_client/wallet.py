"""Explicit in-memory and Secret Service storage for the daemon API token."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
from typing import Any, Mapping, Protocol

from .client import AuthenticationError, HttpResponse, LocalApiClient, LocalApiClientError
from .pairing import FirstRunResult, FirstRunState, TokenPairingController


SECRET_SCHEMA_NAME = "io.github.josethevrtech.VRhotspot.ApiToken"
SECRET_LABEL = "VR Hotspot daemon API token"
SECRET_ATTRIBUTES = {
    "application": "io.github.josethevrtech.VRhotspot",
    "credential": "daemon-api-token",
}


class WalletUnavailableError(RuntimeError):
    """The desktop has no usable Secret Service provider."""

    def __init__(self):
        super().__init__("Secure wallet storage is unavailable.")


class WalletBackend(Protocol):
    """Narrow wallet contract used by the authentication controller."""

    def available(self) -> bool:
        """Return whether a Secret Service provider can be reached."""

    def load(self) -> str | None:
        """Load only VR Hotspot's saved daemon token."""

    def store(self, token: str) -> None:
        """Save or replace only VR Hotspot's daemon token."""

    def clear(self) -> bool:
        """Remove only VR Hotspot's daemon token."""


class SecretServiceWalletBackend:
    """Lazy libsecret backend using one stable app-specific schema."""

    def __init__(self):
        self._secret = None
        self._schema = None

    def __repr__(self) -> str:
        return "SecretServiceWalletBackend(schema=app-specific, token_stored=False)"

    def _load(self):
        if self._secret is not None and self._schema is not None:
            return self._secret, self._schema
        try:
            import gi

            gi.require_version("Secret", "1")
            from gi.repository import Secret

            schema = Secret.Schema.new(
                SECRET_SCHEMA_NAME,
                Secret.SchemaFlags.NONE,
                {
                    "application": Secret.SchemaAttributeType.STRING,
                    "credential": Secret.SchemaAttributeType.STRING,
                },
            )
        except Exception:
            raise WalletUnavailableError() from None
        self._secret = Secret
        self._schema = schema
        return Secret, schema

    def available(self) -> bool:
        try:
            Secret, _schema = self._load()
            service = Secret.Service.get_sync(
                Secret.ServiceFlags.OPEN_SESSION,
                None,
            )
            return service is not None
        except Exception:
            return False

    def load(self) -> str | None:
        try:
            Secret, schema = self._load()
            value = Secret.password_lookup_sync(
                schema,
                dict(SECRET_ATTRIBUTES),
                None,
            )
        except Exception:
            raise WalletUnavailableError() from None
        return value if isinstance(value, str) and value else None

    def store(self, token: str) -> None:
        try:
            Secret, schema = self._load()
            stored = Secret.password_store_sync(
                schema,
                dict(SECRET_ATTRIBUTES),
                Secret.COLLECTION_DEFAULT,
                SECRET_LABEL,
                token,
                None,
            )
        except Exception:
            raise WalletUnavailableError() from None
        if stored is not True:
            raise WalletUnavailableError()

    def clear(self) -> bool:
        try:
            Secret, schema = self._load()
            return bool(
                Secret.password_clear_sync(
                    schema,
                    dict(SECRET_ATTRIBUTES),
                    None,
                )
            )
        except Exception:
            raise WalletUnavailableError() from None


@dataclass(frozen=True)
class AuthenticationResult:
    """Token-free result for authentication dialog actions."""

    code: str
    message: str
    token_available: bool
    securely_saved: bool


class AuthenticationController:
    """Own one session token and optional explicit Secret Service persistence."""

    def __init__(
        self,
        *,
        wallet: WalletBackend | None = None,
        client_factory=LocalApiClient,
    ):
        self._wallet = wallet if wallet is not None else SecretServiceWalletBackend()
        self._client_factory = client_factory
        self._memory_token = ""
        self._securely_saved = False
        self._validation_state: FirstRunState | None = None
        self._rejected_wallet_digest = ""
        self._state_lock = threading.RLock()

    def __repr__(self) -> str:
        return (
            "AuthenticationController("
            f"token_available={bool(self._memory_token)!r}, "
            f"securely_saved={self._securely_saved!r})"
        )

    @property
    def securely_saved(self) -> bool:
        with self._state_lock:
            return self._securely_saved

    def wallet_available(self) -> bool:
        try:
            return self._wallet.available() is True
        except Exception:
            return False

    def save_or_replace(
        self,
        token: str,
        *,
        save_securely: bool,
    ) -> AuthenticationResult:
        try:
            self._client_factory(token=token)
        except LocalApiClientError:
            return AuthenticationResult(
                code="invalid_token",
                message="Enter a non-empty API token using supported characters.",
                token_available=bool(self._memory_token),
                securely_saved=self._securely_saved,
            )
        except Exception:
            return AuthenticationResult(
                code="invalid_token",
                message="The API token could not be accepted.",
                token_available=bool(self._memory_token),
                securely_saved=self._securely_saved,
            )
        if not token:
            return AuthenticationResult(
                code="invalid_token",
                message="Enter the daemon API token.",
                token_available=bool(self._memory_token),
                securely_saved=self._securely_saved,
            )

        with self._state_lock:
            self._memory_token = token
            self._validation_state = None
            self._rejected_wallet_digest = ""
            if not save_securely:
                try:
                    self._wallet.clear()
                except Exception:
                    self._securely_saved = False
                    return AuthenticationResult(
                        code="saved_in_memory_wallet_unavailable",
                        message=(
                            "The API token is retained in memory for this session. "
                            "Secure wallet storage was unavailable, so an older "
                            "VR Hotspot wallet entry could not be checked or removed."
                        ),
                        token_available=True,
                        securely_saved=False,
                    )
                self._securely_saved = False
                return AuthenticationResult(
                    code="saved_in_memory",
                    message="API token retained in memory for this session only.",
                    token_available=True,
                    securely_saved=False,
                )

            try:
                self._wallet.store(token)
            except Exception:
                self._securely_saved = False
                return AuthenticationResult(
                    code="wallet_unavailable",
                    message=(
                        "Secure wallet persistence is unavailable; the API token "
                        "is retained in memory for this session only."
                    ),
                    token_available=True,
                    securely_saved=False,
                )

            self._securely_saved = True
            return AuthenticationResult(
                code="saved_securely",
                message="Authentication saved for this Flatpak desktop app.",
                token_available=True,
                securely_saved=True,
            )

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _token_for_operation_locked(self) -> str | None:
        if self._memory_token and not self._securely_saved:
            return self._memory_token

        try:
            token = self._wallet.load()
        except Exception:
            if self._securely_saved:
                self._memory_token = ""
                self._securely_saved = False
                self._validation_state = None
            return None
        if not token:
            if self._securely_saved:
                self._memory_token = ""
                self._securely_saved = False
                self._validation_state = None
            self._rejected_wallet_digest = ""
            return None

        digest = self._token_digest(token)
        if digest == self._rejected_wallet_digest:
            self._memory_token = ""
            self._securely_saved = False
            return None
        if token != self._memory_token or not self._securely_saved:
            self._memory_token = token
            self._securely_saved = True
            self._validation_state = None
        return self._memory_token

    def token_for_operation(self) -> str | None:
        with self._state_lock:
            token = self._token_for_operation_locked()
            if not token:
                return None
            try:
                self._client_factory(token=token)
            except Exception:
                self.discard_rejected_auth()
                return None
            return token

    def discard_rejected_auth(self) -> None:
        """Forget one rejected token and suppress repeat validation if clear fails."""

        with self._state_lock:
            token = self._memory_token
            was_securely_saved = self._securely_saved
            self._memory_token = ""
            self._securely_saved = False
            self._validation_state = FirstRunState.TOKEN_REJECTED
            self._rejected_wallet_digest = (
                self._token_digest(token) if token and was_securely_saved else ""
            )
            if was_securely_saved:
                try:
                    self._wallet.clear()
                except Exception:
                    pass
            token = ""

    def refresh_saved_auth(self) -> FirstRunResult:
        """Reload and validate app-wallet auth without disclosing the credential."""

        with self._state_lock:
            token = self._token_for_operation_locked()
            if not token:
                return FirstRunResult(FirstRunState.DAEMON_REACHABLE_UNPAIRED)
            if self._validation_state is FirstRunState.TOKEN_ACCEPTED:
                return FirstRunResult(FirstRunState.TOKEN_ACCEPTED)

        result = TokenPairingController(self._client_factory).evaluate(token=token)
        with self._state_lock:
            if token != self._memory_token:
                return FirstRunResult(FirstRunState.DAEMON_REACHABLE_UNPAIRED)
            if result.state is FirstRunState.TOKEN_REJECTED:
                self.discard_rejected_auth()
            elif result.state is FirstRunState.TOKEN_ACCEPTED:
                self._validation_state = FirstRunState.TOKEN_ACCEPTED
            token = ""
            return result

    def authenticate_and_save(
        self,
        token: str,
        *,
        save_securely: bool,
    ) -> AuthenticationResult:
        """Validate explicit native input before retaining or persisting it."""

        result = TokenPairingController(self._client_factory).evaluate(token=token)
        if result.state is not FirstRunState.TOKEN_ACCEPTED:
            messages = {
                FirstRunState.DAEMON_UNREACHABLE: (
                    "The local daemon is unavailable. Authentication was not saved."
                ),
                FirstRunState.TOKEN_REJECTED: (
                    "Authentication was rejected. Nothing was saved."
                ),
                FirstRunState.DAEMON_TOKEN_MISSING: (
                    "The daemon has no configured API token. Nothing was saved."
                ),
                FirstRunState.INVALID_RESPONSE: (
                    "Authentication could not be verified safely. Nothing was saved."
                ),
                FirstRunState.DAEMON_REACHABLE_UNPAIRED: (
                    "Enter the daemon API token."
                ),
            }
            return AuthenticationResult(
                code=result.state.value,
                message=messages.get(
                    result.state,
                    "Authentication could not be verified safely. Nothing was saved.",
                ),
                token_available=bool(self._memory_token),
                securely_saved=self._securely_saved,
            )

        saved = self.save_or_replace(token, save_securely=save_securely)
        with self._state_lock:
            if saved.code in {
                "saved_in_memory",
                "saved_in_memory_wallet_unavailable",
                "saved_securely",
                "wallet_unavailable",
            }:
                self._validation_state = FirstRunState.TOKEN_ACCEPTED
        return saved

    def test_authentication(
        self,
        *,
        explicit_token: str | None = None,
    ) -> FirstRunResult:
        if isinstance(explicit_token, str) and explicit_token:
            return TokenPairingController(self._client_factory).evaluate(
                token=explicit_token
            )
        return self.refresh_saved_auth()

    def reveal_token(self) -> str | None:
        """Return the current token only for an explicit reveal action."""

        return self.token_for_operation()

    def copy_token(self) -> str | None:
        """Return the current token only for an explicit copy action."""

        return self.token_for_operation()

    def clear(self) -> AuthenticationResult:
        with self._state_lock:
            self._memory_token = ""
            self._validation_state = None
            self._rejected_wallet_digest = ""
            removed = False
            try:
                removed = self._wallet.clear()
            except Exception:
                self._securely_saved = False
                return AuthenticationResult(
                    code="cleared_memory_wallet_unavailable",
                    message=(
                        "The in-memory authentication was cleared. Secure wallet "
                        "storage was unavailable."
                    ),
                    token_available=False,
                    securely_saved=False,
                )
            self._securely_saved = False
            return AuthenticationResult(
                code="cleared",
                message=(
                    "Saved authentication for this desktop was cleared."
                    if removed
                    else "No saved authentication for this desktop was present."
                ),
                token_available=False,
                securely_saved=False,
            )

    def request_local_api(
        self,
        path: str,
        *,
        method: str,
        body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        """Broker one allowlisted daemon request without returning the token."""

        token = self.token_for_operation()
        if not token:
            raise AuthenticationError(401)
        try:
            client = self._client_factory(token=token)
            response = client.portal_request(path, method=method, body=body)
        finally:
            token = ""
        if response.status in {401, 403}:
            self.discard_rejected_auth()
        return response

    def authentication_state(self) -> FirstRunState:
        return self.refresh_saved_auth().state
