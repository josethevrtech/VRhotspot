"""Native stdin token pairing mode: bounded input, token-free output."""

import inspect
import json
import sys

import pytest

from flatpak_app import (
    MAX_PAIR_RESULT_JSON_BYTES,
    MAX_PAIR_TOKEN_STDIN_BYTES,
)
from flatpak_app import app
from flatpak_client import (
    ApiResponse,
    AuthenticationController,
    AuthenticationError,
    AuthenticationResult,
    FirstRunResult,
    FirstRunState,
    LocalApiClientError,
    WalletUnavailableError,
)


SECRET = "pair-stdin-secret-token-must-not-escape"
RESULT_KEYS = {"detail_code", "message", "ok", "paired", "saved"}


class FakeStdin:
    """A scripted stdin whose reads are recorded and bounded by the caller."""

    def __init__(self, data, *, interactive=False, isatty_error=False):
        self._data = data
        self._interactive = interactive
        self._isatty_error = isatty_error
        self.read_sizes = []

    def isatty(self):
        if self._isatty_error:
            raise OSError("stdin state unavailable")
        return self._interactive

    def read(self, size):
        self.read_sizes.append(size)
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


class FakeWallet:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.token = None
        self.stored = []

    def available(self):
        return not self.fail

    def load(self):
        if self.fail:
            raise WalletUnavailableError()
        return self.token

    def store(self, token):
        if self.fail:
            raise WalletUnavailableError()
        self.token = token
        self.stored.append(token)

    def clear(self):
        if self.fail:
            raise WalletUnavailableError()
        removed = self.token is not None
        self.token = None
        return removed


class AuthClient:
    def __init__(self, *, reject=False):
        self.reject = reject

    def health(self):
        return True

    def adapter_readiness(self):
        if self.reject:
            raise AuthenticationError(401)
        return ApiResponse(
            correlation_id="pair-stdin-test",
            result_code="ok",
            warnings=(),
            data={},
        )


def make_client_factory(*, reject=False):
    def factory(*, token):
        if not isinstance(token, str) or any(
            ord(character) < 0x20 or ord(character) > 0x7E for character in token
        ):
            raise LocalApiClientError("invalid token")
        return AuthClient(reject=reject)

    return factory


def make_controller_factory(*, wallet=None, reject=False):
    wallet = FakeWallet() if wallet is None else wallet

    def factory():
        return AuthenticationController(
            wallet=wallet,
            client_factory=make_client_factory(reject=reject),
        )

    return factory, wallet


def run_and_capture(capsys, **kwargs):
    exit_code = app.run_pair_token_stdin(**kwargs)
    captured = capsys.readouterr()
    return exit_code, captured, json.loads(captured.out)


def forbidden_controller_factory():
    pytest.fail("the authentication controller must not be constructed")


def test_pair_token_stdin_refuses_interactive_tty(capsys):
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n", interactive=True),
        controller_factory=forbidden_controller_factory,
    )

    assert exit_code == 2
    assert payload == {
        "detail_code": "interactive_input_refused",
        "message": payload["message"],
        "ok": False,
        "paired": False,
        "saved": False,
    }
    assert SECRET not in captured.out + captured.err


def test_pair_token_stdin_fails_closed_when_tty_state_is_unknown(capsys):
    exit_code, _captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n", isatty_error=True),
        controller_factory=forbidden_controller_factory,
    )

    assert exit_code == 2
    assert payload["detail_code"] == "interactive_input_refused"


def test_pair_token_stdin_reads_only_one_bounded_chunk(capsys):
    stream = FakeStdin("a" * (MAX_PAIR_TOKEN_STDIN_BYTES * 4))
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=stream,
        controller_factory=forbidden_controller_factory,
    )

    assert exit_code == 2
    assert payload["detail_code"] == "token_input_too_large"
    assert stream.read_sizes == [MAX_PAIR_TOKEN_STDIN_BYTES + 1]
    assert "a" * 20 not in captured.out


@pytest.mark.parametrize(
    ("data", "expected_code"),
    (
        ("", "token_input_empty"),
        ("\n", "token_input_empty"),
        ("\r\n", "token_input_empty"),
        ("first\nsecond\n", "token_input_invalid"),
        (b"\xff\xfe token", "token_input_invalid"),
    ),
)
def test_read_bounded_token_line_rejects_bad_payloads(data, expected_code):
    token, code = app.read_bounded_token_line(FakeStdin(data))

    assert token == ""
    assert code == expected_code


def test_read_bounded_token_line_accepts_one_line_variants():
    for payload in (SECRET, f"{SECRET}\n", f"{SECRET}\r\n", SECRET.encode("ascii")):
        token, code = app.read_bounded_token_line(FakeStdin(payload))
        assert (token, code) == (SECRET, "ok")


def test_read_bounded_token_line_reports_unreadable_stream():
    class BrokenStream:
        def isatty(self):
            return False

        def read(self, _size):
            raise OSError("read failure")

    token, code = app.read_bounded_token_line(BrokenStream())

    assert (token, code) == ("", "token_input_unavailable")


def test_pair_token_stdin_save_uses_native_controller_and_wallet(capsys):
    factory, wallet = make_controller_factory()
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n"),
        controller_factory=factory,
    )

    assert exit_code == 0
    assert payload == {
        "detail_code": "saved_securely",
        "message": "Authentication saved for this Flatpak desktop app.",
        "ok": True,
        "paired": True,
        "saved": True,
    }
    assert wallet.stored == [SECRET]
    assert SECRET not in captured.out + captured.err
    assert set(payload) == RESULT_KEYS
    assert len(captured.out.encode("utf-8")) <= MAX_PAIR_RESULT_JSON_BYTES + 1


def test_pair_token_stdin_validate_only_does_not_persist(capsys):
    factory, wallet = make_controller_factory()
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=False,
        input_stream=FakeStdin(f"{SECRET}\n"),
        controller_factory=factory,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["paired"] is True
    assert payload["saved"] is False
    assert payload["detail_code"] == "token_accepted"
    assert wallet.stored == []
    assert SECRET not in captured.out + captured.err


def test_pair_token_stdin_rejected_token_fails_closed(capsys):
    factory, wallet = make_controller_factory(reject=True)
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n"),
        controller_factory=factory,
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["paired"] is False
    assert payload["saved"] is False
    assert payload["detail_code"] == "token_rejected"
    assert wallet.stored == []
    assert SECRET not in captured.out + captured.err


def test_pair_token_stdin_wallet_failure_is_nonzero_and_token_free(capsys):
    factory, wallet = make_controller_factory(wallet=FakeWallet(fail=True))
    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n"),
        controller_factory=factory,
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["paired"] is True
    assert payload["saved"] is False
    assert payload["detail_code"] == "wallet_unavailable"
    assert wallet.stored == []
    assert SECRET not in captured.out + captured.err


def test_pair_token_stdin_controller_exception_never_leaks_token(capsys):
    class ExplodingController:
        def authenticate_and_save(self, token, *, save_securely):
            raise RuntimeError(f"unexpected failure carrying {token}")

    exit_code, captured, payload = run_and_capture(
        capsys,
        save=True,
        input_stream=FakeStdin(f"{SECRET}\n"),
        controller_factory=ExplodingController,
    )

    assert exit_code == 1
    assert payload["detail_code"] == "pairing_failed"
    assert SECRET not in captured.out + captured.err


def test_pair_token_stdin_nonconforming_controller_results_fail_closed(capsys):
    class WrongTypeController:
        def __init__(self, result):
            self._result = result

        def authenticate_and_save(self, _token, *, save_securely):
            return self._result

        def test_authentication(self, *, explicit_token=None):
            return self._result

    for save in (True, False):
        exit_code = app.run_pair_token_stdin(
            save=save,
            input_stream=FakeStdin(f"{SECRET}\n"),
            controller_factory=lambda: WrongTypeController({"paired": True}),
        )
        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        assert payload["detail_code"] == "pairing_failed"
        assert payload["ok"] is False


def test_pair_result_json_is_bounded_with_fixed_fallback():
    oversized = app.build_pair_token_result(
        ok=False,
        paired=False,
        saved=False,
        detail_code="x" * (MAX_PAIR_RESULT_JSON_BYTES + 1),
        message="",
    )
    rendered = app.render_pair_token_result_json(oversized)

    assert rendered == app._PAIR_TOKEN_FALLBACK_JSON
    assert json.loads(rendered)["ok"] is False
    assert app.render_pair_token_result_json({"ok": object()}) == (
        app._PAIR_TOKEN_FALLBACK_JSON
    )


def test_pair_token_flags_take_no_values_and_forbid_token_argv():
    parser = app._argument_parser()
    actions = {
        option: action
        for action in parser._actions
        for option in action.option_strings
    }

    assert actions["--pair-token-stdin"].nargs == 0
    assert actions["--save"].nargs == 0
    assert "--token" not in actions
    assert "--api-token" not in actions
    with pytest.raises(SystemExit):
        parser.parse_args([f"--pair-token-stdin={SECRET}"])
    with pytest.raises(SystemExit):
        parser.parse_args([f"--save={SECRET}"])


def test_pair_token_stdin_dispatches_without_importing_gtk(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        app,
        "run_pair_token_stdin",
        lambda *, save: calls.append(save) or 5,
    )
    monkeypatch.setitem(sys.modules, "gi", None)

    assert app.main(["--pair-token-stdin", "--save"]) == 5
    assert app.main(["--pair-token-stdin"]) == 5
    assert calls == [True, False]
    assert sys.modules.get("gi") is None
    capsys.readouterr()


def test_save_without_pair_mode_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        app.main(["--save"])

    assert excinfo.value.code == 2
    assert "--save requires --pair-token-stdin" in capsys.readouterr().err


def test_other_dispatch_modes_are_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run_tray", lambda: calls.append("tray") or 0)
    monkeypatch.setattr(
        app,
        "run_web_portal_shell",
        lambda: calls.append("portal") or 0,
    )
    monkeypatch.setattr(
        app,
        "run_pair_token_stdin",
        lambda **_kwargs: pytest.fail("pairing must not run"),
    )

    assert app.main(["--tray"]) == 0
    assert app.main([]) == 0
    assert app.main(["--web-portal-shell"]) == 0
    assert calls == ["tray", "portal", "portal"]


def test_pairing_path_has_no_web_or_storage_surface():
    source = "\n".join(
        inspect.getsource(value)
        for value in (
            app.read_bounded_token_line,
            app.build_pair_token_result,
            app.render_pair_token_result_json,
            app._emit_pair_token_result,
            app._pair_token_failure,
            app.run_pair_token_stdin,
        )
    )

    for forbidden in (
        "WebKit",
        "web_view",
        "WebPortalAuthBridge",
        "evaluate_javascript",
        "load_uri",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "IndexedDB",
        "os.environ",
        "getenv(",
        "/etc/",
        "/var/lib/",
        "subprocess",
        "logging",
    ):
        assert forbidden not in source


def test_web_bridge_schema_does_not_accept_a_pairing_message():
    for message_type in ("pair_token", "pair_token_stdin", "save_token"):
        payload = json.dumps({"version": 1, "type": message_type})
        assert app.WebPortalAuthBridge._parsed_message(payload) is None


def test_pair_results_reuse_token_free_result_types():
    assert AuthenticationResult("saved_securely", "m", True, True).securely_saved
    result = FirstRunResult(FirstRunState.TOKEN_ACCEPTED)
    assert result.paired is True
    assert "token" not in repr(result).replace("token_accepted", "")
