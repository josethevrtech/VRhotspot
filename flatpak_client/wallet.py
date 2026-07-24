"""Explicit in-memory and Secret Service storage for the daemon API token."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .client import LocalApiClient, LocalApiClientError
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

    def __repr__(self) -> str:
        return (
            "AuthenticationController("
            f"token_available={bool(self._memory_token)!r}, "
            f"securely_saved={self._securely_saved!r})"
        )

    @property
    def securely_saved(self) -> bool:
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

        self._memory_token = token
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
            message="API token saved securely in the system wallet.",
            token_available=True,
            securely_saved=True,
        )

    def token_for_operation(self) -> str | None:
        if self._memory_token:
            return self._memory_token
        try:
            token = self._wallet.load()
        except Exception:
            self._securely_saved = False
            return None
        if not token:
            self._securely_saved = False
            return None
        try:
            self._client_factory(token=token)
        except Exception:
            return None
        self._memory_token = token
        self._securely_saved = True
        return token

    def test_authentication(
        self,
        *,
        explicit_token: str | None = None,
    ) -> FirstRunResult:
        token = (
            explicit_token
            if isinstance(explicit_token, str) and explicit_token
            else self.token_for_operation()
        )
        return TokenPairingController(self._client_factory).evaluate(token=token)

    def reveal_token(self) -> str | None:
        """Return the current token only for an explicit reveal action."""

        return self.token_for_operation()

    def copy_token(self) -> str | None:
        """Return the current token only for an explicit copy action."""

        return self.token_for_operation()

    def clear(self) -> AuthenticationResult:
        self._memory_token = ""
        removed = False
        try:
            removed = self._wallet.clear()
        except Exception:
            self._securely_saved = False
            return AuthenticationResult(
                code="cleared_memory_wallet_unavailable",
                message=(
                    "The in-memory token was cleared. Secure wallet storage "
                    "was unavailable."
                ),
                token_available=False,
                securely_saved=False,
            )
        self._securely_saved = False
        return AuthenticationResult(
            code="cleared",
            message=(
                "VR Hotspot's saved API token was cleared."
                if removed
                else "No saved VR Hotspot API token was present."
            ),
            token_available=False,
            securely_saved=False,
        )

    def authentication_state(self) -> FirstRunState:
        return self.test_authentication().state
