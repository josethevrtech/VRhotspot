"""Tray/menu presentation and optional StatusNotifierItem desktop backend."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from flatpak_client import (
    ActionOutcome,
    AuthenticationController,
    FirstRunState,
    TrayControlController,
    TrayState,
    TrayStatus,
)


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
ICON_NAMES = {
    TrayStatus.STOPPED: APP_ID,
    TrayStatus.RUNNING: f"{APP_ID}-running",
    TrayStatus.TRANSITIONING: f"{APP_ID}-working",
    TrayStatus.ERROR: f"{APP_ID}-error",
}


@dataclass(frozen=True)
class TrayMenuItem:
    item_id: int
    action: str
    label: str
    enabled: bool = True
    checked: bool | None = None
    separator: bool = False


@dataclass(frozen=True)
class TrayMenuModel:
    items: tuple[TrayMenuItem, ...]
    icon_name: str
    notifier_status: str
    tooltip: str

    def action_labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.items if not item.separator)


def build_tray_menu_model(
    state: TrayState,
    *,
    window_visible: bool,
) -> TrayMenuModel:
    """Build a deterministic menu independent of GTK and the tray backend."""

    ready = (
        state.daemon_available
        and state.authenticated
        and state.busy_action is None
        and state.status is not TrayStatus.TRANSITIONING
    )
    items = (
        TrayMenuItem(1, "show", "Show VR Hotspot", not window_visible),
        TrayMenuItem(2, "hide", "Hide VR Hotspot", window_visible),
        TrayMenuItem(3, "separator-1", "", separator=True),
        TrayMenuItem(
            4,
            "status",
            f"Current status: {state.status_label}",
            enabled=False,
        ),
        TrayMenuItem(5, "separator-2", "", separator=True),
        TrayMenuItem(
            6,
            "start",
            "Start Hotspot",
            enabled=ready and state.status is TrayStatus.STOPPED,
        ),
        TrayMenuItem(
            7,
            "stop",
            "Stop Hotspot",
            enabled=ready and state.status is TrayStatus.RUNNING,
        ),
        TrayMenuItem(
            8,
            "restart",
            "Restart Service",
            enabled=ready and state.status is TrayStatus.RUNNING,
        ),
        TrayMenuItem(9, "repair", "Repair Network", enabled=ready),
        TrayMenuItem(
            10,
            "refresh",
            "Refresh Status",
            enabled=state.busy_action is None,
        ),
        TrayMenuItem(11, "separator-3", "", separator=True),
        TrayMenuItem(
            12,
            "share_internet",
            "Share Internet Connection",
            enabled=ready and state.share_internet is not None,
            checked=state.share_internet is True,
        ),
        TrayMenuItem(
            13,
            "privacy",
            "Privacy Mode",
            checked=state.privacy_mode,
        ),
        TrayMenuItem(
            14,
            "hotspot_autostart",
            "Start Hotspot Automatically",
            enabled=ready and state.hotspot_autostart is not None,
            checked=state.hotspot_autostart is True,
        ),
        TrayMenuItem(15, "separator-4", "", separator=True),
        TrayMenuItem(16, "authentication", "Authentication…"),
        TrayMenuItem(17, "diagnostics", "Open Diagnostics"),
        TrayMenuItem(18, "web_portal", "Open Web Portal Shell"),
        TrayMenuItem(19, "separator-5", "", separator=True),
        TrayMenuItem(20, "quit", "Quit VR Hotspot"),
    )
    return TrayMenuModel(
        items=items,
        icon_name=ICON_NAMES[state.status],
        notifier_status=(
            "NeedsAttention" if state.status is TrayStatus.ERROR else "Active"
        ),
        tooltip=f"{APP_NAME} — {state.status_label}",
    )


class WindowLifecycleController:
    """Show, hide, close-to-tray, and companion-only quit behavior."""

    def __init__(self, *, application, window, tray_active: bool = False):
        self._application = application
        self._window = window
        self._tray_active = tray_active
        self._visible = False

    def __repr__(self) -> str:
        return (
            "WindowLifecycleController("
            f"tray_active={self._tray_active!r}, visible={self._visible!r})"
        )

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def tray_active(self) -> bool:
        return self._tray_active

    @property
    def window(self):
        return self._window

    def set_tray_active(self, active: bool) -> None:
        self._tray_active = active is True

    def show(self) -> None:
        self._window.present()
        self._visible = True

    def hide(self) -> None:
        self._window.hide()
        self._visible = False

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def close_request(self, *_args) -> bool:
        if not self._tray_active:
            return False
        self.hide()
        return True

    def quit_companion(self) -> None:
        self._application.quit()


_STATUS_NOTIFIER_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""

_DBUS_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})"/><arg type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  </interface>
</node>
"""


class StatusNotifierBackend:
    """Minimal standards-compatible StatusNotifierItem and D-Bus menu backend."""

    ITEM_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"
    ITEM_INTERFACE = "org.kde.StatusNotifierItem"
    MENU_INTERFACE = "com.canonical.dbusmenu"

    def __init__(
        self,
        *,
        Gio,
        GLib,
        model: TrayMenuModel,
        on_action: Callable[[str], None],
        on_activate: Callable[[], None],
    ):
        self._Gio = Gio
        self._GLib = GLib
        self._model = model
        self._on_action = on_action
        self._on_activate = on_activate
        self._connection = None
        self._registrations: list[int] = []
        self._revision = 1

    def __repr__(self) -> str:
        return (
            "StatusNotifierBackend("
            f"registered={bool(self._registrations)!r}, revision={self._revision!r})"
        )

    def start(self) -> bool:
        connection = None
        registrations: list[int] = []
        try:
            connection = self._Gio.bus_get_sync(
                self._Gio.BusType.SESSION,
                None,
            )
            item_info = self._Gio.DBusNodeInfo.new_for_xml(
                _STATUS_NOTIFIER_XML
            ).interfaces[0]
            menu_info = self._Gio.DBusNodeInfo.new_for_xml(
                _DBUS_MENU_XML
            ).interfaces[0]
            item_id = connection.register_object(
                self.ITEM_PATH,
                item_info,
                self._handle_item_method,
                self._get_item_property,
                None,
            )
            if not item_id:
                raise RuntimeError
            registrations.append(item_id)
            menu_id = connection.register_object(
                self.MENU_PATH,
                menu_info,
                self._handle_menu_method,
                self._get_menu_property,
                None,
            )
            if not menu_id:
                raise RuntimeError
            registrations.append(menu_id)
            self._connection = connection
            self._registrations = registrations
            connection.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                self._GLib.Variant("(s)", (APP_ID,)),
                None,
                self._Gio.DBusCallFlags.NONE,
                5_000,
                None,
            )
            return True
        except Exception:
            if connection is not None:
                for registration in registrations:
                    try:
                        connection.unregister_object(registration)
                    except Exception:
                        pass
            self._registrations = []
            self._connection = None
            self.stop()
            return False

    def stop(self) -> None:
        if self._connection is not None:
            for registration in self._registrations:
                try:
                    self._connection.unregister_object(registration)
                except Exception:
                    pass
        self._registrations = []
        self._connection = None

    def update(self, model: TrayMenuModel) -> None:
        icon_changed = model.icon_name != self._model.icon_name
        status_changed = model.notifier_status != self._model.notifier_status
        tooltip_changed = model.tooltip != self._model.tooltip
        self._model = model
        self._revision += 1
        connection = self._connection
        if connection is None:
            return
        try:
            connection.emit_signal(
                None,
                self.MENU_PATH,
                self.MENU_INTERFACE,
                "LayoutUpdated",
                self._GLib.Variant("(ui)", (self._revision, 0)),
            )
            if icon_changed:
                connection.emit_signal(
                    None,
                    self.ITEM_PATH,
                    self.ITEM_INTERFACE,
                    "NewIcon",
                    None,
                )
            if status_changed:
                connection.emit_signal(
                    None,
                    self.ITEM_PATH,
                    self.ITEM_INTERFACE,
                    "NewStatus",
                    self._GLib.Variant("(s)", (model.notifier_status,)),
                )
            if tooltip_changed:
                connection.emit_signal(
                    None,
                    self.ITEM_PATH,
                    self.ITEM_INTERFACE,
                    "NewToolTip",
                    None,
                )
        except Exception:
            pass

    def _get_item_property(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        property_name,
    ):
        Variant = self._GLib.Variant
        properties = {
            "Category": Variant("s", "ApplicationStatus"),
            "Id": Variant("s", APP_ID),
            "Title": Variant("s", APP_NAME),
            "Status": Variant("s", self._model.notifier_status),
            "WindowId": Variant("u", 0),
            "IconName": Variant("s", self._model.icon_name),
            "IconPixmap": Variant("a(iiay)", []),
            "OverlayIconName": Variant("s", ""),
            "OverlayIconPixmap": Variant("a(iiay)", []),
            "AttentionIconName": Variant("s", f"{APP_ID}-error"),
            "AttentionIconPixmap": Variant("a(iiay)", []),
            "AttentionMovieName": Variant("s", ""),
            "ToolTip": Variant(
                "(sa(iiay)ss)",
                (
                    self._model.icon_name,
                    [],
                    APP_NAME,
                    self._model.tooltip,
                ),
            ),
            "ItemIsMenu": Variant("b", False),
            "Menu": Variant("o", self.MENU_PATH),
        }
        return properties.get(property_name)

    def _handle_item_method(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        method_name,
        _parameters,
        invocation,
    ) -> None:
        if method_name in {"Activate", "SecondaryActivate"}:
            try:
                self._on_activate()
            except Exception:
                pass
        invocation.return_value(None)

    def _item_by_id(self, item_id: int) -> TrayMenuItem | None:
        return next(
            (item for item in self._model.items if item.item_id == item_id),
            None,
        )

    def _menu_properties(
        self,
        item: TrayMenuItem,
        property_names: tuple[str, ...] = (),
    ) -> dict:
        Variant = self._GLib.Variant
        if item.separator:
            values = {"type": Variant("s", "separator")}
        else:
            values = {
                "label": Variant("s", item.label),
                "enabled": Variant("b", item.enabled),
                "visible": Variant("b", True),
            }
            if item.checked is not None:
                values["toggle-type"] = Variant("s", "checkmark")
                values["toggle-state"] = Variant("i", 1 if item.checked else 0)
        if property_names:
            allowed = set(property_names)
            values = {key: value for key, value in values.items() if key in allowed}
        return values

    def _layout(self):
        Variant = self._GLib.Variant
        children = [
            Variant(
                "(ia{sv}av)",
                (item.item_id, self._menu_properties(item), []),
            )
            for item in self._model.items
        ]
        return (
            0,
            {"children-display": Variant("s", "submenu")},
            children,
        )

    def _get_menu_property(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        property_name,
    ):
        Variant = self._GLib.Variant
        return {
            "Version": Variant("u", 3),
            "TextDirection": Variant("s", "ltr"),
            "Status": Variant("s", "normal"),
            "IconThemePath": Variant("as", ["/app/share/icons/hicolor"]),
        }.get(property_name)

    def _dispatch_event(self, item_id: int, event_id: str) -> bool:
        item = self._item_by_id(item_id)
        if (
            item is None
            or item.separator
            or not item.enabled
            or event_id not in {"clicked", "activated"}
        ):
            return False
        try:
            self._on_action(item.action)
        except Exception:
            return False
        return True

    def _handle_menu_method(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        Variant = self._GLib.Variant
        values = parameters.unpack()
        if method_name == "GetLayout":
            invocation.return_value(
                Variant("(u(ia{sv}av))", (self._revision, self._layout()))
            )
            return
        if method_name == "GetGroupProperties":
            ids, property_names = values
            rows = []
            for item_id in ids:
                item = self._item_by_id(item_id)
                if item is not None:
                    rows.append(
                        (
                            item.item_id,
                            self._menu_properties(item, tuple(property_names)),
                        )
                    )
            invocation.return_value(Variant("(a(ia{sv}))", (rows,)))
            return
        if method_name == "GetProperty":
            item_id, property_name = values
            item = self._item_by_id(item_id)
            value = None
            if item is not None:
                value = self._menu_properties(item).get(property_name)
            invocation.return_value(
                Variant(
                    "(v)",
                    (
                        value
                        if value is not None
                        else Variant("s", ""),
                    ),
                )
            )
            return
        if method_name == "Event":
            item_id, event_id, _data, _timestamp = values
            self._dispatch_event(item_id, event_id)
            invocation.return_value(None)
            return
        if method_name == "EventGroup":
            events = values[0]
            errors = [
                item_id
                for item_id, event_id, _data, _timestamp in events
                if not self._dispatch_event(item_id, event_id)
            ]
            invocation.return_value(Variant("(ai)", (errors,)))
            return
        if method_name == "AboutToShow":
            invocation.return_value(Variant("(b)", (False,)))
            return
        if method_name == "AboutToShowGroup":
            invocation.return_value(Variant("(aiai)", ([], [])))
            return
        invocation.return_dbus_error(
            "com.canonical.dbusmenu.Error.UnknownMethod",
            "Unsupported menu method.",
        )


class TrayRuntime:
    """Connect the testable model/controller to GTK and StatusNotifierItem."""

    def __init__(
        self,
        *,
        application,
        lifecycle: WindowLifecycleController,
        controls: TrayControlController,
        authentication: AuthenticationController,
        backend: StatusNotifierBackend,
        Gtk,
        Gdk,
        Gio,
        GLib,
        open_diagnostics: Callable[[], None],
        open_web_portal: Callable[[], None],
    ):
        self._application = application
        self._lifecycle = lifecycle
        self._controls = controls
        self._authentication = authentication
        self._backend = backend
        self._Gtk = Gtk
        self._Gdk = Gdk
        self._Gio = Gio
        self._GLib = GLib
        self._open_diagnostics = open_diagnostics
        self._open_web_portal = open_web_portal
        self._worker_lock = threading.Lock()
        self._notification_counter = 0
        self._last_detail_code = ""
        self._auth_window = None

    def __repr__(self) -> str:
        return (
            "TrayRuntime("
            f"tray_active={self._lifecycle.tray_active!r}, "
            f"worker_active={self._worker_lock.locked()!r})"
        )

    def start(self) -> bool:
        active = self._backend.start()
        self._lifecycle.set_tray_active(active)
        if active:
            self._application.hold()
            self._GLib.timeout_add_seconds(5, self._periodic_refresh)
        self._update_menu()
        self.refresh_async()
        return active

    def _periodic_refresh(self) -> bool:
        self.refresh_async()
        return self._lifecycle.tray_active

    def _update_menu(self) -> None:
        self._backend.update(
            build_tray_menu_model(
                self._controls.state,
                window_visible=self._lifecycle.visible,
            )
        )

    def show(self) -> None:
        self._lifecycle.show()
        self._update_menu()

    def hide(self) -> None:
        self._lifecycle.hide()
        self._update_menu()

    def close_request(self, *_args) -> bool:
        """Hide to the active tray and immediately refresh exported menu state."""

        handled = self._lifecycle.close_request(*_args)
        if handled:
            self._update_menu()
        return handled

    def _notify(self, outcome: ActionOutcome) -> None:
        title = ""
        body = ""
        if outcome.succeeded and outcome.code == "hotspot_started":
            title, body = "VR Hotspot", "Hotspot started."
        elif outcome.succeeded and outcome.code == "hotspot_stopped":
            title, body = "VR Hotspot", "Hotspot stopped."
        elif not outcome.succeeded and outcome.code == "daemon_unavailable":
            title, body = "VR Hotspot unavailable", "The local daemon is unavailable."
        elif not outcome.succeeded:
            title, body = "VR Hotspot operation failed", "The requested operation failed."
        if not title:
            return
        try:
            notification = self._Gio.Notification.new(title)
            notification.set_body(body)
            notification.set_icon(self._Gio.ThemedIcon.new(APP_ID))
            self._notification_counter += 1
            self._application.send_notification(
                f"tray-result-{self._notification_counter}",
                notification,
            )
        except Exception:
            pass

    def _finish_worker(self, outcome: ActionOutcome | None = None) -> bool:
        try:
            detail_code = self._controls.state.detail_code
            if (
                detail_code == "daemon_unavailable"
                and detail_code != self._last_detail_code
                and (
                    outcome is None
                    or outcome.code != "daemon_unavailable"
                )
            ):
                self._notify(
                    ActionOutcome(
                        accepted=True,
                        succeeded=False,
                        code="daemon_unavailable",
                        message="The local daemon is unavailable.",
                        state=self._controls.state,
                    )
                )
            if outcome is not None:
                self._notify(outcome)
            self._last_detail_code = detail_code
            self._update_menu()
        finally:
            if self._worker_lock.locked():
                self._worker_lock.release()
        return False

    def _run_worker(
        self,
        call: Callable[[], object],
        *,
        pending_action: str | None = None,
    ) -> None:
        if not self._worker_lock.acquire(blocking=False):
            return
        if pending_action is not None:
            self._controls.mark_operation_pending(pending_action)
        self._update_menu()

        def worker() -> None:
            outcome = None
            try:
                value = call()
                if isinstance(value, ActionOutcome):
                    outcome = value
            except Exception:
                outcome = ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="operation_failed",
                    message="The requested operation failed.",
                    state=self._controls.state,
                )
            self._GLib.idle_add(self._finish_worker, outcome)

        threading.Thread(
            target=worker,
            name="vrhotspot-tray-operation",
            daemon=True,
        ).start()

    def refresh_async(self) -> None:
        self._run_worker(self._controls.refresh)

    def _open_authentication(self) -> None:
        if self._auth_window is not None:
            self._auth_window.present()
            return

        Gtk = self._Gtk
        window = Gtk.Window(application=self._application)
        window.set_title("VR Hotspot Authentication")
        window.set_default_size(560, 330)
        window.set_transient_for(self._lifecycle.window)
        window.set_modal(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        heading = Gtk.Label(label="Authentication")
        heading.set_xalign(0.0)
        heading.add_css_class("title-2")
        content.append(heading)
        description = Gtk.Label(
            label=(
                "Enter the existing daemon API token. It remains in memory "
                "unless secure wallet storage is explicitly selected."
            )
        )
        description.set_wrap(True)
        description.set_xalign(0.0)
        content.append(description)

        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        entry.set_hexpand(True)
        content.append(entry)

        save_securely = Gtk.CheckButton(
            label="Save API token securely in system wallet"
        )
        save_securely.set_active(self._authentication.securely_saved)
        content.append(save_securely)

        status = Gtk.Label(label="")
        status.set_wrap(True)
        status.set_xalign(0.0)
        content.append(status)

        buttons = Gtk.FlowBox()
        buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        for label, handler in (
            (
                "Save or replace token",
                lambda *_args: self._auth_save(
                    entry,
                    save_securely,
                    status,
                ),
            ),
            (
                "Test authentication",
                lambda *_args: self._auth_test(entry, status),
            ),
            (
                "Copy saved token",
                lambda *_args: self._auth_copy(status),
            ),
            (
                "Reveal saved token",
                lambda *_args: self._auth_reveal(entry, status),
            ),
            (
                "Clear saved token",
                lambda *_args: self._auth_clear(entry, status),
            ),
            ("Close", lambda *_args: window.close()),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            buttons.insert(button, -1)
        content.append(buttons)
        window.set_child(content)

        def closed(*_args):
            entry.set_text("")
            self._auth_window = None
            return False

        window.connect("close-request", closed)
        self._auth_window = window
        window.present()

    def _auth_save(self, entry, save_securely, status) -> None:
        token = entry.get_text()
        entry.set_text("")
        try:
            result = self._authentication.save_or_replace(
                token,
                save_securely=save_securely.get_active() is True,
            )
        except Exception:
            status.set_text("The API token could not be saved.")
            return
        finally:
            token = ""
        status.set_text(result.message)
        save_securely.set_active(result.securely_saved)
        self.refresh_async()

    def _auth_test(self, entry, status) -> None:
        token = entry.get_text()
        entry.set_text("")
        try:
            result = self._authentication.test_authentication(
                explicit_token=token or None
            )
        except Exception:
            status.set_text("Authentication could not be tested safely.")
            return
        finally:
            token = ""
        messages = {
            FirstRunState.TOKEN_ACCEPTED: "Authentication succeeded.",
            FirstRunState.TOKEN_REJECTED: "Authentication was rejected.",
            FirstRunState.DAEMON_UNREACHABLE: "The local daemon is unavailable.",
            FirstRunState.DAEMON_TOKEN_MISSING: (
                "The daemon has no configured API token."
            ),
            FirstRunState.DAEMON_REACHABLE_UNPAIRED: "Enter an API token first.",
            FirstRunState.INVALID_RESPONSE: (
                "The daemon returned an unsupported response."
            ),
        }
        status.set_text(messages[result.state])
        self.refresh_async()

    def _auth_copy(self, status) -> None:
        token = self._authentication.copy_token()
        if not token:
            status.set_text("No saved API token is available.")
            return
        try:
            display = self._Gdk.Display.get_default()
            if display is None:
                raise RuntimeError
            display.get_clipboard().set(token)
            status.set_text("API token copied to the clipboard.")
        except Exception:
            status.set_text("The API token could not be copied.")
        finally:
            token = ""

    def _auth_reveal(self, entry, status) -> None:
        token = self._authentication.reveal_token()
        if not token:
            status.set_text("No saved API token is available.")
            return
        entry.set_text(token)
        status.set_text("API token revealed by explicit request.")
        token = ""

    def _auth_clear(self, entry, status) -> None:
        entry.set_text("")
        result = self._authentication.clear()
        status.set_text(result.message)
        self.refresh_async()

    def dispatch_action(self, action: str) -> None:
        state = self._controls.state
        if action == "show":
            self.show()
        elif action == "hide":
            self.hide()
        elif action == "start":
            self._run_worker(
                lambda: self._controls.perform("start"),
                pending_action="start",
            )
        elif action == "stop":
            self._run_worker(
                lambda: self._controls.perform("stop"),
                pending_action="stop",
            )
        elif action == "restart":
            self._run_worker(
                lambda: self._controls.perform("restart"),
                pending_action="restart",
            )
        elif action == "repair":
            self._run_worker(
                lambda: self._controls.perform("repair"),
                pending_action="repair",
            )
        elif action == "refresh":
            self.refresh_async()
        elif action == "share_internet":
            self._run_worker(
                lambda: self._controls.perform(
                    "share_internet",
                    enabled=not bool(state.share_internet),
                ),
                pending_action="share_internet",
            )
        elif action == "privacy":
            self._controls.set_privacy_mode(not state.privacy_mode)
            self._update_menu()
        elif action == "hotspot_autostart":
            self._run_worker(
                lambda: self._controls.perform(
                    "hotspot_autostart",
                    enabled=not bool(state.hotspot_autostart),
                ),
                pending_action="hotspot_autostart",
            )
        elif action == "authentication":
            self._open_authentication()
        elif action == "diagnostics":
            self._open_diagnostics()
        elif action == "web_portal":
            self._open_web_portal()
        elif action == "quit":
            self._backend.stop()
            self._lifecycle.quit_companion()
