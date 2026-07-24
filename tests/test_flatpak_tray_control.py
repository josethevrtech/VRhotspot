import json
from pathlib import Path
import threading
import xml.etree.ElementTree as ET

import pytest

from flatpak_app.tray import (
    ICON_NAMES,
    StatusNotifierBackend,
    TrayRuntime,
    WindowLifecycleController,
    build_tray_menu_model,
)
from flatpak_client import (
    ActionOutcome,
    ApiResponse,
    AuthenticationError,
    ConnectionFailure,
    TrayControlController,
    TrayState,
    TrayStatus,
)


def _response(data=None, *, result_code="ok"):
    return ApiResponse(
        correlation_id="tray-test",
        result_code=result_code,
        warnings=(),
        data=data or {},
    )


class TokenProvider:
    def __init__(self, token="explicit-token"):
        self.token = token

    def token_for_operation(self):
        return self.token


class FakeControlClient:
    def __init__(
        self,
        *,
        phase="stopped",
        running=False,
        share_internet=True,
        autostart=False,
        status_error=None,
        blocker=None,
    ):
        self.phase = phase
        self.running = running
        self.share_internet = share_internet
        self.autostart = autostart
        self.status_error = status_error
        self.blocker = blocker
        self.calls = []
        self.status_log_requests = []

    def status(self, *, include_logs=False):
        self.calls.append(("status",))
        self.status_log_requests.append(include_logs)
        if self.status_error is not None:
            raise self.status_error
        return _response({"phase": self.phase, "running": self.running})

    def config(self):
        self.calls.append(("config",))
        return _response(
            {
                "enable_internet": self.share_internet,
                "autostart": self.autostart,
            }
        )

    def start_hotspot(self):
        self.calls.append(("start_hotspot",))
        if self.blocker is not None:
            self.blocker[0].set()
            self.blocker[1].wait(timeout=5)
        self.phase = "running"
        self.running = True
        return _response(result_code="started")

    def stop_hotspot(self):
        self.calls.append(("stop_hotspot",))
        self.phase = "stopped"
        self.running = False
        return _response(result_code="stopped")

    def restart_service(self):
        self.calls.append(("restart_service",))
        self.phase = "running"
        self.running = True
        return _response(result_code="restarted:started")

    def repair_network(self):
        self.calls.append(("repair_network",))
        return _response(result_code="repaired")

    def set_share_internet(self, enabled):
        self.calls.append(("set_share_internet", enabled))
        self.share_internet = enabled
        return _response(result_code="config_saved")

    def set_hotspot_autostart(self, enabled):
        self.calls.append(("set_hotspot_autostart", enabled))
        self.autostart = enabled
        return _response(
            result_code=(
                "autostart_enabled" if enabled else "autostart_disabled"
            )
        )


def _controller(client, *, token="explicit-token"):
    return TrayControlController(
        TokenProvider(token),
        client_factory=lambda *, token: client,
    )


def test_tray_menu_contains_every_required_action():
    model = build_tray_menu_model(
        TrayState(
            status=TrayStatus.RUNNING,
            status_label="Running",
            phase="running",
            running=True,
            daemon_available=True,
            authenticated=True,
            share_internet=True,
            hotspot_autostart=False,
        ),
        window_visible=True,
    )

    assert model.action_labels() == (
        "Show VR Hotspot",
        "Hide VR Hotspot",
        "Current status: Running",
        "Start Hotspot",
        "Stop Hotspot",
        "Restart Service",
        "Repair Network",
        "Refresh Status",
        "Share Internet Connection",
        "Privacy Mode",
        "Start Hotspot Automatically",
        "Authentication…",
        "Open Diagnostics",
        "Open Web Portal Shell",
        "Quit VR Hotspot",
    )


@pytest.mark.parametrize(
    ("status", "label", "notifier_status"),
    (
        (TrayStatus.RUNNING, "Running", "Active"),
        (TrayStatus.STOPPED, "Stopped", "Active"),
        (TrayStatus.TRANSITIONING, "Transitioning", "Active"),
        (TrayStatus.ERROR, "Error", "NeedsAttention"),
    ),
)
def test_tray_status_labels_and_icon_variants_reflect_daemon_state(
    status,
    label,
    notifier_status,
):
    model = build_tray_menu_model(
        TrayState(status=status, status_label=label),
        window_visible=False,
    )

    assert f"Current status: {label}" in model.action_labels()
    assert model.icon_name == ICON_NAMES[status]
    assert model.notifier_status == notifier_status


class FakeWindow:
    def __init__(self):
        self.present_calls = 0
        self.hide_calls = 0

    def present(self):
        self.present_calls += 1

    def hide(self):
        self.hide_calls += 1


class FakeApplication:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


def test_show_hide_toggle_and_close_to_tray_behavior():
    window = FakeWindow()
    application = FakeApplication()
    lifecycle = WindowLifecycleController(
        application=application,
        window=window,
        tray_active=True,
    )

    lifecycle.show()
    lifecycle.toggle()
    lifecycle.toggle()
    close_handled = lifecycle.close_request()

    assert window.present_calls == 2
    assert window.hide_calls == 2
    assert lifecycle.visible is False
    assert close_handled is True
    assert application.quit_calls == 0


def test_close_is_not_intercepted_when_tray_backend_is_unavailable():
    lifecycle = WindowLifecycleController(
        application=FakeApplication(),
        window=FakeWindow(),
        tray_active=False,
    )

    assert lifecycle.close_request() is False


@pytest.mark.parametrize(
    ("action", "expected"),
    (
        ("start", ("start_hotspot",)),
        ("stop", ("stop_hotspot",)),
        ("restart", ("restart_service",)),
        ("repair", ("repair_network",)),
    ),
)
def test_lifecycle_actions_call_only_their_intended_authenticated_method(
    action,
    expected,
):
    client = FakeControlClient(phase="running", running=True)
    controller = _controller(client)

    outcome = controller.perform(action)

    assert outcome.succeeded is True
    assert client.calls[0] == expected
    assert client.calls[1:] == [("status",), ("config",)]


def test_repeated_mutation_click_is_blocked_while_first_action_is_pending():
    started = threading.Event()
    release = threading.Event()
    client = FakeControlClient(blocker=(started, release))
    controller = _controller(client)
    results = []

    thread = threading.Thread(
        target=lambda: results.append(controller.perform("start"))
    )
    thread.start()
    assert started.wait(timeout=2)

    repeated = controller.perform("repair")
    release.set()
    thread.join(timeout=3)

    assert repeated.accepted is False
    assert repeated.code == "operation_in_progress"
    assert [call for call in client.calls if call[0] == "repair_network"] == []
    assert results[0].succeeded is True


def test_successful_mutation_refreshes_status_and_configuration():
    client = FakeControlClient()
    controller = _controller(client)

    outcome = controller.perform("start")

    assert outcome.state.status is TrayStatus.RUNNING
    assert outcome.state.status_label == "Running"
    assert client.calls == [
        ("start_hotspot",),
        ("status",),
        ("config",),
    ]


@pytest.mark.parametrize(
    ("error", "detail_code", "message"),
    (
        (
            AuthenticationError(401),
            "authentication_rejected",
            "Authentication was rejected.",
        ),
        (
            ConnectionFailure("secret transport detail"),
            "daemon_unavailable",
            "The local daemon is unavailable.",
        ),
    ),
)
def test_auth_rejected_and_daemon_unavailable_states_are_bounded(
    error,
    detail_code,
    message,
):
    secret = "must-not-escape-tray-errors"
    client = FakeControlClient(status_error=error)
    controller = _controller(client, token=secret)

    state = controller.refresh()
    exposed = repr(state) + state.message + state.detail_code

    assert state.status is TrayStatus.ERROR
    assert state.detail_code == detail_code
    assert state.message == message
    assert secret not in exposed
    assert "secret transport detail" not in exposed


def test_missing_token_disables_mutations_with_fixed_state():
    controller = _controller(FakeControlClient(), token=None)

    state = controller.refresh()
    outcome = controller.perform("start")

    assert state.detail_code == "token_missing"
    assert outcome.succeeded is False
    assert outcome.code == "token_missing"
    assert "Authentication" in outcome.message


def test_share_internet_toggle_uses_only_canonical_enable_internet_method():
    client = FakeControlClient(share_internet=True)
    controller = _controller(client)

    outcome = controller.perform("share_internet", enabled=False)

    assert outcome.succeeded is True
    assert client.calls == [
        ("set_share_internet", False),
        ("status",),
        ("config",),
    ]
    assert outcome.state.share_internet is False


def test_hotspot_autostart_toggle_uses_only_canonical_daemon_method():
    client = FakeControlClient(autostart=False)
    controller = _controller(client)

    outcome = controller.perform("hotspot_autostart", enabled=True)

    assert outcome.succeeded is True
    assert client.calls == [
        ("set_hotspot_autostart", True),
        ("status",),
        ("config",),
    ]
    assert outcome.state.hotspot_autostart is True


def test_privacy_mode_is_companion_local_and_never_mutates_daemon_config():
    client = FakeControlClient()
    controller = _controller(client)

    state = controller.set_privacy_mode(False)
    refreshed = controller.refresh()

    assert state.privacy_mode is False
    assert refreshed.privacy_mode is False
    assert client.status_log_requests == [True]
    assert client.calls == [("status",), ("config",)]


def test_privacy_mode_defaults_to_status_without_logs():
    client = FakeControlClient()
    controller = _controller(client)

    controller.refresh()

    assert client.status_log_requests == [False]


def test_pending_operation_immediately_disables_mutations_in_menu_model():
    controller = _controller(FakeControlClient())
    controller.mark_operation_pending("start")

    model = build_tray_menu_model(controller.state, window_visible=True)

    assert controller.state.status is TrayStatus.TRANSITIONING
    assert controller.state.busy_action == "start"
    assert all(
        not item.enabled
        for item in model.items
        if item.action in {"start", "stop", "restart", "repair"}
    )


def test_hotspot_autostart_and_desktop_login_autostart_are_not_conflated():
    model = build_tray_menu_model(TrayState(), window_visible=False)
    manifest = json.loads(
        Path(
            "packaging/flatpak/io.github.josethevrtech.VRhotspot.json"
        ).read_text(encoding="utf-8")
    )

    assert "Start Hotspot Automatically" in model.action_labels()
    assert "Launch VR Hotspot at login" not in model.action_labels()
    assert not any(
        argument.startswith("--filesystem=")
        for argument in manifest["finish-args"]
    )


class FakeBackend:
    def __init__(self):
        self.stop_calls = 0
        self.models = []

    def stop(self):
        self.stop_calls += 1

    def update(self, model):
        self.models.append(model)


class FakeControls:
    state = TrayState()

    def perform(self, *_args, **_kwargs):
        raise AssertionError("Quit must not perform a hotspot action")


def _menu_enabled(model, action):
    return next(item.enabled for item in model.items if item.action == action)


def test_close_to_tray_immediately_refreshes_exported_show_hide_state():
    application = FakeApplication()
    lifecycle = WindowLifecycleController(
        application=application,
        window=FakeWindow(),
        tray_active=True,
    )
    backend = FakeBackend()
    runtime = TrayRuntime(
        application=application,
        lifecycle=lifecycle,
        controls=FakeControls(),
        authentication=object(),
        backend=backend,
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=object(),
        open_diagnostics=lambda: None,
        open_web_portal=lambda: None,
    )

    runtime.show()
    assert _menu_enabled(backend.models[-1], "show") is False
    assert _menu_enabled(backend.models[-1], "hide") is True

    runtime.dispatch_action("hide")
    assert _menu_enabled(backend.models[-1], "show") is True
    assert _menu_enabled(backend.models[-1], "hide") is False

    runtime.dispatch_action("show")
    assert _menu_enabled(backend.models[-1], "show") is False
    assert _menu_enabled(backend.models[-1], "hide") is True

    assert runtime.close_request(lifecycle.window) is True
    assert lifecycle.visible is False
    assert _menu_enabled(backend.models[-1], "show") is True
    assert _menu_enabled(backend.models[-1], "hide") is False


def test_tray_window_close_event_uses_runtime_state_refresh_path():
    from flatpak_app import app

    source = Path(app.__file__).read_text(encoding="utf-8")

    assert 'window.connect("close-request", runtime.close_request)' in source
    assert 'window.connect("close-request", lifecycle.close_request)' not in source


def test_explicit_quit_exits_only_the_companion_without_hotspot_stop():
    application = FakeApplication()
    lifecycle = WindowLifecycleController(
        application=application,
        window=FakeWindow(),
        tray_active=True,
    )
    backend = FakeBackend()
    runtime = TrayRuntime(
        application=application,
        lifecycle=lifecycle,
        controls=FakeControls(),
        authentication=object(),
        backend=backend,
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=object(),
        open_diagnostics=lambda: None,
        open_web_portal=lambda: None,
    )

    runtime.dispatch_action("quit")

    assert application.quit_calls == 1
    assert backend.stop_calls == 1


@pytest.mark.parametrize(
    ("succeeded", "code", "expected_title", "expected_body"),
    (
        (True, "hotspot_started", "VR Hotspot", "Hotspot started."),
        (True, "hotspot_stopped", "VR Hotspot", "Hotspot stopped."),
        (
            False,
            "daemon_unavailable",
            "VR Hotspot unavailable",
            "The local daemon is unavailable.",
        ),
        (
            False,
            "operation_failed",
            "VR Hotspot operation failed",
            "The requested operation failed.",
        ),
    ),
)
def test_significant_notifications_are_fixed_bounded_and_secret_free(
    succeeded,
    code,
    expected_title,
    expected_body,
):
    secret = "notification-secret-must-not-escape"

    class Notification:
        def __init__(self, title):
            self.title = title
            self.body = ""
            self.icon = None

        @classmethod
        def new(cls, title):
            return cls(title)

        def set_body(self, body):
            self.body = body

        def set_icon(self, icon):
            self.icon = icon

    class ThemedIcon:
        @staticmethod
        def new(name):
            return name

    Gio = type(
        "Gio",
        (),
        {"Notification": Notification, "ThemedIcon": ThemedIcon},
    )

    class NotificationApplication(FakeApplication):
        def __init__(self):
            super().__init__()
            self.notifications = []

        def send_notification(self, notification_id, notification):
            self.notifications.append((notification_id, notification))

    application = NotificationApplication()
    lifecycle = WindowLifecycleController(
        application=application,
        window=FakeWindow(),
    )
    runtime = TrayRuntime(
        application=application,
        lifecycle=lifecycle,
        controls=FakeControls(),
        authentication=object(),
        backend=FakeBackend(),
        Gtk=object(),
        Gdk=object(),
        Gio=Gio,
        GLib=object(),
        open_diagnostics=lambda: None,
        open_web_portal=lambda: None,
    )
    outcome = ActionOutcome(
        accepted=True,
        succeeded=succeeded,
        code=code,
        message=secret,
        state=TrayState(),
    )

    runtime._notify(outcome)

    assert len(application.notifications) == 1
    notification_id, notification = application.notifications[0]
    exposed = "\n".join(
        (notification_id, notification.title, notification.body)
    )
    assert notification.title == expected_title
    assert notification.body == expected_body
    assert len(notification.body) < 100
    assert secret not in exposed


def test_status_notifier_backend_unavailable_returns_false_without_crash():
    class BrokenGio:
        class BusType:
            SESSION = 1

        @staticmethod
        def bus_get_sync(*_args):
            raise RuntimeError("watcher unavailable")

    backend = StatusNotifierBackend(
        Gio=BrokenGio,
        GLib=object(),
        model=build_tray_menu_model(TrayState(), window_visible=True),
        on_action=lambda _action: None,
        on_activate=lambda: None,
    )

    assert backend.start() is False


def test_dbus_menu_get_property_preserves_false_boolean_values():
    class Variant:
        def __init__(self, signature, value):
            self.signature = signature
            self.value = value

    GLib = type("GLib", (), {"Variant": Variant})

    class Parameters:
        @staticmethod
        def unpack():
            return 2, "enabled"

    class Invocation:
        returned = None

        def return_value(self, value):
            self.returned = value

    invocation = Invocation()
    backend = StatusNotifierBackend(
        Gio=object(),
        GLib=GLib,
        model=build_tray_menu_model(TrayState(), window_visible=False),
        on_action=lambda _action: None,
        on_activate=lambda: None,
    )

    backend._handle_menu_method(
        None,
        None,
        None,
        None,
        "GetProperty",
        Parameters(),
        invocation,
    )

    value = invocation.returned.value[0]
    assert value.signature == "b"
    assert value.value is False


def test_tray_mode_falls_back_to_normal_native_app_when_desktop_modules_missing(
    monkeypatch,
):
    from flatpak_app import app

    calls = []
    monkeypatch.setattr(
        app,
        "run_tray",
        lambda: (_ for _ in ()).throw(app.GuiUnavailableError("missing")),
    )
    monkeypatch.setattr(app, "run_gui", lambda: calls.append("native") or 0)

    assert app.main(["--tray"]) == 0
    assert calls == ["native"]


def test_flatpak_tray_sources_launch_no_host_service_or_network_commands():
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "flatpak_app/app.py",
            "flatpak_app/tray.py",
            "flatpak_client/client.py",
            "flatpak_client/control.py",
            "flatpak_client/wallet.py",
        )
    )

    for forbidden in (
        "subprocess.run",
        "systemctl",
        "nmcli",
        "NetworkManager",
        "iwd",
        "hostapd",
        "dnsmasq",
        "firewall-cmd",
        "iptables",
        "nft ",
        "ip route",
    ):
        assert forbidden not in sources


def test_cyan_black_icon_variants_are_valid_scalable_and_packaged():
    icon_dir = Path("packaging/flatpak")
    names = (
        "io.github.josethevrtech.VRhotspot.svg",
        "io.github.josethevrtech.VRhotspot-running.svg",
        "io.github.josethevrtech.VRhotspot-working.svg",
        "io.github.josethevrtech.VRhotspot-error.svg",
    )
    manifest_text = (
        icon_dir / "io.github.josethevrtech.VRhotspot.json"
    ).read_text(encoding="utf-8")

    for name in names:
        path = icon_dir / name
        root = ET.parse(path).getroot()
        source = path.read_text(encoding="utf-8").casefold()
        assert root.tag.endswith("svg")
        assert root.attrib["viewBox"] == "0 0 128 128"
        assert "#00eaff" in source
        assert "#05070a" in source
        assert "#6d4aff" not in source
        assert "#1466cc" not in source
        assert name in manifest_text
