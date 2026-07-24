import configparser
from dataclasses import asdict
import importlib
import inspect
import json
from pathlib import Path
import re
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
        self.show_peek_icon = False

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

    def set_vexpand(self, _expand):
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

    def set_show_peek_icon(self, show):
        self.show_peek_icon = show


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
    last_presented = None

    def set_title(self, _title):
        pass

    def set_default_size(self, _width, _height):
        pass

    def present(self):
        type(self).last_presented = self


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


class FakeGtkPasswordEntry(FakeGtkWidget):
    pass


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
    PasswordEntry = FakeGtkPasswordEntry


class FakeCookieManager:
    def __init__(self):
        self.accept_policy = None

    def set_accept_policy(self, policy):
        self.accept_policy = policy


class FakeWebKitNetworkSession:
    last_created = None

    def __init__(self):
        self.ephemeral = True
        self.persistent_credentials = None
        self.cookie_manager = FakeCookieManager()
        type(self).last_created = self

    @classmethod
    def new_ephemeral(cls):
        return cls()

    def is_ephemeral(self):
        return self.ephemeral

    def set_persistent_credential_storage_enabled(self, enabled):
        self.persistent_credentials = enabled

    def get_cookie_manager(self):
        return self.cookie_manager


class FakeWebKitSettings:
    last_created = None

    def __init__(self):
        self.values = {}
        type(self).last_created = self

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda value: self.values.__setitem__(name, value)
        raise AttributeError(name)


class FakeWebKitWebView(FakeGtkWidget):
    last_created = None

    def __init__(self, **properties):
        super().__init__()
        self.properties = properties
        self.loaded_uris = []
        self.zoom_levels = []
        type(self).last_created = self

    def load_uri(self, uri):
        self.loaded_uris.append(uri)

    def set_zoom_level(self, zoom_level):
        self.zoom_levels.append(zoom_level)


class FakeWebKit:
    class PolicyDecisionType:
        NAVIGATION_ACTION = "navigation"
        NEW_WINDOW_ACTION = "new-window"
        RESPONSE = "response"

    class CookieAcceptPolicy:
        NEVER = "never"

    NetworkSession = FakeWebKitNetworkSession
    Settings = FakeWebKitSettings
    WebView = FakeWebKitWebView


def _walk_fake_widgets(widget):
    yield widget
    for child in widget.children:
        yield from _walk_fake_widgets(child)


def _fake_labels_under(widget):
    return {
        child.label
        for child in _walk_fake_widgets(widget)
        if isinstance(child, FakeGtkLabel)
    }


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


@pytest.mark.parametrize(
    "uri",
    (
        "http://127.0.0.1:8732/",
        "http://127.0.0.1:8732/ui",
        "http://127.0.0.1:8732/assets/ui.js?v=1",
        "http://127.0.0.1:8732/v1/status#safe-fragment",
    ),
)
def test_web_portal_shell_accepts_only_paths_on_the_pinned_origin(uri):
    from flatpak_app import (
        WEB_PORTAL_ORIGIN,
        WEB_PORTAL_URL,
        is_approved_web_portal_uri,
    )

    assert WEB_PORTAL_ORIGIN == "http://127.0.0.1:8732"
    assert WEB_PORTAL_URL == "http://127.0.0.1:8732/ui"
    assert is_approved_web_portal_uri(uri) is True


@pytest.mark.parametrize(
    "uri",
    (
        "",
        None,
        "https://127.0.0.1:8732/ui",
        "http://localhost:8732/ui",
        "http://127.0.0.1/ui",
        "http://127.0.0.1:80/ui",
        "http://127.0.0.2:8732/ui",
        "http://[::1]:8732/ui",
        "http://127.0.0.1:8732.evil.example/ui",
        "http://127.0.0.1:8732@evil.example/ui",
        "https://example.com/",
        "file:///etc/vr-hotspot/env",
        "data:text/html,external",
        "javascript:alert(1)",
        "blob:http://127.0.0.1:8732/value",
    ),
)
def test_web_portal_shell_rejects_arbitrary_or_non_pinned_urls(uri):
    from flatpak_app import is_approved_web_portal_uri

    assert is_approved_web_portal_uri(uri) is False


@pytest.mark.parametrize(
    ("decision_type", "uri", "expected_action"),
    (
        ("navigation", "http://127.0.0.1:8732/ui", "use"),
        ("navigation", "https://example.com/", "ignore"),
        ("new-window", "http://127.0.0.1:8732/ui", "ignore"),
        ("new-window", "https://example.com/", "ignore"),
        ("response", "http://127.0.0.1:8732/assets/ui.js", "use"),
        ("response", "https://example.com/remote.js", "ignore"),
        ("unknown", "http://127.0.0.1:8732/ui", "ignore"),
    ),
)
def test_web_portal_policy_explicitly_blocks_external_and_new_window_navigation(
    decision_type,
    uri,
    expected_action,
):
    from flatpak_app import app

    class Request:
        def get_uri(self):
            return uri

    class NavigationAction:
        def get_request(self):
            return Request()

    class Response:
        def get_uri(self):
            return uri

    class Decision:
        def __init__(self):
            self.actions = []

        def get_navigation_action(self):
            return NavigationAction()

        def get_response(self):
            return Response()

        def use(self):
            self.actions.append("use")

        def ignore(self):
            self.actions.append("ignore")

    decision = Decision()

    assert (
        app._handle_web_portal_policy(
            None,
            decision,
            decision_type,
            FakeWebKit,
        )
        is True
    )
    assert decision.actions == [expected_action]


def test_locked_web_portal_view_is_ephemeral_and_has_no_injection_surface():
    from flatpak_app import app

    web_view = app._build_locked_web_portal_view(FakeWebKit)
    session = FakeWebKitNetworkSession.last_created
    settings = FakeWebKitSettings.last_created
    source = inspect.getsource(app._build_locked_web_portal_view)

    assert session is not None
    assert session.is_ephemeral() is True
    assert session.persistent_credentials is False
    assert session.cookie_manager.accept_policy == "never"
    assert web_view.properties["network_session"] is session
    assert web_view.properties["settings"] is settings
    assert web_view.zoom_levels == [app.WEB_PORTAL_SHELL_ZOOM]
    assert app.WEB_PORTAL_SHELL_ZOOM == 1.75
    assert (
        app._WEB_PORTAL_SHELL_ZOOM_MIN
        <= app.WEB_PORTAL_SHELL_ZOOM
        <= app._WEB_PORTAL_SHELL_ZOOM_MAX
    )
    assert (
        web_view.properties["default_content_security_policy"]
        == app._WEB_PORTAL_CSP
    )
    assert app.WEB_PORTAL_ORIGIN in app._WEB_PORTAL_CSP
    assert "object-src 'none'" in app._WEB_PORTAL_CSP
    assert "frame-src 'none'" in app._WEB_PORTAL_CSP
    assert settings.values["set_enable_dns_prefetching"] is False
    assert settings.values["set_enable_page_cache"] is False
    assert (
        settings.values["set_javascript_can_open_windows_automatically"] is False
    )
    assert {
        "decide-policy",
        "create",
        "context-menu",
        "permission-request",
    } <= set(web_view.signal_handlers)
    for forbidden in (
        "X-Api-Token",
        "Authorization",
        "Bearer ",
        "localStorage",
        "sessionStorage",
        "UserScript",
        "add_script",
        "add_style_sheet",
        "set_uri(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    ("configured_zoom", "expected_zoom"),
    (
        (-100.0, 1.0),
        (100.0, 2.0),
    ),
)
def test_web_portal_shell_zoom_is_bounded_and_has_no_runtime_input(
    monkeypatch,
    configured_zoom,
    expected_zoom,
):
    from flatpak_app import app

    monkeypatch.setattr(app, "WEB_PORTAL_SHELL_ZOOM", configured_zoom)

    assert inspect.signature(app._bounded_web_portal_shell_zoom).parameters == {}
    assert app._bounded_web_portal_shell_zoom() == expected_zoom


def test_web_portal_shell_loads_only_the_fixed_daemon_served_route(monkeypatch):
    from flatpak_app import app

    FakeGtkApplicationWindow.last_presented = None
    FakeWebKitWebView.last_created = None
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)

    assert app.run_web_portal_shell() == 0

    window = FakeGtkApplicationWindow.last_presented
    web_view = FakeWebKitWebView.last_created
    assert window is not None
    assert web_view is not None
    assert web_view.loaded_uris == [app.WEB_PORTAL_URL]
    assert "load-failed" in web_view.signal_handlers
    assert not any(
        isinstance(widget, FakeGtkPasswordEntry)
        for widget in _walk_fake_widgets(window)
    )


def test_web_portal_load_failure_shows_bounded_safe_retry_ui(monkeypatch):
    from flatpak_app import app

    FakeGtkApplicationWindow.last_presented = None
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)
    assert app.run_web_portal_shell() == 0

    window = FakeGtkApplicationWindow.last_presented
    web_view = FakeWebKitWebView.last_created
    secret = "failure-detail-must-not-render"
    handled = web_view.signal_handlers["load-failed"](
        web_view,
        "started",
        app.WEB_PORTAL_URL,
        RuntimeError(secret),
    )
    widgets = tuple(_walk_fake_widgets(window))
    labels = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    buttons = [
        widget for widget in widgets if isinstance(widget, FakeGtkButton)
    ]

    assert handled is True
    assert "Local Web Portal unavailable" in labels
    assert secret not in repr(labels)
    assert [button.label for button in buttons] == ["Retry local portal"]
    buttons[0].signal_handlers["clicked"](buttons[0])
    assert web_view.loaded_uris == [app.WEB_PORTAL_URL, app.WEB_PORTAL_URL]


def test_webkit_unavailable_falls_back_to_native_dashboard(monkeypatch):
    from flatpak_app import app

    calls = []

    def unavailable():
        raise app.WebKitUnavailableError("bounded")

    monkeypatch.setattr(app, "run_web_portal_shell", unavailable)
    monkeypatch.setattr(app, "run_gui", lambda: calls.append("native") or 19)

    assert app.main(["--web-portal-shell"]) == 19
    assert calls == ["native"]


def test_webkit_construction_failure_falls_back_in_the_same_window(monkeypatch):
    from flatpak_app import app

    FakeGtkApplicationWindow.last_presented = None
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)

    def fail_to_construct(_webkit):
        raise RuntimeError("bounded")

    monkeypatch.setattr(app, "_build_locked_web_portal_view", fail_to_construct)

    assert app.run_web_portal_shell() == 0

    window = FakeGtkApplicationWindow.last_presented
    widgets = tuple(_walk_fake_widgets(window))
    labels = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    assert isinstance(window.children[0], FakeGtkScrolledWindow)
    assert {"VR Hotspot", "Native Dashboard", "NATIVE GTK"} <= labels


def test_web_portal_shell_has_no_token_injection_or_persistence_path():
    from flatpak_app import app

    portal_source = "\n".join(
        inspect.getsource(value)
        for value in (
            app._build_locked_web_portal_view,
            app._populate_web_portal_window,
            app.run_web_portal_shell,
        )
    )
    exposed = (
        repr(app.WEB_PORTAL_ORIGIN)
        + repr(app.WEB_PORTAL_URL)
        + repr(app._WEB_PORTAL_CSP)
        + app.render_smoke_json()
    )

    assert inspect.signature(app.run_web_portal_shell).parameters == {}
    assert "?" not in app.WEB_PORTAL_URL
    assert "#" not in app.WEB_PORTAL_URL
    assert "token" not in exposed.casefold()
    for forbidden in (
        "X-Api-Token",
        "Authorization",
        "Bearer ",
        "localStorage",
        "sessionStorage",
        "UserScript",
        "add_script",
        "set_cookie",
        "set_http_headers",
        "getenv",
        "/etc/",
        "/var/lib/",
    ):
        assert forbidden not in portal_source


def test_native_dashboard_remains_the_default_fallback(monkeypatch):
    from flatpak_app import app

    calls = []
    monkeypatch.setattr(app, "run_gui", lambda: calls.append("native") or 23)
    monkeypatch.setattr(
        app,
        "run_web_portal_shell",
        lambda: pytest.fail("the spike must remain opt-in"),
    )

    assert app.main([]) == 23
    assert calls == ["native"]


def test_web_portal_flag_accepts_no_url_or_other_value():
    from flatpak_app import app

    parser = app._argument_parser()
    parsed = parser.parse_args(["--web-portal-shell"])
    portal_action = next(
        action
        for action in parser._actions
        if "--web-portal-shell" in action.option_strings
    )

    assert parsed.web_portal_shell is True
    assert portal_action.nargs == 0
    with pytest.raises(SystemExit):
        parser.parse_args(["--web-portal-shell", "https://example.com/"])


def test_web_portal_shell_has_no_arbitrary_zoom_or_scale_option():
    from flatpak_app import app

    parser = app._argument_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }

    assert all(
        "zoom" not in option.casefold() and "scale" not in option.casefold()
        for option in option_strings
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--web-portal-shell", "--zoom", "2"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--web-portal-shell", "--scale", "2"])


def test_shared_browser_portal_css_has_no_flatpak_shell_scaling():
    css = Path("assets/ui.css").read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*zoom\s*:", css) is None
    assert "set_zoom_level" not in css
    assert "web-portal-shell" not in css


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
        "labels",
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
    assert tuple(asdict(dashboard.labels).values()) == (
        "Dashboard Overview",
        "Connection & Pairing",
        "Readiness & Adapter Summary",
        "Adapter Readiness",
        "Preflight Diagnostics",
        "Readiness & Host Summary",
        "Facts",
        "Blocking Issues",
        "Warnings",
        "Other Issues",
        "Recommended Actions",
        "Support Bundle",
        "Controls Boundary",
        "Unavailable Features",
    )

    widgets = tuple(_walk_fake_widgets(container))
    rendered_text = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    buttons = [
        widget for widget in widgets if isinstance(widget, FakeGtkButton)
    ]
    for section_title in (
        "Dashboard Overview",
        "Connection & Pairing",
        "Daemon Status",
        "Pairing Status",
        "Readiness & Adapter Summary",
        "Adapter Readiness",
        "Preflight Diagnostics",
        "Readiness & Host Summary",
        "Blocking Issues",
        "Warnings",
        "Recommended Actions",
        "Support Bundle",
        "Controls Boundary",
        "Unavailable Features",
    ):
        assert section_title in rendered_text
    assert "wlan1" in rendered_text
    assert "DAEMON RECOMMENDED" in rendered_text
    assert "RECOMMENDED" in rendered_text
    assert "PAIRED" in rendered_text
    assert "Readiness: Ready" in rendered_text
    assert "OK" in rendered_text
    assert "WARNING" in rendered_text
    assert "Top Reasons" in rendered_text
    assert "Facts" in rendered_text
    assert "No blocking issues." in rendered_text
    assert "Mutation actions: none" in rendered_text
    assert "NOT AVAILABLE YET" in rendered_text
    assert [(button.label, button.sensitive) for button in buttons] == [
        ("Export support bundle", False)
    ]


@pytest.mark.parametrize(
    ("severity_name", "expected_label", "semantic_class"),
    (
        ("OK", "OK", "success"),
        ("WARNING", "WARNING", "warning"),
        ("BLOCKED", "BLOCKED", "error"),
        ("ERROR", "ERROR", "error"),
        ("UNKNOWN", "UNKNOWN", "dim-label"),
    ),
)
def test_native_dashboard_severity_badges_render_consistently(
    severity_name,
    expected_label,
    semantic_class,
):
    from flatpak_app import app
    from flatpak_client import StatusSeverity

    container = FakeGtkWidget()
    severity = StatusSeverity[severity_name]

    badge = app._add_status_badge(FakeGtk, container, severity)

    assert _fake_labels_under(badge) == {expected_label}
    assert badge.css_classes == [
        "status-badge",
        f"severity-{severity.value}",
        semantic_class,
    ]


def test_recommended_adapter_has_visible_badge_and_emphasized_card():
    from flatpak_app import FirstRunTokenEntryController, build_dashboard_model
    from flatpak_app import app

    dashboard = build_dashboard_model(
        FirstRunTokenEntryController(
            client_factory=ScriptedReadOnlyClientFactory()
        ).connect(token="recommended-emphasis-value")
    )
    container = FakeGtkWidget()

    app._render_dashboard_model(FakeGtk, container, dashboard)

    emphasized = [
        widget
        for widget in _walk_fake_widgets(container)
        if "recommended-card" in widget.css_classes
    ]
    assert emphasized
    assert any(
        {"wlan1", "RECOMMENDED"}.issubset(_fake_labels_under(widget))
        for widget in emphasized
    )


def test_disabled_sections_are_visibly_unavailable_without_mutation_actions():
    from flatpak_app import FirstRunTokenEntryController, build_dashboard_model
    from flatpak_app import app

    dashboard = build_dashboard_model(
        FirstRunTokenEntryController(
            client_factory=ScriptedReadOnlyClientFactory()
        ).connect(token="disabled-sections-value")
    )
    container = FakeGtkWidget()

    app._render_dashboard_model(FakeGtk, container, dashboard)
    widgets = tuple(_walk_fake_widgets(container))
    labels = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    buttons = [
        widget for widget in widgets if isinstance(widget, FakeGtkButton)
    ]

    assert "NOT AVAILABLE YET" in labels
    assert "UNAVAILABLE" in labels
    assert "Mutation actions: none" in labels
    assert dashboard.controls.mutation_actions == ()
    assert dashboard.controls.action_enabled is False
    assert [(button.label, button.sensitive) for button in buttons] == [
        ("Export support bundle", False)
    ]
    assert all(button.signal_handlers == {} for button in buttons)


def test_gui_keeps_hidden_token_entry_scrollable_native_header_and_no_controls(
    monkeypatch,
):
    from flatpak_app import app

    FakeGtkApplicationWindow.last_presented = None
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)

    assert app.run_gui() == 0

    window = FakeGtkApplicationWindow.last_presented
    assert window is not None
    widgets = tuple(_walk_fake_widgets(window))
    entries = [
        widget for widget in widgets if isinstance(widget, FakeGtkPasswordEntry)
    ]
    labels = {
        widget.label for widget in widgets if isinstance(widget, FakeGtkLabel)
    }
    buttons = [
        widget for widget in widgets if isinstance(widget, FakeGtkButton)
    ]
    button_labels = {button.label.casefold() for button in buttons}

    assert isinstance(window.children[0], FakeGtkScrolledWindow)
    assert len(entries) == 1
    assert entries[0].show_peek_icon is True
    assert {"VR Hotspot", "Native Dashboard", "NATIVE GTK", "READ-ONLY"} <= labels
    assert "Connect / Validate token" in {button.label for button in buttons}
    assert {
        "start",
        "stop",
        "restart",
        "repair",
        "save",
        "apply",
        "refresh",
    }.isdisjoint(button_labels)


def test_native_dashboard_styles_are_bounded_and_have_no_external_assets():
    from flatpak_app import app

    stylesheet = app._DASHBOARD_CSS

    assert len(stylesheet.encode("utf-8")) < 512
    assert "url(" not in stylesheet.casefold()
    assert "@import" not in stylesheet.casefold()
    assert {
        ".dashboard-card",
        ".status-badge",
        ".recommended-card",
        ".unavailable-card",
    } <= set(line.strip().split(" {", 1)[0] for line in stylesheet.splitlines())


def test_web_portal_shell_does_not_copy_or_package_frontend_assets():
    source = Path("flatpak_app/app.py").read_text(encoding="utf-8").casefold()
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8").casefold()

    assert "webkit" in source
    assert "webview" in source
    for copied_asset in (
        "index.html",
        "ui.js",
        "ui.css",
        "../../assets/",
    ):
        assert copied_asset not in manifest_text


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
    assert "--web-portal-shell" in option_strings
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
        "--talk-name=org.kde.StatusNotifierWatcher",
        "--talk-name=org.freedesktop.secrets",
    }
    assert not any("filesystem=" in argument for argument in finish_args)
    assert not any("system-bus" in argument for argument in finish_args)
    assert not any("session-bus" in argument for argument in finish_args)
    assert {
        argument
        for argument in finish_args
        if argument.startswith("--talk-name=")
    } == {
        "--talk-name=org.kde.StatusNotifierWatcher",
        "--talk-name=org.freedesktop.secrets",
    }
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
    assert entry["Exec"] == f"{LAUNCHER_PATH.name} --tray"
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
