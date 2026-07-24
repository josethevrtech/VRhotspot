"""Launchable Flatpak shell for the locked local Web Portal."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
import sys
from typing import Any, Sequence
from urllib.parse import urlsplit
import warnings

from flatpak_client import (
    AuthenticationController,
    DiagnosticsControlUiController,
    DiagnosticsControlUiModel,
    FirstRunResult,
    FirstRunState,
    LocalApiClient,
    PresentationMode,
    StatusSeverity,
    TokenPairingController,
    TrayControlController,
)


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
WINDOW_ICON_NAME = APP_ID
WEB_PORTAL_ORIGIN = "http://127.0.0.1:8732"
WEB_PORTAL_URL = f"{WEB_PORTAL_ORIGIN}/ui"
WEBKIT_GI_NAMESPACE = "WebKit"
WEBKIT_GI_VERSION = "6.0"
WEB_PORTAL_SHELL_ZOOM = 1.75
MAX_SMOKE_JSON_BYTES = 8_192
MAX_LIVE_SMOKE_JSON_BYTES = 65_536
WEB_PORTAL_AUTH_HANDLER = "vrHotspotCompanionAuth"
WEB_PORTAL_AUTH_PROTOCOL_VERSION = 1
MAX_WEB_PORTAL_AUTH_MESSAGE_BYTES = 8_192
MAX_WEB_PORTAL_AUTH_TOKEN_CHARS = 4_096
_WEB_PORTAL_SHELL_ZOOM_MIN = 1.0
_WEB_PORTAL_SHELL_ZOOM_MAX = 2.0
_WEB_PORTAL_CSP = (
    f"default-src {WEB_PORTAL_ORIGIN}; "
    f"connect-src {WEB_PORTAL_ORIGIN}; "
    f"img-src {WEB_PORTAL_ORIGIN} data:; "
    f"style-src {WEB_PORTAL_ORIGIN} 'unsafe-inline'; "
    f"script-src {WEB_PORTAL_ORIGIN} 'unsafe-inline'; "
    f"font-src {WEB_PORTAL_ORIGIN}; "
    "object-src 'none'; frame-src 'none'; base-uri 'none'; "
    f"form-action {WEB_PORTAL_ORIGIN}"
)
_LIVE_SMOKE_SUCCESS = "success"
_LIVE_SMOKE_INVALID_RESPONSE = "invalid_response"
_LIVE_SMOKE_INTERACTIVE_INPUT_REQUIRED = "interactive_input_required"
_LIVE_SMOKE_TOKEN_INPUT_EMPTY = "token_input_empty"
_LIVE_SMOKE_TOKEN_INPUT_CANCELLED = "token_input_cancelled"
_LIVE_SMOKE_FAILURE_EXIT = 1
_LIVE_SMOKE_INPUT_EXIT = 2


class GuiUnavailableError(RuntimeError):
    """GTK 4 or PyGObject is unavailable for the graphical shell."""


class WebKitUnavailableError(RuntimeError):
    """The pinned WebKitGTK GI API is unavailable for the portal shell."""


class WebPortalAuthBridge:
    """Share companion authentication with only the locked local Portal."""

    _CLEAR_PORTAL_SCRIPT = (
        "if (typeof window.vrHotspotCompanionAuthCleared === 'function') {"
        "window.vrHotspotCompanionAuthCleared();"
        "}"
    )

    def __init__(self, authentication: AuthenticationController):
        self._authentication = authentication
        self._auth_changed = None
        self._web_view = None

    def __repr__(self) -> str:
        return (
            "WebPortalAuthBridge("
            f"view_attached={self._web_view is not None!r})"
        )

    def set_auth_changed_callback(self, callback) -> None:
        self._auth_changed = callback if callable(callback) else None

    def attach_web_view(self, web_view) -> None:
        self._web_view = web_view

    @staticmethod
    def _reply_string(message_value, reply, response: str) -> None:
        try:
            context = message_value.get_context()
            value = type(message_value).new_string(context, response)
            reply.return_value(value)
        except Exception:
            pass

    @staticmethod
    def _page_uri(web_view) -> str:
        try:
            uri = web_view.get_uri()
        except Exception:
            return ""
        return uri if isinstance(uri, str) else ""

    @staticmethod
    def _raw_message(message_value) -> str | None:
        try:
            if message_value.is_string() is not True:
                return None
            raw = message_value.to_string()
        except Exception:
            return None
        if not isinstance(raw, str):
            return None
        try:
            size = len(raw.encode("utf-8"))
        except UnicodeError:
            return None
        if size > MAX_WEB_PORTAL_AUTH_MESSAGE_BYTES:
            return None
        return raw

    @staticmethod
    def _parsed_message(raw: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if type(payload.get("version")) is not int:
            return None
        if payload.get("version") != WEB_PORTAL_AUTH_PROTOCOL_VERSION:
            return None
        message_type = payload.get("type")
        if not isinstance(message_type, str):
            return None
        expected_keys = {
            "token_request": {"version", "type"},
            "auth_accepted": {"version", "type", "token"},
            "auth_cleared": {"version", "type"},
        }.get(message_type)
        if expected_keys is None or set(payload) != expected_keys:
            return None
        if message_type == "auth_accepted":
            token = payload.get("token")
            if (
                not isinstance(token, str)
                or not token
                or len(token) > MAX_WEB_PORTAL_AUTH_TOKEN_CHARS
            ):
                return None
        return payload

    def _emit_auth_changed(self) -> None:
        callback = self._auth_changed
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass

    def handle_message(self, web_view, message_value, reply) -> bool:
        """Handle one bounded message and always return only a fixed reply."""

        if not is_approved_web_portal_bridge_uri(self._page_uri(web_view)):
            self._reply_string(message_value, reply, "rejected")
            return False

        raw = self._raw_message(message_value)
        payload = self._parsed_message(raw) if raw is not None else None
        raw = None
        if payload is None:
            self._reply_string(message_value, reply, "rejected")
            return False

        message_type = payload["type"]
        if message_type == "token_request":
            token = ""
            try:
                candidate = self._authentication.token_for_operation()
                if (
                    isinstance(candidate, str)
                    and 0 < len(candidate) <= MAX_WEB_PORTAL_AUTH_TOKEN_CHARS
                ):
                    token = candidate
            except Exception:
                token = ""
            self._reply_string(message_value, reply, token)
            token = ""
            candidate = None
            return True

        if message_type == "auth_cleared":
            try:
                self._authentication.clear()
            except Exception:
                self._reply_string(message_value, reply, "rejected")
                return False
            self._emit_auth_changed()
            self._reply_string(message_value, reply, "cleared")
            return True

        token = payload["token"]
        accepted = False
        try:
            result = self._authentication.save_or_replace(
                token,
                save_securely=True,
            )
            accepted = (
                result.code in {"saved_securely", "wallet_unavailable"}
                and result.token_available is True
            )
        except Exception:
            accepted = False
        finally:
            payload = None
            token = ""
        if accepted:
            self._emit_auth_changed()
        self._reply_string(
            message_value,
            reply,
            "accepted" if accepted else "rejected",
        )
        return accepted

    def clear_web_portal_session(self) -> None:
        """Clear only the attached fixed-origin page's in-memory auth state."""

        web_view = self._web_view
        if web_view is None:
            return
        if not is_approved_web_portal_bridge_uri(self._page_uri(web_view)):
            return
        try:
            web_view.evaluate_javascript(
                self._CLEAR_PORTAL_SCRIPT,
                -1,
                None,
                f"{WEB_PORTAL_ORIGIN}/companion-auth-bridge",
                None,
                None,
                None,
            )
        except Exception:
            pass


def build_initial_model() -> DiagnosticsControlUiModel:
    """Build deterministic offline/unpaired state without daemon access."""

    return DiagnosticsControlUiController().build(
        pairing_result=FirstRunResult(FirstRunState.INVALID_RESPONSE),
        mode=PresentationMode.BASIC,
    )


def build_smoke_payload() -> dict[str, Any]:
    """Return a bounded, non-secret description of the Flatpak shell."""

    model = build_initial_model()
    return {
        "application": {
            "id": APP_ID,
            "name": APP_NAME,
            "prototype": True,
        },
        "controls": {
            "mutation_actions": [],
            "support_bundle_export_enabled": False,
        },
        "shell": {
            "graphical_shell": "web_portal",
            "origin": WEB_PORTAL_ORIGIN,
            "state": "offline_unpaired",
        },
        "ui": asdict(model),
    }


def render_smoke_json() -> str:
    """Serialize the deterministic smoke payload under its fixed size limit."""

    rendered = json.dumps(
        build_smoke_payload(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(rendered.encode("utf-8")) > MAX_SMOKE_JSON_BYTES:
        raise RuntimeError("The Flatpak shell smoke payload exceeded its size limit.")
    return rendered


def _build_live_smoke_payload(
    *,
    model: DiagnosticsControlUiModel,
    status: str,
) -> dict[str, Any]:
    """Build one bounded-model live smoke result without credential fields."""

    return {
        "application": {
            "id": APP_ID,
            "name": APP_NAME,
        },
        "live_smoke": {
            "status": status,
        },
        "daemon": asdict(model.daemon),
        "pairing": asdict(model.pairing),
        "adapter_readiness": asdict(model.adapters),
        "preflight": asdict(model.preflight),
        "support_bundle": asdict(model.support_bundle),
        "controls": {
            "mutation_actions": [],
            "support_bundle_export_enabled": False,
        },
    }


def _render_live_smoke_json(
    *,
    model: DiagnosticsControlUiModel,
    status: str,
) -> str:
    """Serialize a sanitized live smoke model under a fixed output bound."""

    rendered = json.dumps(
        _build_live_smoke_payload(model=model, status=status),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(rendered.encode("utf-8")) > MAX_LIVE_SMOKE_JSON_BYTES:
        raise RuntimeError("The Flatpak live smoke payload exceeded its size limit.")
    return rendered


def _live_smoke_status(model: DiagnosticsControlUiModel) -> str:
    if model.pairing.paired:
        if (
            model.adapters.severity is StatusSeverity.UNKNOWN
            or model.preflight.severity is StatusSeverity.UNKNOWN
        ):
            return _LIVE_SMOKE_INVALID_RESPONSE
        return _LIVE_SMOKE_SUCCESS

    statuses = {
        "authentication_failed": "token_rejected",
        "daemon_unreachable": "daemon_unreachable",
        "api_token_missing": "daemon_token_missing",
        "unexpected_daemon_response": _LIVE_SMOKE_INVALID_RESPONSE,
    }
    return statuses.get(model.pairing.detail_code, _LIVE_SMOKE_INVALID_RESPONSE)


def _emit_live_smoke(
    *,
    model: DiagnosticsControlUiModel,
    status: str,
    exit_code: int,
) -> int:
    try:
        rendered = _render_live_smoke_json(model=model, status=status)
    except Exception:
        model = build_initial_model()
        status = _LIVE_SMOKE_INVALID_RESPONSE
        exit_code = _LIVE_SMOKE_FAILURE_EXIT
        rendered = _render_live_smoke_json(model=model, status=status)
    print(rendered)
    return exit_code


def _build_live_pairing_model(
    *,
    token: str,
    client_factory,
) -> DiagnosticsControlUiModel:
    """Build bounded read-only smoke state from one explicit in-memory token."""

    try:
        pairing_result = TokenPairingController(client_factory).evaluate(token=token)
    except Exception:
        pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)
    if not isinstance(pairing_result, FirstRunResult):
        pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)

    client = None
    if pairing_result.state is FirstRunState.TOKEN_ACCEPTED:
        try:
            client = client_factory(token=token)
        except Exception:
            pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)

    try:
        return DiagnosticsControlUiController(client).build(
            pairing_result=pairing_result,
            mode=PresentationMode.BASIC,
        )
    finally:
        client = None


def run_live_pairing_smoke_json(
    *,
    input_stream=None,
    token_prompt=None,
    client_factory=None,
) -> int:
    """Prompt for one in-memory token and render authenticated read-only state."""

    stream = sys.stdin if input_stream is None else input_stream
    try:
        interactive = stream.isatty() is True
    except Exception:
        interactive = False
    if not interactive:
        return _emit_live_smoke(
            model=build_initial_model(),
            status=_LIVE_SMOKE_INTERACTIVE_INPUT_REQUIRED,
            exit_code=_LIVE_SMOKE_INPUT_EXIT,
        )

    prompt = getpass.getpass if token_prompt is None else token_prompt
    token = ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = prompt("VRhotspot daemon API token: ")
    except KeyboardInterrupt:
        return _emit_live_smoke(
            model=build_initial_model(),
            status=_LIVE_SMOKE_TOKEN_INPUT_CANCELLED,
            exit_code=_LIVE_SMOKE_INPUT_EXIT,
        )
    except Exception:
        return _emit_live_smoke(
            model=build_initial_model(),
            status=_LIVE_SMOKE_TOKEN_INPUT_CANCELLED,
            exit_code=_LIVE_SMOKE_INPUT_EXIT,
        )

    if not isinstance(token, str) or token == "":
        token = ""
        return _emit_live_smoke(
            model=build_initial_model(),
            status=_LIVE_SMOKE_TOKEN_INPUT_EMPTY,
            exit_code=_LIVE_SMOKE_INPUT_EXIT,
        )

    factory = LocalApiClient if client_factory is None else client_factory
    try:
        try:
            model = _build_live_pairing_model(
                token=token,
                client_factory=factory,
            )
        except KeyboardInterrupt:
            model = build_initial_model()
        except Exception:
            model = build_initial_model()
    finally:
        token = ""

    status = _live_smoke_status(model)
    return _emit_live_smoke(
        model=model,
        status=status,
        exit_code=0 if status == _LIVE_SMOKE_SUCCESS else _LIVE_SMOKE_FAILURE_EXIT,
    )


def _load_gtk():
    """Import GTK only when a graphical entry point is launched."""

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
    except (ImportError, ValueError):
        raise GuiUnavailableError(
            "GTK 4 and PyGObject are required for the graphical shell."
        ) from None
    return Gtk


def _load_webkit():
    """Import the pinned WebKitGTK namespace only for the graphical shell."""

    try:
        import gi

        gi.require_version(WEBKIT_GI_NAMESPACE, WEBKIT_GI_VERSION)
        from gi.repository import WebKit

        if not callable(getattr(WebKit.NetworkSession, "new_ephemeral", None)):
            raise AttributeError
        if not hasattr(WebKit, "WebView") or not hasattr(WebKit, "Settings"):
            raise AttributeError
    except (ImportError, ValueError, AttributeError):
        raise WebKitUnavailableError(
            "WebKitGTK 6.0 is unavailable for the locked Web Portal shell."
        ) from None
    return WebKit


def _load_tray_desktop_modules():
    """Load only the session desktop modules needed by explicit tray mode."""

    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib
    except (ImportError, ValueError):
        raise GuiUnavailableError(
            "GDK, GIO, and GLib are required for tray mode."
        ) from None
    return Gdk, Gio, GLib


def is_approved_web_portal_uri(uri: object) -> bool:
    """Accept only HTTP URLs on the one pinned daemon loopback origin."""

    if not isinstance(uri, str) or not uri:
        return False
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "http"
        and parsed.netloc == "127.0.0.1:8732"
        and parsed.hostname == "127.0.0.1"
        and port == 8732
        and parsed.username is None
        and parsed.password is None
    )


def is_approved_web_portal_bridge_uri(uri: object) -> bool:
    """Restrict companion auth to the Portal document and its asset namespace."""

    if not is_approved_web_portal_uri(uri):
        return False
    try:
        path = urlsplit(uri).path
    except (TypeError, ValueError):
        return False
    return path in {"/ui", "/ui/"} or path.startswith("/assets/")


def _policy_decision_uri(decision, decision_type, WebKit) -> str:
    try:
        if decision_type in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            return decision.get_navigation_action().get_request().get_uri()
        if decision_type == WebKit.PolicyDecisionType.RESPONSE:
            return decision.get_response().get_uri()
    except (AttributeError, TypeError, ValueError):
        return ""
    return ""


def _handle_web_portal_policy(_web_view, decision, decision_type, WebKit) -> bool:
    """Resolve every WebKit policy request explicitly and fail closed."""

    uri = _policy_decision_uri(decision, decision_type, WebKit)
    allowed = (
        decision_type != WebKit.PolicyDecisionType.NEW_WINDOW_ACTION
        and is_approved_web_portal_uri(uri)
    )
    try:
        if allowed:
            decision.use()
        else:
            decision.ignore()
    except (AttributeError, TypeError):
        return True
    return True


def _bounded_web_portal_shell_zoom() -> float:
    """Return the fixed app-shell zoom clamped to its reviewed safe range."""

    return max(
        _WEB_PORTAL_SHELL_ZOOM_MIN,
        min(WEB_PORTAL_SHELL_ZOOM, _WEB_PORTAL_SHELL_ZOOM_MAX),
    )


def _attach_web_portal_auth_bridge(web_view, auth_bridge) -> None:
    """Register one bounded request/reply channel on the locked WebView."""

    try:
        manager = web_view.get_user_content_manager()
        manager.connect(
            f"script-message-with-reply-received::{WEB_PORTAL_AUTH_HANDLER}",
            lambda _manager, value, reply: auth_bridge.handle_message(
                web_view,
                value,
                reply,
            ),
        )
        registered = manager.register_script_message_handler_with_reply(
            WEB_PORTAL_AUTH_HANDLER,
            None,
        )
    except Exception:
        raise WebKitUnavailableError(
            "WebKitGTK did not provide the required local auth bridge."
        ) from None
    if registered is not True:
        raise WebKitUnavailableError(
            "WebKitGTK did not provide the required local auth bridge."
        )
    auth_bridge.attach_web_view(web_view)


def _build_locked_web_portal_view(WebKit, *, auth_bridge=None):
    """Create an ephemeral fixed-origin WebView with an optional auth bridge."""

    network_session = WebKit.NetworkSession.new_ephemeral()
    if network_session.is_ephemeral() is not True:
        raise WebKitUnavailableError(
            "WebKitGTK did not provide an ephemeral network session."
        )
    network_session.set_persistent_credential_storage_enabled(False)
    network_session.get_cookie_manager().set_accept_policy(
        WebKit.CookieAcceptPolicy.NEVER
    )

    settings = WebKit.Settings()
    settings.set_enable_developer_extras(False)
    settings.set_enable_dns_prefetching(False)
    settings.set_enable_offline_web_application_cache(False)
    settings.set_enable_page_cache(False)
    settings.set_enable_back_forward_navigation_gestures(False)
    settings.set_javascript_can_open_windows_automatically(False)
    settings.set_allow_file_access_from_file_urls(False)
    settings.set_allow_universal_access_from_file_urls(False)

    web_view = WebKit.WebView(
        network_session=network_session,
        settings=settings,
        default_content_security_policy=_WEB_PORTAL_CSP,
    )
    web_view.set_zoom_level(_bounded_web_portal_shell_zoom())
    web_view.connect(
        "decide-policy",
        lambda view, decision, decision_type: _handle_web_portal_policy(
            view,
            decision,
            decision_type,
            WebKit,
        ),
    )
    web_view.connect("create", lambda _view, _navigation_action: None)
    web_view.connect("context-menu", lambda *_args: True)

    def deny_permission(_view, request) -> bool:
        try:
            request.deny()
        except (AttributeError, TypeError):
            pass
        return True

    web_view.connect("permission-request", deny_permission)
    if auth_bridge is not None:
        _attach_web_portal_auth_bridge(web_view, auth_bridge)
    return web_view


def _add_text_label(Gtk, container, text: str, *, css_class: str | None = None):
    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_xalign(0.0)
    if css_class:
        label.add_css_class(css_class)
    container.append(label)
    return label


def _clear_box(container) -> None:
    child = container.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        container.remove(child)
        child = next_child


def _populate_web_portal_error(Gtk, window) -> None:
    """Show a fixed, bounded error surface without rendering exception details."""

    window.set_title(APP_NAME)
    window.set_default_size(720, 360)
    error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    error_box.set_margin_top(36)
    error_box.set_margin_bottom(36)
    error_box.set_margin_start(36)
    error_box.set_margin_end(36)
    _add_text_label(
        Gtk,
        error_box,
        "Web Portal shell unavailable",
        css_class="title-2",
    )
    _add_text_label(
        Gtk,
        error_box,
        "VR Hotspot could not create its locked local WebKit window. "
        "Confirm that WebKitGTK 6.0 is installed, then restart the companion. "
        "No alternate interface or external site was opened.",
    )
    window.set_child(error_box)


def _populate_web_portal_window(
    Gtk,
    WebKit,
    window,
    *,
    auth_bridge=None,
) -> bool:
    """Populate the locked portal shell or a bounded error surface."""

    window.set_title(APP_NAME)
    window.set_default_size(1200, 900)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    if WebKit is None:
        _populate_web_portal_error(Gtk, window)
        return False
    try:
        web_view = _build_locked_web_portal_view(
            WebKit,
            auth_bridge=auth_bridge,
        )
    except Exception:
        _populate_web_portal_error(Gtk, window)
        return False

    web_view.set_hexpand(True)
    web_view.set_vexpand(True)

    def show_portal() -> None:
        _clear_box(root)
        root.append(web_view)

    def show_unreachable() -> None:
        _clear_box(root)
        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        error_box.set_margin_top(36)
        error_box.set_margin_bottom(36)
        error_box.set_margin_start(36)
        error_box.set_margin_end(36)
        _add_text_label(
            Gtk,
            error_box,
            "Local Web Portal unavailable",
            css_class="title-2",
        )
        _add_text_label(
            Gtk,
            error_box,
            "The Flatpak could not load the local daemon Web Portal at "
            "127.0.0.1:8732. Confirm that vr-hotspotd is installed and running, "
            "then retry. No external site was opened.",
        )
        retry_button = Gtk.Button(label="Retry local portal")

        def on_retry(_button) -> None:
            show_portal()
            web_view.load_uri(WEB_PORTAL_URL)

        retry_button.connect("clicked", on_retry)
        error_box.append(retry_button)
        root.append(error_box)

    def on_load_failed(_view, _load_event, failing_uri, _error) -> bool:
        if is_approved_web_portal_uri(failing_uri):
            show_unreachable()
        return True

    web_view.connect("load-failed", on_load_failed)
    show_portal()
    window.set_child(root)
    web_view.load_uri(WEB_PORTAL_URL)
    return True


def _populate_new_portal_window(Gtk, window, *, auth_bridge=None) -> bool:
    """Load WebKit lazily and always leave the window with bounded content."""

    try:
        WebKit = _load_webkit()
    except Exception:
        WebKit = None
    return _populate_web_portal_window(
        Gtk,
        WebKit,
        window,
        auth_bridge=auth_bridge,
    )


def _set_window_icon(Gtk, window) -> None:
    """Keep the window/taskbar identity on the stable base application icon."""

    Gtk.Window.set_default_icon_name(WINDOW_ICON_NAME)
    window.set_icon_name(WINDOW_ICON_NAME)


def run_web_portal_shell() -> int:
    """Run the only Flatpak graphical UI inside a locked WebKit view."""

    Gtk = _load_gtk()
    application = Gtk.Application(application_id=APP_ID)
    window_holder: dict[str, Any] = {}
    authentication = AuthenticationController()
    auth_bridge = WebPortalAuthBridge(authentication)

    def on_activate(app) -> None:
        existing = window_holder.get("window")
        if existing is not None:
            existing.present()
            return

        window = Gtk.ApplicationWindow(application=app)
        _set_window_icon(Gtk, window)
        _populate_new_portal_window(
            Gtk,
            window,
            auth_bridge=auth_bridge,
        )

        def clear_window(*_args):
            window_holder.pop("window", None)
            return False

        window.connect("close-request", clear_window)
        window_holder["window"] = window
        window.present()

    application.connect("activate", on_activate)
    return int(application.run([]))


def run_tray() -> int:
    """Run the Web Portal window with a persistent StatusNotifierItem."""

    Gtk = _load_gtk()
    Gdk, Gio, GLib = _load_tray_desktop_modules()
    from .tray import (
        StatusNotifierBackend,
        TrayRuntime,
        WindowLifecycleController,
        build_tray_menu_model,
    )

    application = Gtk.Application(application_id=APP_ID)
    runtime_holder: dict[str, Any] = {}

    def on_activate(app) -> None:
        existing = runtime_holder.get("runtime")
        if existing is not None:
            existing.show()
            return

        window = Gtk.ApplicationWindow(application=app)
        _set_window_icon(Gtk, window)
        authentication = AuthenticationController()
        auth_bridge = WebPortalAuthBridge(authentication)
        _populate_new_portal_window(
            Gtk,
            window,
            auth_bridge=auth_bridge,
        )
        controls = TrayControlController(authentication)
        lifecycle = WindowLifecycleController(
            application=app,
            window=window,
        )

        def open_diagnostics() -> None:
            lifecycle.show()

        initial_menu = build_tray_menu_model(
            controls.state,
            window_visible=True,
        )
        backend = StatusNotifierBackend(
            Gio=Gio,
            GLib=GLib,
            model=initial_menu,
            on_action=lambda action: runtime_holder["runtime"].dispatch_action(
                action
            ),
            on_activate=lambda: runtime_holder["runtime"].show(),
        )
        runtime = TrayRuntime(
            application=app,
            lifecycle=lifecycle,
            controls=controls,
            authentication=authentication,
            backend=backend,
            Gtk=Gtk,
            Gdk=Gdk,
            Gio=Gio,
            GLib=GLib,
            open_diagnostics=open_diagnostics,
            on_auth_cleared=auth_bridge.clear_web_portal_session,
        )
        auth_bridge.set_auth_changed_callback(runtime.refresh_after_auth_change)
        runtime_holder["runtime"] = runtime
        window.connect("close-request", runtime.close_request)
        lifecycle.show()
        runtime.start()

    application.connect("activate", on_activate)
    return int(application.run([]))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vrhotspot-flatpak",
        description="VR Hotspot Flatpak Web Portal shell",
    )
    parser.add_argument(
        "--smoke-json",
        action="store_true",
        help="print a bounded offline shell model as JSON and exit",
    )
    parser.add_argument(
        "--live-pairing-smoke-json",
        action="store_true",
        help=(
            "prompt interactively for an in-memory daemon token, print bounded "
            "authenticated read-only state as JSON, and exit"
        ),
    )
    parser.add_argument(
        "--web-portal-shell",
        action="store_true",
        help="compatibility alias for the default graphical shell",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="launch the persistent system-tray control companion",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a smoke path, tray companion, or the default Web Portal shell."""

    args = _argument_parser().parse_args(argv)
    if args.smoke_json:
        print(render_smoke_json())
        return 0
    if args.live_pairing_smoke_json:
        return run_live_pairing_smoke_json()
    if args.tray:
        try:
            return run_tray()
        except GuiUnavailableError:
            pass

    try:
        return run_web_portal_shell()
    except GuiUnavailableError:
        print(
            "The Web Portal shell requires GTK 4, WebKitGTK 6.0, and PyGObject. "
            "Use --smoke-json for the offline shell check.",
            file=sys.stderr,
        )
        return 2
