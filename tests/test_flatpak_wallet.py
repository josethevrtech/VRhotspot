import inspect
from pathlib import Path
import sys


from flatpak_app import build_smoke_payload, render_smoke_json
from flatpak_client import (
    ApiResponse,
    AuthenticationController,
    FirstRunState,
    SECRET_ATTRIBUTES,
    SECRET_SCHEMA_NAME,
    WalletUnavailableError,
)


class FakeWallet:
    def __init__(self, *, available=True, token=None, fail=False):
        self.is_available = available
        self.token = token
        self.fail = fail
        self.stored = []
        self.clear_calls = 0

    def available(self):
        if self.fail:
            raise RuntimeError("wallet failure with a-secret-value")
        return self.is_available

    def load(self):
        if self.fail or not self.is_available:
            raise WalletUnavailableError()
        return self.token

    def store(self, token):
        if self.fail or not self.is_available:
            raise WalletUnavailableError()
        self.token = token
        self.stored.append(token)

    def clear(self):
        if self.fail or not self.is_available:
            raise WalletUnavailableError()
        self.clear_calls += 1
        removed = self.token is not None
        self.token = None
        return removed


class AuthClient:
    def __init__(self, token):
        self.token_present = bool(token)

    def health(self):
        return True

    def adapter_readiness(self):
        if not self.token_present:
            raise AssertionError("authenticated request requires the token")
        return ApiResponse(
            correlation_id="wallet-test",
            result_code="ok",
            warnings=(),
            data={},
        )


def client_factory(*, token):
    if not isinstance(token, str):
        raise TypeError
    if any(ord(character) < 0x20 for character in token):
        from flatpak_client import LocalApiClientError

        raise LocalApiClientError("invalid token")
    return AuthClient(token)


def test_manual_token_is_retained_only_in_memory_when_not_saved():
    wallet = FakeWallet(token="old-wallet-token")
    controller = AuthenticationController(
        wallet=wallet,
        client_factory=client_factory,
    )

    result = controller.save_or_replace(
        "manual-session-token",
        save_securely=False,
    )

    assert result.code == "saved_in_memory"
    assert result.token_available is True
    assert result.securely_saved is False
    assert wallet.token is None
    assert controller.token_for_operation() == "manual-session-token"
    assert "manual-session-token" not in repr(controller)
    assert "manual-session-token" not in repr(result)


def test_token_can_be_saved_retrieved_and_replaced_through_fake_wallet():
    wallet = FakeWallet()
    first = AuthenticationController(
        wallet=wallet,
        client_factory=client_factory,
    )

    saved = first.save_or_replace("first-token", save_securely=True)
    replaced = first.save_or_replace("replacement-token", save_securely=True)
    second = AuthenticationController(
        wallet=wallet,
        client_factory=client_factory,
    )

    assert saved.code == "saved_securely"
    assert replaced.code == "saved_securely"
    assert wallet.stored == ["first-token", "replacement-token"]
    assert second.token_for_operation() == "replacement-token"
    assert second.securely_saved is True


def test_clear_removes_only_the_vr_hotspot_wallet_entry_and_memory_copy():
    wallet = FakeWallet(token="saved-token")
    controller = AuthenticationController(
        wallet=wallet,
        client_factory=client_factory,
    )
    assert controller.token_for_operation() == "saved-token"

    result = controller.clear()

    assert result.code == "cleared"
    assert wallet.clear_calls == 1
    assert wallet.token is None
    assert controller.token_for_operation() is None


def test_wallet_unavailable_falls_back_to_memory_only_with_fixed_message():
    secret = "memory-only-secret"
    controller = AuthenticationController(
        wallet=FakeWallet(available=False),
        client_factory=client_factory,
    )

    result = controller.save_or_replace(secret, save_securely=True)

    assert result.code == "wallet_unavailable"
    assert result.token_available is True
    assert result.securely_saved is False
    assert "memory" in result.message.casefold()
    assert secret not in result.message
    assert controller.token_for_operation() == secret


def test_memory_only_replace_reports_when_old_wallet_entry_cannot_be_removed():
    secret = "replacement-memory-secret"
    controller = AuthenticationController(
        wallet=FakeWallet(fail=True),
        client_factory=client_factory,
    )

    result = controller.save_or_replace(secret, save_securely=False)

    assert result.code == "saved_in_memory_wallet_unavailable"
    assert result.securely_saved is False
    assert "could not be checked or removed" in result.message
    assert secret not in result.message
    assert controller.token_for_operation() == secret


def test_copy_and_reveal_require_explicit_methods_and_do_not_auto_disclose():
    secret = "explicit-copy-reveal-token"
    controller = AuthenticationController(
        wallet=FakeWallet(token=secret),
        client_factory=client_factory,
    )

    automatic_surfaces = (
        repr(controller),
        str(controller),
        repr(controller.wallet_available()),
        repr(controller.securely_saved),
    )

    assert all(secret not in surface for surface in automatic_surfaces)
    assert controller.reveal_token() == secret
    assert controller.copy_token() == secret


def test_authentication_test_uses_saved_token_without_returning_it():
    secret = "saved-auth-test-token"
    controller = AuthenticationController(
        wallet=FakeWallet(token=secret),
        client_factory=client_factory,
    )

    result = controller.test_authentication()

    assert result.state is FirstRunState.TOKEN_ACCEPTED
    assert secret not in repr(result)
    assert secret not in result.message
    assert secret not in result.detail_code


def test_authentication_can_test_explicit_entry_without_saving_it():
    secret = "explicit-auth-test-token"
    controller = AuthenticationController(
        wallet=FakeWallet(),
        client_factory=client_factory,
    )

    result = controller.test_authentication(explicit_token=secret)

    assert result.state is FirstRunState.TOKEN_ACCEPTED
    assert controller.copy_token() is None
    assert secret not in repr(result)


def test_secret_service_schema_and_attributes_are_stable_and_non_secret():
    assert SECRET_SCHEMA_NAME == "io.github.josethevrtech.VRhotspot.ApiToken"
    assert SECRET_ATTRIBUTES == {
        "application": "io.github.josethevrtech.VRhotspot",
        "credential": "daemon-api-token",
    }
    assert set(SECRET_ATTRIBUTES) == {"application", "credential"}
    assert all("secret-value" not in value for value in SECRET_ATTRIBUTES.values())


def test_token_never_enters_smoke_json_urls_argv_logs_or_exceptions(
    caplog,
):
    secret = "never-export-this-api-token"
    wallet = FakeWallet(fail=True)
    controller = AuthenticationController(
        wallet=wallet,
        client_factory=client_factory,
    )
    result = controller.save_or_replace(secret, save_securely=True)
    clear_result = controller.clear()
    exposed = "\n".join(
        (
            repr(controller),
            repr(result),
            repr(clear_result),
            repr(build_smoke_payload()),
            render_smoke_json(),
            caplog.text,
            repr(sys.argv),
        )
    )

    assert secret not in exposed
    assert secret not in result.message
    assert secret not in clear_result.message


def test_wallet_and_tray_sources_never_read_privileged_token_files():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            Path("flatpak_client/wallet.py"),
            Path("flatpak_client/control.py"),
            Path("flatpak_app/tray.py"),
        )
    )

    assert "/etc/vr-hotspot/env" not in sources
    assert "/var/lib/vr-hotspot" not in sources
    assert "VR_HOTSPOTD_API_TOKEN" not in sources
    assert "subprocess" not in sources
    assert "systemctl" not in sources


def test_authentication_controller_has_no_implicit_print_or_export_method():
    public_methods = {
        name
        for name, value in inspect.getmembers(
            AuthenticationController,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert {
        "clear",
        "copy_token",
        "reveal_token",
        "save_or_replace",
        "test_authentication",
        "token_for_operation",
        "wallet_available",
    } <= public_methods
    assert public_methods.isdisjoint({"print_token", "export_token", "token_url"})
