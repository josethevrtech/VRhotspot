import configparser
from dataclasses import asdict
import importlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import warnings
import xml.etree.ElementTree as ET

import pytest

from flatpak_client import (
    AuthenticationError,
    ConnectionFailure,
    DaemonTokenMissingError,
)


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
MANIFEST_PATH = Path("packaging/flatpak") / f"{APP_ID}.json"
DESKTOP_PATH = Path("packaging/flatpak") / f"{APP_ID}.desktop"
METAINFO_PATH = Path("packaging/flatpak") / f"{APP_ID}.metainfo.xml"
LAUNCHER_PATH = Path("packaging/flatpak/vrhotspot-flatpak")


def _api_response(data):
    from flatpak_client import ApiResponse

    return ApiResponse(
        correlation_id="token-entry-ui-test",
        result_code="ok",
        warnings=(),
        data=data,
    )


def _readiness_response():
    return _api_response(
        {
            "recommended": "wlan1",
            "basic_mode_recommended": "wlan1",
            "adapters": [
                {
                    "interface": "wlan1",
                    "driver": "example",
                    "bus_type": "usb",
                    "supports_5ghz": True,
                    "readiness_state": "ready",
                    "reason_codes": [
                        "supports_ap_mode",
                        "daemon_recommended",
                    ],
                    "explanation": "Ready for display-only diagnostics.",
                }
            ],
        }
    )


def _preflight_response():
    return _api_response(
        {
            "schema_version": 1,
            "overall_readiness": "warning",
            "platform": {},
            "firewall": {},
            "services": {},
            "network": {},
            "wifi": {"selected_adapter": "wlan1"},
            "issues": [
                {
                    "severity": "warning",
                    "code": "review_example",
                    "message": "Review the daemon-reported readiness.",
                }
            ],
            "recommended_actions": [
                {
                    "code": "review_example",
                    "message": "Review this display-only guidance.",
                }
            ],
        }
    )


class ScriptedReadOnlyClientFactory:
    def __init__(
        self,
        *,
        health_result=True,
        readiness_result=None,
        preflight_result=None,
    ):
        self.health_result = health_result
        self.readiness_result = readiness_result or _readiness_response()
        self.preflight_result = preflight_result or _preflight_response()
        self.token_presence = []

    def __repr__(self):
        return "ScriptedReadOnlyClientFactory(token_storage=False)"

    def __call__(self, *, token):
        self.token_presence.append(bool(token))
        factory = self

        class Client:
            def health(self):
                if isinstance(factory.health_result, BaseException):
                    raise factory.health_result
                return factory.health_result

            def adapter_readiness(self):
                if isinstance(factory.readiness_result, BaseException):
                    raise factory.readiness_result
                return factory.readiness_result

            def preflight_report(self):
                if isinstance(factory.preflight_result, BaseException):
                    raise factory.preflight_result
                return factory.preflight_result

        return Client()


class FakeInputStream:
    def __init__(self, *, interactive):
        self.interactive = interactive

    def isatty(self):
        return self.interactive


class FakeGtkWidget:
    def __init__(self, **_properties):
        self.children = []
        self.parent = None
        self.css_classes = []
        self.signal_handlers = {}
        self.sensitive = True

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def set_child(self, child):
        self.children = []
        self.append(child)

    def attach(self, child, *_position):
        self.append(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = siblings.index(self)
        return siblings[index + 1] if index + 1 < len(siblings) else None

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def add_css_class(self, css_class):
        self.css_classes.append(css_class)

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def connect(self, signal, handler):
        self.signal_handlers[signal] = handler

    def set_wrap(self, _wrap):
        pass

    def set_xalign(self, _alignment):
        pass

    def set_hexpand(self, _expand):
        pass

    def set_column_homogeneous(self, _homogeneous):
        pass

    def set_margin_top(self, _margin):
        pass

    def set_margin_bottom(self, _margin):
        pass

    def set_margin_start(self, _margin):
        pass

    def set_margin_end(self, _margin):
        pass

    def set_show_peek_icon(self, _show):
        pass


class FakeGtkApplication:
    def __init__(self, *, application_id):
        self.application_id = application_id
        self.signal_handlers = {}

    def connect(self, signal, handler):
        self.signal_handlers[signal] = handler

    def run(self, arguments):
        assert arguments == []
        self.signal_handlers["activate"](self)
        return 0


class FakeGtkApplicationWindow(FakeGtkWidget):
    def set_title(self, _title):
        pass

    def set_default_size(self, _width, _height):
        pass

    def present(self):
        pass


class FakeGtkScrolledWindow(FakeGtkWidget):
    def set_policy(self, _horizontal, _vertical):
        pass


class FakeGtkLabel(FakeGtkWidget):
    def __init__(self, *, label):
        super().__init__()
        self.label = label


class FakeGtkButton(FakeGtkWidget):
    def __init__(self, *, label):
        super().__init__()
        self.label = label


class FakeGtk:
    class Orientation:
        VERTICAL = "vertical"
        HORIZONTAL = "horizontal"

    class PolicyType:
        NEVER = "never"
        AUTOMATIC = "automatic"

    Application = FakeGtkApplication
    ApplicationWindow = FakeGtkApplicationWindow
    ScrolledWindow = FakeGtkScrolledWindow
    Box = FakeGtkWidget
    Frame = FakeGtkWidget
    Grid = FakeGtkWidget
    Label = FakeGtkLabel
    Button = FakeGtkButton
    PasswordEntry = FakeGtkWidget


def _walk_fake_widgets(widget):
    yield widget
    for child in widget.children:
        yield from _walk_fake_widgets(child)


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _smoke():
    return subprocess.run(
        [sys.executable, "-m", "flatpak_app", "--smoke-json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_app_shell_imports_without_importing_gtk(monkeypatch):
    monkeypatch.setitem(sys.modules, "gi", None)
    for module_name in tuple(sys.modules):
        if module_name == "flatpak_app" or module_name.startswith("flatpak_app."):
            del sys.modules[module_name]

    module = importlib.import_module("flatpak_app")

    assert module.APP_ID == APP_ID
    assert sys.modules.get("gi") is None


def test_gui_activation_does_not_require_password_entry_placeholder_setter(
    monkeypatch,
):
    from flatpak_app import app

    assert not hasattr(FakeGtk.PasswordEntry(), "set_placeholder_text")
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)

    assert app.run_gui() == 0


def test_placeholder_helper_prefers_direct_setter():
    from flatpak_app import app

    calls = []

    class Entry:
        def set_placeholder_text(self, value):
            calls.append(("direct", value))

        def set_property(self, name, value):
            calls.append((name, value))

    app._set_placeholder_text_compat(Entry(), "API token")

    assert calls == [("direct", "API token")]


def test_placeholder_helper_falls_back_to_supported_property_setter():
    from flatpak_app import app

    calls = []

    class Entry:
        def find_property(self, name):
            calls.append(("find", name))
            return object()

        def set_property(self, name, value):
            calls.append((name, value))

    app._set_placeholder_text_compat(Entry(), "API token")

    assert calls == [
        ("find", "placeholder-text"),
        ("placeholder-text", "API token"),
    ]


@pytest.mark.parametrize(
    "entry",
    (
        object(),
        type(
            "UnsupportedPropertyEntry",
            (),
            {
                "find_property": lambda self, _name: None,
                "set_property": lambda self, _name, _value: pytest.fail(
                    "unsupported property must not be set"
                ),
            },
        )(),
    ),
)
def test_placeholder_helper_noops_when_placeholder_is_unsupported(entry):
    from flatpak_app import app

    app._set_placeholder_text_compat(entry, "API token")


def test_smoke_json_exits_successfully_is_bounded_and_has_expected_sections():
    from flatpak_app import MAX_SMOKE_JSON_BYTES

    result = _smoke()
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stderr == ""
    assert len(result.stdout.encode("utf-8")) <= MAX_SMOKE_JSON_BYTES + 1
    assert set(payload) == {"application", "controls", "shell", "ui"}
    assert payload["application"] == {
        "id": APP_ID,
        "name": APP_NAME,
        "prototype": True,
    }
    assert payload["shell"] == {
        "graphical_shell": "gtk4_placeholder",
        "state": "offline_unpaired",
    }
    assert set(payload["ui"]) == {
        "mode",
        "show_technical_details",
        "daemon",
        "pairing",
        "adapters",
        "preflight",
        "support_bundle",
    }


def test_smoke_json_contains_no_secret_or_host_path_leak_markers():
    result = _smoke()
    rendered = result.stdout.lower()

    assert result.returncode == 0
    for forbidden in (
        "token",
        "passphrase",
        "password",
        "psk",
        "bearer",
        "file://",
        "/etc/",
        "/home/",
        "/run/",
        "/tmp/",
        "/var/",
    ):
        assert forbidden not in rendered


def test_live_pairing_smoke_command_dispatches_without_importing_gtk(monkeypatch):
    from flatpak_app import app

    calls = []

    def fake_live_smoke():
        calls.append("live_smoke")
        return 17

    monkeypatch.setattr(app, "run_live_pairing_smoke_json", fake_live_smoke)
    monkeypatch.setitem(sys.modules, "gi", None)

    assert app.main(["--live-pairing-smoke-json"]) == 17
    assert calls == ["live_smoke"]
    assert sys.modules.get("gi") is None


def test_live_smoke_refuses_noninteractive_input_with_token_free_json(capsys):
    from flatpak_app import app

    def forbidden_prompt(_prompt):
        raise AssertionError("noninteractive smoke must not prompt")

    def forbidden_factory(*, token):
        raise AssertionError("noninteractive smoke must not create a client")

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=False),
        token_prompt=forbidden_prompt,
        client_factory=forbidden_factory,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert captured.err == ""
    assert payload["live_smoke"]["status"] == "interactive_input_required"
    assert payload["daemon"]["reachable"] is None
    assert payload["pairing"]["paired"] is False
    assert payload["controls"]["mutation_actions"] == []
    assert payload["support_bundle"]["action_enabled"] is False


def test_live_smoke_success_returns_bounded_sanitized_ui_ready_json(capsys):
    from flatpak_app import MAX_LIVE_SMOKE_JSON_BYTES
    from flatpak_app import app

    secret = "live-smoke-success-value-must-not-escape"
    prompts = []
    factory = ScriptedReadOnlyClientFactory()

    def hidden_prompt(prompt):
        prompts.append(prompt)
        return secret

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=hidden_prompt,
        client_factory=factory,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert prompts == ["VRhotspot daemon API token: "]
    assert len(captured.out.encode("utf-8")) <= MAX_LIVE_SMOKE_JSON_BYTES + 1
    assert set(payload) == {
        "application",
        "live_smoke",
        "daemon",
        "pairing",
        "adapter_readiness",
        "preflight",
        "support_bundle",
        "controls",
    }
    assert payload["application"] == {"id": APP_ID, "name": APP_NAME}
    assert payload["live_smoke"]["status"] == "success"
    assert payload["daemon"]["reachable"] is True
    assert payload["pairing"]["paired"] is True
    assert payload["adapter_readiness"]["recommended_interface"] == "wlan1"
    assert payload["preflight"]["readiness_label"] == "Needs attention"
    assert payload["support_bundle"]["action_enabled"] is False
    assert payload["support_bundle"]["request_performed"] is False
    assert payload["controls"] == {
        "mutation_actions": [],
        "support_bundle_export_enabled": False,
    }
    assert factory.token_presence == [False, True, True]
    assert secret not in captured.out


@pytest.mark.parametrize(
    ("factory", "expected_status", "daemon_reachable", "pairing_title"),
    [
        (
            ScriptedReadOnlyClientFactory(
                readiness_result=AuthenticationError(401)
            ),
            "token_rejected",
            True,
            "Token rejected",
        ),
        (
            ScriptedReadOnlyClientFactory(
                health_result=ConnectionFailure("offline")
            ),
            "daemon_unreachable",
            False,
            "Pairing unavailable",
        ),
        (
            ScriptedReadOnlyClientFactory(
                readiness_result=DaemonTokenMissingError()
            ),
            "daemon_token_missing",
            True,
            "Daemon token missing",
        ),
        (
            ScriptedReadOnlyClientFactory(
                readiness_result={"unexpected": "response"}
            ),
            "invalid_response",
            None,
            "Pairing status unknown",
        ),
    ],
    ids=(
        "token-rejected",
        "daemon-unreachable",
        "daemon-token-missing",
        "malformed-pairing-response",
    ),
)
def test_live_smoke_failures_are_nonzero_and_token_free(
    capsys,
    factory,
    expected_status,
    daemon_reachable,
    pairing_title,
):
    from flatpak_app import app

    secret = f"live-smoke-{expected_status}-must-not-escape"

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: secret,
        client_factory=factory,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert captured.err == ""
    assert payload["live_smoke"]["status"] == expected_status
    assert payload["daemon"]["reachable"] is daemon_reachable
    assert payload["pairing"]["title"] == pairing_title
    assert payload["pairing"]["paired"] is False
    assert payload["controls"]["mutation_actions"] == []
    assert payload["support_bundle"]["action_enabled"] is False
    assert secret not in captured.out


def test_live_smoke_malformed_preflight_is_unknown_and_nonzero(capsys):
    from flatpak_app import app

    secret = "live-smoke-malformed-preflight-must-not-escape"
    factory = ScriptedReadOnlyClientFactory(
        preflight_result=_api_response(
            {
                "schema_version": 99,
                "overall_readiness": "future",
            }
        )
    )

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: secret,
        client_factory=factory,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["live_smoke"]["status"] == "invalid_response"
    assert payload["pairing"]["paired"] is True
    assert payload["preflight"]["severity"] == "unknown"
    assert payload["controls"]["mutation_actions"] == []
    assert secret not in captured.out


def test_live_smoke_drops_secret_and_host_path_values_from_json(capsys):
    from flatpak_app import app

    entered_token = "entered-live-token-value-must-not-escape"
    api_token_value = "daemon-api-token-value-must-not-escape"
    passphrase_value = "wifi-passphrase-value-must-not-escape"
    password_value = "daemon-password-value-must-not-escape"
    psk_value = "wifi-psk-value-must-not-escape"
    bearer_value = "bearer-value-must-not-escape"
    host_path = "/var/lib/vr-hotspot/private-state.json"
    factory = ScriptedReadOnlyClientFactory(
        readiness_result=_api_response(
            {
                "recommended": "wlan1",
                "basic_mode_recommended": "wlan1",
                "summary": {"readiness_state": "ready"},
                "adapters": [
                    {
                        "interface": "wlan1",
                        "readiness_state": "ready",
                        "explanation": (
                            f"token={api_token_value}; "
                            f"passphrase={passphrase_value}; "
                            f"password={password_value}; psk={psk_value}"
                        ),
                    }
                ],
            }
        ),
        preflight_result=_api_response(
            {
                "schema_version": 1,
                "overall_readiness": "warning",
                "platform": {},
                "firewall": {},
                "services": {},
                "network": {},
                "wifi": {},
                "issues": [
                    {
                        "severity": "warning",
                        "code": "redaction_check",
                        "message": (
                            f"Authorization: Bearer {bearer_value}; "
                            f"inspect {host_path}"
                        ),
                    }
                ],
                "recommended_actions": [],
            }
        ),
    )

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: entered_token,
        client_factory=factory,
    )
    captured = capsys.readouterr()
    rendered = captured.out

    assert exit_code == 0
    for forbidden_value in (
        entered_token,
        api_token_value,
        passphrase_value,
        password_value,
        psk_value,
        bearer_value,
        host_path,
    ):
        assert forbidden_value not in rendered
    assert "[redacted]" in rendered
    assert "[host path]" in rendered


def test_live_smoke_rejects_empty_or_unavailable_hidden_input_without_a_client(
    capsys,
):
    from flatpak_app import app

    client_calls = []

    def forbidden_factory(*, token):
        client_calls.append(token)
        raise AssertionError("empty or unavailable input must not create a client")

    empty_exit = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: "",
        client_factory=forbidden_factory,
    )
    empty_payload = json.loads(capsys.readouterr().out)

    def failed_prompt(_prompt):
        raise OSError("terminal input unavailable")

    cancelled_exit = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=failed_prompt,
        client_factory=forbidden_factory,
    )
    cancelled_payload = json.loads(capsys.readouterr().out)

    assert empty_exit == 2
    assert empty_payload["live_smoke"]["status"] == "token_input_empty"
    assert cancelled_exit == 2
    assert cancelled_payload["live_smoke"]["status"] == "token_input_cancelled"
    assert client_calls == []


def test_live_smoke_rejects_getpass_echo_fallback_before_reading_input(capsys):
    from flatpak_app import app

    secret = "fallback-must-not-read-or-echo-this-value"
    client_calls = []

    def fallback_prompt(_prompt):
        warnings.warn("hidden input unavailable", app.getpass.GetPassWarning)
        return secret

    def forbidden_factory(*, token):
        client_calls.append(token)
        raise AssertionError("unsafe prompt fallback must not create a client")

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=fallback_prompt,
        client_factory=forbidden_factory,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert captured.err == ""
    assert payload["live_smoke"]["status"] == "token_input_cancelled"
    assert secret not in captured.out
    assert client_calls == []


def test_token_entry_requires_only_a_caller_supplied_token():
    from flatpak_app import FirstRunTokenEntryController

    factory = ScriptedReadOnlyClientFactory()
    controller = FirstRunTokenEntryController(client_factory=factory)

    with pytest.raises(TypeError):
        controller.connect()

    model = controller.connect(token="caller-provided-value")

    assert model.pairing.paired is True
    assert factory.token_presence == [False, True, True]
    assert not hasattr(controller, "token")
    assert not hasattr(controller, "_token")


def test_successful_token_validation_updates_all_display_only_sections():
    from flatpak_app import FirstRunTokenEntryController

    controller = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory()
    )

    model = controller.connect(token="accepted-in-memory-value")

    assert model.daemon.title == "Daemon connected"
    assert model.pairing.title == "Paired"
    assert model.adapters.recommended_interface == "wlan1"
    assert [card.interface for card in model.adapters.cards] == ["wlan1"]
    assert model.preflight.readiness_label == "Needs attention"
    assert [issue.code for issue in model.preflight.issues] == ["review_example"]
    assert [action.code for action in model.preflight.actions] == ["review_example"]
    assert model.preflight.actions[0].interactive is False
    assert model.support_bundle.action_enabled is False
    assert model.support_bundle.request_performed is False


def test_successful_pairing_builds_and_renders_all_native_dashboard_sections():
    from flatpak_app import FirstRunTokenEntryController, build_dashboard_model
    from flatpak_app import app

    display_model = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory()
    ).connect(token="accepted-dashboard-value")

    dashboard = build_dashboard_model(display_model)
    container = FakeGtkWidget()
    app._render_dashboard_model(FakeGtk, container, dashboard)

    assert set(asdict(dashboard)) == {
        "daemon",
        "pairing",
        "adapter_readiness",
        "preflight",
        "support_bundle",
        "controls",
    }
    assert dashboard.adapter_readiness.recommended_interface == "wlan1"
    assert dashboard.adapter_readiness.cards[0].readiness_label == "Ready"
    assert dashboard.adapter_readiness.cards[0].severity.value == "ok"
    assert dashboard.adapter_readiness.cards[0].summary
    assert dashboard.adapter_readiness.cards[0].reasons == (
        "Supports Ap Mode",
        "Daemon Recommended",
    )
    assert dashboard.preflight.readiness_label == "Needs attention"
    assert dashboard.preflight.severity.value == "warning"
    assert dashboard.preflight.summary
    assert dashboard.preflight.issues
    assert dashboard.preflight.facts
    assert dashboard.preflight.actions
    assert all(not action.interactive for action in dashboard.preflight.actions)
    assert dashboard.support_bundle.visible is True
    assert dashboard.support_bundle.action_enabled is False
    assert dashboard.controls.visible is True
    assert dashboard.controls.mutation_actions == ()
    assert dashboard.controls.action_enabled is False

    widgets = tuple(_walk_fake_widgets(container))
    rendered_text = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    buttons = [
        widget for widget in widgets if isinstance(widget, FakeGtkButton)
    ]
    for section_title in (
        "Daemon status",
        "Pairing status",
        "Adapter readiness",
        "Preflight diagnostics",
        "Support bundle",
        "Controls boundary",
    ):
        assert section_title in rendered_text
    assert "Recommended interface: wlan1" in rendered_text
    assert "Readiness: Ready" in rendered_text
    assert "Severity: OK" in rendered_text
    assert "Reasons" in rendered_text
    assert "Facts" in rendered_text
    assert "Issues" in rendered_text
    assert "Noninteractive actions" in rendered_text
    assert "Mutation actions: none" in rendered_text
    assert [(button.label, button.sensitive) for button in buttons] == [
        ("Export support bundle", False)
    ]


def test_token_entry_is_cleared_before_dashboard_validation():
    from flatpak_app import app

    events = []

    class Entry:
        def __init__(self):
            self.text = "clear-before-validation-value"

        def get_text(self):
            events.append("entry-read")
            return self.text

        def set_text(self, text):
            self.text = text
            events.append("entry-cleared")

    class Button:
        def __init__(self):
            self.sensitive = True

        def set_sensitive(self, sensitive):
            self.sensitive = sensitive
            events.append(f"button-{sensitive}")

    class Controller:
        def connect(self, *, token):
            assert token == "clear-before-validation-value"
            assert entry.text == ""
            assert button.sensitive is False
            events.append("validated")
            return "safe-dashboard-model"

    entry = Entry()
    button = Button()
    rendered = []

    app._connect_from_token_entry(
        token_entry=entry,
        connect_button=button,
        controller=Controller(),
        render_model=rendered.append,
    )

    assert events == [
        "entry-read",
        "entry-cleared",
        "button-False",
        "validated",
        "button-True",
    ]
    assert entry.text == ""
    assert button.sensitive is True
    assert rendered == ["safe-dashboard-model"]


@pytest.mark.parametrize(
    ("factory", "expected_daemon", "expected_pairing"),
    [
        (
            ScriptedReadOnlyClientFactory(
                readiness_result=AuthenticationError(401)
            ),
            "Daemon reachable",
            "Token rejected",
        ),
        (
            ScriptedReadOnlyClientFactory(
                health_result=ConnectionFailure("offline")
            ),
            "Daemon unavailable",
            "Pairing unavailable",
        ),
        (
            ScriptedReadOnlyClientFactory(
                readiness_result={"unexpected": "response"}
            ),
            "Daemon status unknown",
            "Pairing status unknown",
        ),
        (
            ScriptedReadOnlyClientFactory(
                preflight_result=_api_response(
                    {
                        "schema_version": 99,
                        "overall_readiness": "future",
                    }
                )
            ),
            "Daemon connected",
            "Paired",
        ),
    ],
    ids=(
        "rejected",
        "unreachable",
        "invalid-pairing",
        "malformed-diagnostics",
    ),
)
def test_native_dashboard_uses_safe_states_for_pairing_and_response_failures(
    factory,
    expected_daemon,
    expected_pairing,
):
    from flatpak_app import FirstRunTokenEntryController, build_dashboard_model

    display_model = FirstRunTokenEntryController(
        client_factory=factory
    ).connect(token="safe-failure-state-value")
    dashboard = build_dashboard_model(display_model)

    assert dashboard.daemon.title == expected_daemon
    assert dashboard.pairing.title == expected_pairing
    assert dashboard.support_bundle.action_enabled is False
    assert dashboard.controls.mutation_actions == ()
    if expected_pairing != "Paired":
        assert dashboard.adapter_readiness.cards == ()
        assert dashboard.preflight.issues == ()
    else:
        assert dashboard.preflight.severity.value == "unknown"
        assert dashboard.preflight.issues == ()


def test_native_dashboard_exposes_no_secret_or_host_path_values():
    from flatpak_app import FirstRunTokenEntryController, build_dashboard_model
    from flatpak_app import app

    entered_value = "entered-dashboard-value-must-not-escape"
    token_value = "daemon-token-value-must-not-escape"
    passphrase_value = "passphrase-value-must-not-escape"
    password_value = "password-value-must-not-escape"
    psk_value = "psk-value-must-not-escape"
    bearer_value = "bearer-value-must-not-escape"
    secret_value = "secret-value-must-not-escape"
    host_path = "/var/lib/vr-hotspot/private-dashboard-state.json"
    factory = ScriptedReadOnlyClientFactory(
        readiness_result=_api_response(
            {
                "recommended": "wlan1",
                "basic_mode_recommended": "wlan1",
                "summary": {"readiness_state": "ready"},
                "adapters": [
                    {
                        "interface": "wlan1",
                        "readiness_state": "ready",
                        "explanation": (
                            f"token={token_value}; "
                            f"passphrase={passphrase_value}; "
                            f"password={password_value}; psk={psk_value}; "
                            f"secret={secret_value}"
                        ),
                    }
                ],
            }
        ),
        preflight_result=_api_response(
            {
                "schema_version": 1,
                "overall_readiness": "warning",
                "platform": {},
                "firewall": {},
                "services": {},
                "network": {},
                "wifi": {},
                "issues": [
                    {
                        "severity": "warning",
                        "code": "redaction_check",
                        "message": (
                            f"Authorization: Bearer {bearer_value}; "
                            f"inspect {host_path}"
                        ),
                    }
                ],
                "recommended_actions": [],
            }
        ),
    )

    dashboard = build_dashboard_model(
        FirstRunTokenEntryController(client_factory=factory).connect(
            token=entered_value
        )
    )
    container = FakeGtkWidget()
    app._render_dashboard_model(FakeGtk, container, dashboard)
    rendered_labels = tuple(
        widget.label
        for widget in _walk_fake_widgets(container)
        if isinstance(widget, (FakeGtkLabel, FakeGtkButton))
    )
    exposed = (
        repr(asdict(dashboard))
        + repr(dashboard)
        + repr(rendered_labels)
    )

    for forbidden_value in (
        entered_value,
        token_value,
        passphrase_value,
        password_value,
        psk_value,
        bearer_value,
        secret_value,
        host_path,
    ):
        assert forbidden_value not in exposed
    assert "[redacted]" in exposed
    assert "[host path]" in exposed


def test_rejected_token_is_safe_and_never_enters_output_or_logs(caplog):
    from flatpak_app import FirstRunTokenEntryController
    from flatpak_client import AuthenticationError

    secret = "rejected-ui-value-must-not-escape"
    controller = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory(
            readiness_result=AuthenticationError(401)
        )
    )

    model = controller.connect(token=secret)
    exposed = repr(model) + repr(asdict(model)) + repr(controller) + caplog.text

    assert model.daemon.reachable is True
    assert model.pairing.title == "Token rejected"
    assert model.pairing.paired is False
    assert secret not in exposed


def test_unreachable_daemon_updates_the_safe_offline_display_state():
    from flatpak_app import FirstRunTokenEntryController
    from flatpak_client import ConnectionFailure

    controller = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory(
            health_result=ConnectionFailure("offline")
        )
    )

    model = controller.connect(token="in-memory-offline-value")

    assert model.daemon.title == "Daemon unavailable"
    assert model.daemon.reachable is False
    assert model.pairing.title == "Pairing unavailable"
    assert model.adapters.cards == ()
    assert model.preflight.issues == ()


def test_missing_daemon_token_updates_the_safe_blocked_display_state():
    from flatpak_app import FirstRunTokenEntryController
    from flatpak_client import DaemonTokenMissingError

    controller = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory(
            readiness_result=DaemonTokenMissingError()
        )
    )

    model = controller.connect(token="caller-provided-missing-config-value")

    assert model.daemon.reachable is True
    assert model.pairing.title == "Daemon token missing"
    assert model.pairing.detail_code == "api_token_missing"
    assert model.pairing.paired is False


def test_malformed_pairing_response_degrades_the_entire_display_to_unknown():
    from flatpak_app import FirstRunTokenEntryController
    from flatpak_client import StatusSeverity

    controller = FirstRunTokenEntryController(
        client_factory=ScriptedReadOnlyClientFactory(
            readiness_result={"unexpected": "response"}
        )
    )

    model = controller.connect(token="caller-provided-malformed-value")

    assert model.daemon.severity is StatusSeverity.UNKNOWN
    assert model.pairing.severity is StatusSeverity.UNKNOWN
    assert model.adapters.severity is StatusSeverity.UNKNOWN
    assert model.preflight.severity is StatusSeverity.UNKNOWN


def test_token_entry_flow_does_not_retain_persist_log_or_emit_token(
    caplog,
    monkeypatch,
    tmp_path,
):
    from flatpak_app import (
        FirstRunTokenEntryController,
        build_smoke_payload,
        render_smoke_json,
    )

    secret = "ephemeral-ui-value-never-persist"
    factory = ScriptedReadOnlyClientFactory()
    controller = FirstRunTokenEntryController(client_factory=factory)
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.rglob("*"))

    model = controller.connect(token=secret)
    exposed = (
        repr(controller)
        + repr(model)
        + repr(asdict(model))
        + repr(build_smoke_payload())
        + render_smoke_json()
        + caplog.text
    )

    assert tuple(tmp_path.rglob("*")) == before
    assert secret not in exposed
    assert secret not in repr(vars(controller))
    assert not hasattr(controller, "token")
    assert not hasattr(controller, "_token")


def test_shell_exposes_no_mutation_controls_or_actions():
    from flatpak_app import build_smoke_payload
    from flatpak_app import app

    payload = build_smoke_payload()
    public_methods = {
        name
        for name, value in inspect.getmembers(app, inspect.isfunction)
        if not name.startswith("_")
    }

    assert payload["controls"]["mutation_actions"] == []
    assert payload["controls"]["support_bundle_export_enabled"] is False
    assert payload["ui"]["support_bundle"]["action_enabled"] is False
    assert public_methods.isdisjoint(
        {
            "start",
            "stop",
            "restart",
            "repair",
            "save_config",
            "update_config",
            "request",
            "post",
            "export",
        }
    )


def test_app_shell_has_no_direct_host_secret_or_network_access():
    source = Path("flatpak_app/app.py").read_text(encoding="utf-8")

    for forbidden in (
        "import os",
        "import pathlib",
        "import socket",
        "import subprocess",
        "import urllib",
        "import requests",
        "os.environ",
        "getenv(",
        "keyring",
        "SecretService",
        "portal",
        "/etc/",
        "/var/lib/",
        "VR_HOTSPOTD_API_TOKEN",
        "systemctl",
        "nmcli",
        "hostapd",
        "dnsmasq",
        "firewall",
    ):
        assert forbidden not in source


def test_shell_has_no_token_cli_argument_or_discovery_source():
    from flatpak_app import app

    parser = app._argument_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    source = Path("flatpak_app/app.py").read_text(encoding="utf-8")

    assert "--token" not in option_strings
    assert "--api-token" not in option_strings
    assert "--live-pairing-smoke-json" in option_strings
    assert "getpass.getpass" in source
    assert "isatty()" in source
    assert "FirstRunTokenEntryController" in source
    assert "LocalApiClient" in source
    assert "TokenPairingController" in source
    assert "DiagnosticsControlUiController" in source


def test_manifest_is_valid_json_and_matches_app_id_command_and_runtime():
    manifest = _manifest()

    assert manifest["app-id"] == APP_ID
    assert manifest["command"] == LAUNCHER_PATH.name
    assert manifest["runtime"] == "org.gnome.Platform"
    assert manifest["sdk"] == "org.gnome.Sdk"
    assert manifest["runtime-version"]


def test_manifest_has_only_minimal_display_and_loopback_client_permissions():
    finish_args = set(_manifest()["finish-args"])

    assert finish_args == {
        "--share=network",
        "--share=ipc",
        "--socket=wayland",
        "--socket=fallback-x11",
    }
    assert not any("filesystem=" in argument for argument in finish_args)
    assert not any("system-bus" in argument for argument in finish_args)
    assert not any("session-bus" in argument for argument in finish_args)
    assert not any("talk-name" in argument for argument in finish_args)
    assert "--filesystem=host" not in finish_args
    assert "--device=all" not in finish_args
    assert "--socket=system-bus" not in finish_args


def test_manifest_packages_only_shell_client_and_static_desktop_assets():
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    sources = _manifest()["modules"][0]["sources"]
    paths = {source["path"] for source in sources}

    assert all(source["type"] == "file" for source in sources)
    assert paths
    assert all(
        path.startswith("../../flatpak_app/")
        or path.startswith("../../flatpak_client/")
        or "/" not in path
        for path in paths
    )
    for forbidden in (
        "backend/",
        "backend/vendor",
        "vr_hotspotd",
        "install.sh",
        "uninstall.sh",
        "systemd",
    ):
        assert forbidden not in manifest_text


def test_desktop_file_matches_app_id_name_and_launcher():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    assert DESKTOP_PATH.exists()
    parser.read(DESKTOP_PATH, encoding="utf-8")
    entry = parser["Desktop Entry"]

    assert DESKTOP_PATH.stem == APP_ID
    assert entry["Type"] == "Application"
    assert entry["Name"] == APP_NAME
    assert entry["Exec"] == LAUNCHER_PATH.name
    assert entry["Icon"] == APP_ID
    assert entry["Terminal"] == "false"
    assert not any(key.startswith("Actions") for key in entry)


def test_metainfo_xml_parses_and_matches_app_and_desktop_ids():
    assert METAINFO_PATH.exists()
    root = ET.parse(METAINFO_PATH).getroot()

    assert root.tag == "component"
    assert root.attrib["type"] == "desktop-application"
    assert root.findtext("id") == APP_ID
    assert root.findtext("name") == APP_NAME
    launchable = root.find("launchable")
    assert launchable is not None
    assert launchable.attrib["type"] == "desktop-id"
    assert launchable.text == DESKTOP_PATH.name


def test_launcher_is_executable_static_and_safe():
    assert LAUNCHER_PATH.exists()
    assert LAUNCHER_PATH.stat().st_mode & 0o111
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert source.startswith("#!/bin/sh\n")
    assert "exec python3 -m flatpak_app" in source
    assert len(source.encode("utf-8")) < 512
    for forbidden in (
        "sudo",
        "pkexec",
        "curl",
        "wget",
        "systemctl",
        "dbus-send",
        "/etc/",
        "/var/lib/",
        "VR_HOTSPOTD_API_TOKEN",
        "backend/vendor",
    ):
        assert forbidden not in source
