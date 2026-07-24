import configparser
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


def _api_response(data, *, result_code="ok"):
    from flatpak_client import ApiResponse

    return ApiResponse(
        correlation_id="flatpak-shell-test",
        result_code=result_code,
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
                    "reason_codes": ["supports_ap_mode", "daemon_recommended"],
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
            "recommended_actions": [],
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
        self.visible = False

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def set_child(self, child):
        self.children = []
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

    def set_margin_top(self, _margin):
        pass

    def set_margin_bottom(self, _margin):
        pass

    def set_margin_start(self, _margin):
        pass

    def set_margin_end(self, _margin):
        pass


class FakeGtkApplication:
    activations = 1
    last_created = None

    def __init__(self, *, application_id):
        self.application_id = application_id
        self.signal_handlers = {}
        type(self).last_created = self

    def connect(self, signal, handler):
        self.signal_handlers[signal] = handler

    def run(self, arguments):
        assert arguments == []
        for _index in range(type(self).activations):
            self.signal_handlers["activate"](self)
        return 0


class FakeGtkApplicationWindow(FakeGtkWidget):
    created = []
    last_presented = None

    def __init__(self, **properties):
        super().__init__(**properties)
        self.icon_names = []
        self.present_calls = 0
        self.hide_calls = 0
        type(self).created.append(self)

    def set_icon_name(self, icon_name):
        self.icon_names.append(icon_name)

    def set_title(self, _title):
        pass

    def set_default_size(self, _width, _height):
        pass

    def present(self):
        self.visible = True
        self.present_calls += 1
        type(self).last_presented = self

    def hide(self):
        self.visible = False
        self.hide_calls += 1


class FakeGtkWindow:
    default_icon_names = []

    @classmethod
    def set_default_icon_name(cls, icon_name):
        cls.default_icon_names.append(icon_name)


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

    Application = FakeGtkApplication
    ApplicationWindow = FakeGtkApplicationWindow
    Window = FakeGtkWindow
    Box = FakeGtkWidget
    Label = FakeGtkLabel
    Button = FakeGtkButton


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


class FakeWebKitUserContentManager:
    def __init__(self):
        self.signal_handlers = {}
        self.registrations = []

    def connect(self, signal, handler):
        self.signal_handlers[signal] = handler

    def register_script_message_handler_with_reply(self, name, world_name):
        self.registrations.append((name, world_name))
        return True


class FakeWebKitWebView(FakeGtkWidget):
    created = []
    last_created = None

    def __init__(self, **properties):
        super().__init__()
        self.properties = properties
        self.loaded_uris = []
        self.zoom_levels = []
        self.current_uri = ""
        self.user_content_manager = FakeWebKitUserContentManager()
        self.evaluated_scripts = []
        type(self).created.append(self)
        type(self).last_created = self

    def load_uri(self, uri):
        self.loaded_uris.append(uri)
        self.current_uri = uri

    def set_zoom_level(self, zoom_level):
        self.zoom_levels.append(zoom_level)

    def get_uri(self):
        return self.current_uri

    def get_user_content_manager(self):
        return self.user_content_manager

    def evaluate_javascript(self, *arguments):
        self.evaluated_scripts.append(arguments)


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


def _labels_under(widget):
    return {
        child.label
        for child in _walk_fake_widgets(widget)
        if isinstance(child, FakeGtkLabel)
    }


def _reset_gui_fakes():
    FakeGtkApplication.activations = 1
    FakeGtkApplicationWindow.created = []
    FakeGtkApplicationWindow.last_presented = None
    FakeGtkWindow.default_icon_names = []
    FakeWebKitWebView.created = []
    FakeWebKitWebView.last_created = None


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
        ("response", "http://127.0.0.1:8732/assets/ui.js", "use"),
        ("response", "https://example.com/remote.js", "ignore"),
        ("unknown", "http://127.0.0.1:8732/ui", "ignore"),
    ),
)
def test_web_portal_policy_is_origin_locked(
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


def test_locked_web_portal_view_is_ephemeral_zoomed_and_has_no_injection():
    from flatpak_app import app

    web_view = app._build_locked_web_portal_view(FakeWebKit)
    session = FakeWebKitNetworkSession.last_created
    settings = FakeWebKitSettings.last_created
    source = inspect.getsource(app._build_locked_web_portal_view)

    assert session.is_ephemeral() is True
    assert session.persistent_credentials is False
    assert session.cookie_manager.accept_policy == "never"
    assert web_view.properties["network_session"] is session
    assert web_view.properties["settings"] is settings
    assert web_view.zoom_levels == [1.75]
    assert app.WEB_PORTAL_SHELL_ZOOM == 1.75
    assert web_view.properties["default_content_security_policy"] == app._WEB_PORTAL_CSP
    assert app.WEB_PORTAL_ORIGIN in app._WEB_PORTAL_CSP
    assert "object-src 'none'" in app._WEB_PORTAL_CSP
    assert "frame-src 'none'" in app._WEB_PORTAL_CSP
    assert settings.values["set_enable_dns_prefetching"] is False
    assert settings.values["set_enable_page_cache"] is False
    assert settings.values["set_javascript_can_open_windows_automatically"] is False
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
    ):
        assert forbidden not in source


class FakeJavascriptValue:
    def __init__(self, value):
        self.value = value

    @classmethod
    def new_string(cls, _context, value):
        return cls(value)

    def get_context(self):
        return object()

    def is_string(self):
        return isinstance(self.value, str)

    def to_string(self):
        return self.value


class FakeScriptReply:
    def __init__(self):
        self.value = None

    def return_value(self, value):
        self.value = value.value


class FakeBridgeAuthentication:
    def __init__(self, *, token=None):
        self.token = token
        self.saved = []
        self.clear_calls = 0

    def token_for_operation(self):
        return self.token

    def save_or_replace(self, token, *, save_securely):
        self.saved.append((bool(token), save_securely))
        self.token = token
        return type(
            "Result",
            (),
            {
                "code": "saved_securely",
                "token_available": True,
            },
        )()

    def clear(self):
        self.token = None
        self.clear_calls += 1


def _bridge_message(message):
    return FakeJavascriptValue(json.dumps(message)), FakeScriptReply()


def test_web_portal_auth_success_updates_shared_state_and_refreshes_tray():
    from flatpak_app import app

    secret = "accepted-portal-value"
    authentication = FakeBridgeAuthentication()
    bridge = app.WebPortalAuthBridge(authentication)
    refreshes = []
    bridge.set_auth_changed_callback(lambda: refreshes.append("refresh"))
    web_view = FakeWebKitWebView()
    web_view.current_uri = f"{app.WEB_PORTAL_ORIGIN}/ui"
    value, reply = _bridge_message(
        {
            "version": app.WEB_PORTAL_AUTH_PROTOCOL_VERSION,
            "type": "auth_accepted",
            "token": secret,
        }
    )

    assert bridge.handle_message(web_view, value, reply) is True

    assert authentication.saved == [(True, True)]
    assert refreshes == ["refresh"]
    assert reply.value == "accepted"
    assert secret not in repr(bridge)
    assert secret not in repr(authentication.saved)


def test_wallet_token_reply_is_fixed_origin_only_and_never_enters_bridge_repr():
    from flatpak_app import app

    secret = "wallet-value-for-fixed-origin"
    authentication = FakeBridgeAuthentication(token=secret)
    bridge = app.WebPortalAuthBridge(authentication)
    value, reply = _bridge_message(
        {
            "version": app.WEB_PORTAL_AUTH_PROTOCOL_VERSION,
            "type": "token_request",
        }
    )
    web_view = FakeWebKitWebView()
    web_view.current_uri = f"{app.WEB_PORTAL_ORIGIN}/assets/ui.js"

    assert bridge.handle_message(web_view, value, reply) is True
    assert reply.value == secret
    assert secret not in repr(bridge)

    rejected_value, rejected_reply = _bridge_message(
        {
            "version": app.WEB_PORTAL_AUTH_PROTOCOL_VERSION,
            "type": "token_request",
        }
    )
    web_view.current_uri = "https://example.com/ui"
    assert bridge.handle_message(
        web_view,
        rejected_value,
        rejected_reply,
    ) is False
    assert rejected_reply.value == "rejected"

    same_origin_value, same_origin_reply = _bridge_message(
        {
            "version": app.WEB_PORTAL_AUTH_PROTOCOL_VERSION,
            "type": "token_request",
        }
    )
    web_view.current_uri = f"{app.WEB_PORTAL_ORIGIN}/v1/status"
    assert bridge.handle_message(
        web_view,
        same_origin_value,
        same_origin_reply,
    ) is False
    assert same_origin_reply.value == "rejected"


@pytest.mark.parametrize(
    "message",
    (
        None,
        [],
        {"version": True, "type": "token_request"},
        {"version": 2, "type": "token_request"},
        {"version": 1, "type": "unknown"},
        {"version": 1, "type": "token_request", "extra": True},
        {"version": 1, "type": "auth_accepted"},
        {"version": 1, "type": "auth_accepted", "token": ""},
    ),
)
def test_web_portal_auth_bridge_rejects_invalid_message_schema(message):
    from flatpak_app import app

    authentication = FakeBridgeAuthentication()
    bridge = app.WebPortalAuthBridge(authentication)
    web_view = FakeWebKitWebView()
    web_view.current_uri = app.WEB_PORTAL_URL
    value = FakeJavascriptValue(json.dumps(message))
    reply = FakeScriptReply()

    assert bridge.handle_message(web_view, value, reply) is False
    assert reply.value == "rejected"
    assert authentication.saved == []


def test_web_portal_auth_bridge_bounds_message_bytes_before_parsing():
    from flatpak_app import app

    bridge = app.WebPortalAuthBridge(FakeBridgeAuthentication())
    web_view = FakeWebKitWebView()
    web_view.current_uri = app.WEB_PORTAL_URL
    value = FakeJavascriptValue(
        "x" * (app.MAX_WEB_PORTAL_AUTH_MESSAGE_BYTES + 1)
    )
    reply = FakeScriptReply()

    assert bridge.handle_message(web_view, value, reply) is False
    assert reply.value == "rejected"


def test_web_portal_clear_syncs_shared_auth_and_refreshes_tray():
    from flatpak_app import app

    authentication = FakeBridgeAuthentication(token="present")
    bridge = app.WebPortalAuthBridge(authentication)
    refreshes = []
    bridge.set_auth_changed_callback(lambda: refreshes.append("refresh"))
    web_view = FakeWebKitWebView()
    web_view.current_uri = app.WEB_PORTAL_URL
    value, reply = _bridge_message(
        {
            "version": app.WEB_PORTAL_AUTH_PROTOCOL_VERSION,
            "type": "auth_cleared",
        }
    )

    assert bridge.handle_message(web_view, value, reply) is True
    assert authentication.clear_calls == 1
    assert authentication.token is None
    assert refreshes == ["refresh"]
    assert reply.value == "cleared"


def test_web_portal_bridge_registration_and_host_clear_stay_fixed_origin():
    from flatpak_app import app

    bridge = app.WebPortalAuthBridge(FakeBridgeAuthentication())
    web_view = app._build_locked_web_portal_view(
        FakeWebKit,
        auth_bridge=bridge,
    )
    manager = web_view.user_content_manager
    signal = (
        "script-message-with-reply-received::"
        f"{app.WEB_PORTAL_AUTH_HANDLER}"
    )

    assert manager.registrations == [(app.WEB_PORTAL_AUTH_HANDLER, None)]
    assert signal in manager.signal_handlers

    web_view.current_uri = app.WEB_PORTAL_URL
    bridge.clear_web_portal_session()
    assert len(web_view.evaluated_scripts) == 1
    script_call = web_view.evaluated_scripts[0]
    assert script_call[0] == bridge._CLEAR_PORTAL_SCRIPT
    assert script_call[3] == (
        f"{app.WEB_PORTAL_ORIGIN}/companion-auth-bridge"
    )

    web_view.current_uri = "https://example.com/"
    bridge.clear_web_portal_session()
    assert len(web_view.evaluated_scripts) == 1


@pytest.mark.parametrize(
    ("configured_zoom", "expected_zoom"),
    ((-100.0, 1.0), (100.0, 2.0)),
)
def test_web_portal_shell_zoom_is_bounded(
    monkeypatch,
    configured_zoom,
    expected_zoom,
):
    from flatpak_app import app

    monkeypatch.setattr(app, "WEB_PORTAL_SHELL_ZOOM", configured_zoom)

    assert inspect.signature(app._bounded_web_portal_shell_zoom).parameters == {}
    assert app._bounded_web_portal_shell_zoom() == expected_zoom


def test_default_graphical_launch_uses_one_web_portal_window(monkeypatch):
    from flatpak_app import app
    from flatpak_app.tray import ICON_NAMES

    _reset_gui_fakes()
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)

    assert app.main([]) == 0
    assert len(FakeGtkApplicationWindow.created) == 1
    assert len(FakeWebKitWebView.created) == 1
    assert FakeWebKitWebView.last_created.loaded_uris == [app.WEB_PORTAL_URL]
    assert FakeGtkApplication.last_created.application_id == APP_ID
    assert app.WINDOW_ICON_NAME == APP_ID
    assert FakeGtkWindow.default_icon_names == [APP_ID]
    assert FakeGtkApplicationWindow.created[0].icon_names == [APP_ID]
    assert app.WINDOW_ICON_NAME not in ICON_NAMES.values()


def test_compatibility_alias_uses_default_web_portal_behavior(monkeypatch):
    from flatpak_app import app

    calls = []
    monkeypatch.setattr(
        app,
        "run_web_portal_shell",
        lambda: calls.append("web-portal") or 17,
    )

    assert app.main(["--web-portal-shell"]) == 17
    assert calls == ["web-portal"]
    with pytest.raises(SystemExit):
        app._argument_parser().parse_args(["--web-portal-shell=https://example.com"])


def test_repeated_activation_restores_without_duplicate_windows(monkeypatch):
    from flatpak_app import app

    _reset_gui_fakes()
    FakeGtkApplication.activations = 3
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)

    assert app.run_web_portal_shell() == 0
    assert len(FakeGtkApplicationWindow.created) == 1
    assert len(FakeWebKitWebView.created) == 1
    assert FakeGtkApplicationWindow.created[0].present_calls == 3


def test_tray_primary_activation_restores_single_web_portal_window(monkeypatch):
    from flatpak_app import app
    from flatpak_app import tray
    from flatpak_client import TrayState

    _reset_gui_fakes()
    FakeGtkApplication.activations = 2
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(
        app,
        "_load_tray_desktop_modules",
        lambda: (object(), object(), object()),
    )
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)
    monkeypatch.setattr(app, "AuthenticationController", lambda: object())
    monkeypatch.setattr(
        app,
        "TrayControlController",
        lambda _authentication: type("Controls", (), {"state": TrayState()})(),
    )
    captured = {}

    class Backend:
        def __init__(self, **kwargs):
            captured["primary"] = kwargs["on_activate"]

    class Runtime:
        def __init__(self, *, lifecycle, **_kwargs):
            self.lifecycle = lifecycle
            captured["runtime"] = self

        def show(self):
            self.lifecycle.show()

        def start(self):
            return True

        def refresh_async(self):
            pass

        def refresh_after_auth_change(self):
            pass

        def close_request(self, *_args):
            return self.lifecycle.close_request(*_args)

        def dispatch_action(self, _action):
            pass

    monkeypatch.setattr(tray, "StatusNotifierBackend", Backend)
    monkeypatch.setattr(tray, "TrayRuntime", Runtime)

    assert app.run_tray() == 0
    captured["primary"]()

    assert len(FakeGtkApplicationWindow.created) == 1
    assert len(FakeWebKitWebView.created) == 1
    assert FakeWebKitWebView.last_created.loaded_uris == [app.WEB_PORTAL_URL]
    assert FakeGtkApplicationWindow.created[0].present_calls == 3
    assert FakeGtkApplication.last_created.application_id == APP_ID
    assert FakeGtkWindow.default_icon_names == [APP_ID]
    assert FakeGtkApplicationWindow.created[0].icon_names == [APP_ID]


def test_webkit_unavailable_shows_bounded_error_without_alternate_ui(monkeypatch):
    from flatpak_app import app

    _reset_gui_fakes()
    secret = "webkit-detail-must-not-render"
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)

    def unavailable():
        raise app.WebKitUnavailableError(secret)

    monkeypatch.setattr(app, "_load_webkit", unavailable)

    assert app.run_web_portal_shell() == 0
    window = FakeGtkApplicationWindow.last_presented
    labels = _labels_under(window)
    assert "Web Portal shell unavailable" in labels
    assert secret not in repr(labels)
    assert FakeWebKitWebView.created == []


def test_unexpected_webkit_loader_failure_is_also_bounded(monkeypatch):
    from flatpak_app import app

    _reset_gui_fakes()
    secret = "unexpected-webkit-detail-must-not-render"
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)

    def unavailable():
        raise RuntimeError(secret)

    monkeypatch.setattr(app, "_load_webkit", unavailable)

    assert app.run_web_portal_shell() == 0
    labels = _labels_under(FakeGtkApplicationWindow.last_presented)
    assert "Web Portal shell unavailable" in labels
    assert secret not in repr(labels)
    assert FakeWebKitWebView.created == []


def test_webkit_construction_failure_shows_same_bounded_error(monkeypatch):
    from flatpak_app import app

    _reset_gui_fakes()
    secret = "construction-detail-must-not-render"
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)

    def fail_to_construct(_webkit):
        raise RuntimeError(secret)

    monkeypatch.setattr(app, "_build_locked_web_portal_view", fail_to_construct)

    assert app.run_web_portal_shell() == 0
    labels = _labels_under(FakeGtkApplicationWindow.last_presented)
    assert "Web Portal shell unavailable" in labels
    assert secret not in repr(labels)


def test_web_portal_load_failure_has_bounded_safe_retry(monkeypatch):
    from flatpak_app import app

    _reset_gui_fakes()
    monkeypatch.setattr(app, "_load_gtk", lambda: FakeGtk)
    monkeypatch.setattr(app, "_load_webkit", lambda: FakeWebKit)
    assert app.run_web_portal_shell() == 0

    window = FakeGtkApplicationWindow.last_presented
    web_view = FakeWebKitWebView.last_created
    secret = "load-detail-must-not-render"
    handled = web_view.signal_handlers["load-failed"](
        web_view,
        "started",
        app.WEB_PORTAL_URL,
        RuntimeError(secret),
    )
    labels = _labels_under(window)
    buttons = [
        widget
        for widget in _walk_fake_widgets(window)
        if isinstance(widget, FakeGtkButton)
    ]

    assert handled is True
    assert "Local Web Portal unavailable" in labels
    assert secret not in repr(labels)
    assert [button.label for button in buttons] == ["Retry local portal"]
    buttons[0].signal_handlers["clicked"](buttons[0])
    assert web_view.loaded_uris == [app.WEB_PORTAL_URL, app.WEB_PORTAL_URL]


def test_retired_graphical_implementation_symbols_are_absent():
    from flatpak_app import app

    names = (
        "run_" + "gui",
        "_populate_" + "native_" + "dashboard_window",
        "Native" + "DashboardModel",
        "Dashboard" + "SectionLabels",
        "build_" + "dashboard_model",
        "FirstRun" + "TokenEntryController",
        "_connect_from_" + "token_entry",
    )
    source = Path(app.__file__).read_text(encoding="utf-8")

    for name in names:
        assert not hasattr(app, name)
        assert name not in source


def test_smoke_json_reports_web_portal_and_is_bounded():
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
        "graphical_shell": "web_portal",
        "origin": "http://127.0.0.1:8732",
        "state": "offline_unpaired",
    }
    assert payload["controls"]["mutation_actions"] == []


def test_smoke_json_contains_no_secret_or_host_path_markers():
    rendered = _smoke().stdout

    for forbidden in (
        "X-Api-Token",
        "Authorization",
        "Bearer ",
        "VR_HOTSPOTD_API_TOKEN",
        "/etc/vr-hotspot",
        "/var/lib/vr-hotspot",
        "token_value",
        "passphrase",
        "password",
    ):
        assert forbidden not in rendered


def test_live_pairing_smoke_dispatches_without_importing_gtk(monkeypatch):
    from flatpak_app import app

    calls = []
    monkeypatch.setattr(
        app,
        "run_live_pairing_smoke_json",
        lambda: calls.append("live-smoke") or 17,
    )
    monkeypatch.setitem(sys.modules, "gi", None)

    assert app.main(["--live-pairing-smoke-json"]) == 17
    assert calls == ["live-smoke"]
    assert sys.modules.get("gi") is None


def test_live_smoke_refuses_noninteractive_input_with_token_free_json(capsys):
    from flatpak_app import app

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=False),
        token_prompt=lambda _prompt: pytest.fail("must not prompt"),
        client_factory=lambda **_kwargs: pytest.fail("must not create client"),
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["live_smoke"]["status"] == "interactive_input_required"
    assert payload["pairing"]["paired"] is False
    assert payload["controls"]["mutation_actions"] == []


def test_live_smoke_success_is_bounded_and_secret_free(capsys):
    from flatpak_app import MAX_LIVE_SMOKE_JSON_BYTES
    from flatpak_app import app

    secret = "live-smoke-success-value-must-not-escape"
    factory = ScriptedReadOnlyClientFactory()
    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: secret,
        client_factory=factory,
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert exit_code == 0
    assert len(rendered.encode("utf-8")) <= MAX_LIVE_SMOKE_JSON_BYTES + 1
    assert payload["live_smoke"]["status"] == "success"
    assert payload["pairing"]["paired"] is True
    assert payload["adapter_readiness"]["recommended_interface"] == "wlan1"
    assert payload["preflight"]["readiness_label"] == "Needs attention"
    assert payload["controls"]["mutation_actions"] == []
    assert factory.token_presence == [False, True, True]
    assert secret not in rendered


@pytest.mark.parametrize(
    ("factory", "expected_status"),
    (
        (
            ScriptedReadOnlyClientFactory(
                readiness_result=AuthenticationError(401)
            ),
            "token_rejected",
        ),
        (
            ScriptedReadOnlyClientFactory(
                health_result=ConnectionFailure("offline")
            ),
            "daemon_unreachable",
        ),
        (
            ScriptedReadOnlyClientFactory(
                readiness_result=DaemonTokenMissingError()
            ),
            "daemon_token_missing",
        ),
        (
            ScriptedReadOnlyClientFactory(
                readiness_result={"unexpected": "response"}
            ),
            "invalid_response",
        ),
    ),
)
def test_live_smoke_failures_are_nonzero_and_secret_free(
    capsys,
    factory,
    expected_status,
):
    from flatpak_app import app

    secret = f"live-smoke-{expected_status}-must-not-escape"
    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=lambda _prompt: secret,
        client_factory=factory,
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert exit_code == 1
    assert payload["live_smoke"]["status"] == expected_status
    assert payload["pairing"]["paired"] is False
    assert payload["controls"]["mutation_actions"] == []
    assert secret not in rendered


def test_live_smoke_rejects_empty_or_unavailable_hidden_input(capsys):
    from flatpak_app import app

    client_calls = []

    def forbidden_factory(*, token):
        client_calls.append(token)
        raise AssertionError("client must not be created")

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


def test_live_smoke_rejects_getpass_echo_fallback(capsys):
    from flatpak_app import app

    secret = "fallback-must-not-read-or-echo-this-value"

    def fallback_prompt(_prompt):
        warnings.warn("hidden input unavailable", app.getpass.GetPassWarning)
        return secret

    exit_code = app.run_live_pairing_smoke_json(
        input_stream=FakeInputStream(interactive=True),
        token_prompt=fallback_prompt,
        client_factory=lambda **_kwargs: pytest.fail("must not create client"),
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert exit_code == 2
    assert payload["live_smoke"]["status"] == "token_input_cancelled"
    assert secret not in rendered


def test_web_portal_shell_keeps_tokens_out_of_urls_and_webview_configuration():
    from flatpak_app import app

    source = "\n".join(
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
        "keyring",
        "SecretService",
        "/etc/",
        "/var/lib/",
        "os.environ",
        "getenv(",
    ):
        assert forbidden not in source


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
    assert "LocalApiClient" in source
    assert "TokenPairingController" in source
    assert "DiagnosticsControlUiController" in source


def test_web_portal_shell_does_not_copy_or_package_frontend_assets():
    shell_source = Path("flatpak_app/app.py").read_text(encoding="utf-8")
    manifest_paths = {
        source["path"] for source in _manifest()["modules"][0]["sources"]
    }

    assert "index.html" not in shell_source
    assert "ui.js" not in shell_source
    assert "ui.css" not in shell_source
    assert not any(path.startswith("../../assets/") for path in manifest_paths)


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
    assert "--filesystem=host" not in finish_args
    assert "--device=all" not in finish_args
    assert "--socket=system-bus" not in finish_args


def test_manifest_packages_only_shell_client_and_static_desktop_assets():
    manifest = _manifest()
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    module = manifest["modules"][0]
    sources = module["sources"]
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
    base_icon_install = (
        f"install -Dm644 {APP_ID}.svg "
        f"/app/share/icons/hicolor/scalable/apps/{APP_ID}.svg"
    )
    assert base_icon_install in module["build-commands"]
    assert f"{APP_ID}.svg" in paths


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
