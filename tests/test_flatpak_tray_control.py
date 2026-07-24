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
    DaemonTokenMissingError,
    FirstRunResult,
    FirstRunState,
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


def _menu_item(model, action):
    return next(item for item in model.all_items() if item.action == action)


def _submenu(model, label):
    return next(item for item in model.items if item.label == label)


def test_tray_menu_contains_every_required_action_in_grouped_submenus():
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

    assert tuple(item.label for item in model.items) == (
        "Current status: Running",
        "Hotspot Commands",
        "Network",
        "Advanced",
        "Quit VR Hotspot",
    )
    assert model.action_labels() == (
        "Current status: Running",
        "Hotspot Commands",
        "Start Hotspot",
        "Stop Hotspot",
        "Restart Service",
        "Repair Network",
        "Network",
        "Share Internet Connection",
        "Advanced",
        "Authentication…",
        "Refresh Status",
        "Open Diagnostics",
        "Privacy Mode",
        "Start Hotspot Automatically",
        "Quit VR Hotspot",
    )
    hotspot_commands = _submenu(model, "Hotspot Commands")
    network = _submenu(model, "Network")
    advanced = _submenu(model, "Advanced")
    assert tuple(item.action for item in hotspot_commands.children) == (
        "start",
        "stop",
        "restart",
        "repair",
    )
    assert tuple(item.action for item in network.children) == (
        "share_internet",
    )
    assert tuple(item.action for item in advanced.children) == (
        "authentication",
        "refresh",
        "diagnostics",
        "privacy",
        "hotspot_autostart",
    )
    nested_actions = {
        "start",
        "stop",
        "restart",
        "repair",
        "share_internet",
        "refresh",
        "authentication",
        "diagnostics",
        "privacy",
        "hotspot_autostart",
    }
    assert not nested_actions.intersection(
        item.action for item in model.items
    )
    assert all(
        item.action not in {"show", "hide"}
        for item in model.all_items()
    )
    assert all(
        item.label not in {"Show VR Hotspot", "Hide VR Hotspot"}
        for item in model.all_items()
    )
    assert _menu_item(model, "refresh") in advanced.children
    assert all(item.action != "refresh" for item in model.items)
    assert model.items[0].action == "status"
    assert all(not item.separator for item in model.all_items())
    assert all(item.action != "web_portal" for item in model.all_items())
    assert not any(
        item.label.casefold().startswith("launch vr hotspot at log")
        for item in model.all_items()
    )


def test_dbus_menu_layout_exports_nested_submenus_without_kde():
    class Variant:
        def __init__(self, signature, value):
            self.signature = signature
            self.value = value

    backend = StatusNotifierBackend(
        Gio=object(),
        GLib=type("GLib", (), {"Variant": Variant}),
        model=build_tray_menu_model(
            TrayState(
                status=TrayStatus.RUNNING,
                status_label="Running",
                daemon_available=True,
                authenticated=True,
                share_internet=True,
                hotspot_autostart=False,
            ),
            window_visible=True,
        ),
        on_action=lambda _action: None,
        on_activate=lambda: None,
    )

    root_id, root_properties, root_children = backend._layout()
    exported = {
        child.value[1]["label"].value: child.value
        for child in root_children
    }

    assert root_id == 0
    assert root_properties["children-display"].value == "submenu"
    assert tuple(exported) == (
        "Current status: Running",
        "Hotspot Commands",
        "Network",
        "Advanced",
        "Quit VR Hotspot",
    )
    assert tuple(
        child.value[1]["label"].value
        for child in exported["Hotspot Commands"][2]
    ) == (
        "Start Hotspot",
        "Stop Hotspot",
        "Restart Service",
        "Repair Network",
    )
    assert tuple(
        child.value[1]["label"].value
        for child in exported["Network"][2]
    ) == ("Share Internet Connection",)
    assert tuple(
        child.value[1]["label"].value
        for child in exported["Advanced"][2]
    ) == (
        "Authentication…",
        "Refresh Status",
        "Open Diagnostics",
        "Privacy Mode",
        "Start Hotspot Automatically",
    )
    for label in ("Hotspot Commands", "Network", "Advanced"):
        assert exported[label][1]["children-display"].value == "submenu"

    parent_id, _properties, children = backend._layout(23, 1, ("label",))
    assert parent_id == 23
    assert tuple(child.value[1]["label"].value for child in children) == (
        "Authentication…",
        "Refresh Status",
        "Open Diagnostics",
        "Privacy Mode",
        "Start Hotspot Automatically",
    )


def test_dbus_update_exports_refreshed_command_sensitivity_to_kde():
    class Variant:
        def __init__(self, signature, value):
            self.signature = signature
            self.value = value

    class Connection:
        def __init__(self):
            self.signals = []

        def emit_signal(self, *_args):
            self.signals.append(_args)

    stopped = build_tray_menu_model(
        TrayState(
            status=TrayStatus.STOPPED,
            status_label="Stopped",
            daemon_available=True,
            authenticated=True,
        ),
        window_visible=True,
    )
    running = build_tray_menu_model(
        TrayState(
            status=TrayStatus.RUNNING,
            status_label="Running",
            running=True,
            daemon_available=True,
            authenticated=True,
        ),
        window_visible=True,
    )
    backend = StatusNotifierBackend(
        Gio=object(),
        GLib=type("GLib", (), {"Variant": Variant}),
        model=stopped,
        on_action=lambda _action: None,
        on_activate=lambda: None,
    )
    connection = Connection()
    backend._connection = connection

    backend.update(running)

    update_signal = next(
        signal
        for signal in connection.signals
        if signal[3] == "ItemsPropertiesUpdated"
    )
    changes, removed = update_signal[4].value
    changes_by_id = {item_id: values for item_id, values in changes}
    assert changes_by_id[7]["enabled"].value is False
    assert changes_by_id[8]["enabled"].value is True
    assert removed == []
    assert _menu_enabled(backend._model, "start") is False
    assert _menu_enabled(backend._model, "stop") is True
    assert _menu_enabled(backend._model, "restart") is True


@pytest.mark.parametrize(
    ("status", "label", "notifier_status"),
    (
        (TrayStatus.RUNNING, "Running", "Active"),
        (TrayStatus.STOPPED, "Stopped", "Active"),
        (
            TrayStatus.TRANSITIONING,
            "Transitioning",
            "NeedsAttention",
        ),
        (
            TrayStatus.NEEDS_AUTHENTICATION,
            "Needs Authentication",
            "Active",
        ),
        (
            TrayStatus.DAEMON_UNAVAILABLE,
            "Daemon Unavailable",
            "Active",
        ),
        (TrayStatus.ERROR, "Error", "Active"),
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
    assert _menu_enabled(model, "authentication") is True


def test_needs_authentication_is_static_and_only_transitioning_requests_attention():
    needs_authentication = build_tray_menu_model(
        TrayState(
            status=TrayStatus.NEEDS_AUTHENTICATION,
            status_label="Needs Authentication",
        ),
        window_visible=True,
    )
    transitioning = build_tray_menu_model(
        TrayState(
            status=TrayStatus.TRANSITIONING,
            status_label="Transitioning",
            busy_action="start",
        ),
        window_visible=True,
    )

    assert needs_authentication.icon_name == ICON_NAMES[
        TrayStatus.NEEDS_AUTHENTICATION
    ]
    assert needs_authentication.notifier_status == "Active"
    assert transitioning.icon_name == ICON_NAMES[TrayStatus.TRANSITIONING]
    assert transitioning.notifier_status == "NeedsAttention"


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
    ("error", "detail_code", "message", "expected_status"),
    (
        (
            AuthenticationError(401),
            "authentication_rejected",
            "Authentication was rejected.",
            TrayStatus.NEEDS_AUTHENTICATION,
        ),
        (
            DaemonTokenMissingError(),
            "daemon_token_missing",
            "The daemon has no configured API token.",
            TrayStatus.NEEDS_AUTHENTICATION,
        ),
        (
            ConnectionFailure("secret transport detail"),
            "daemon_unavailable",
            "The local daemon is unavailable.",
            TrayStatus.DAEMON_UNAVAILABLE,
        ),
        (
            RuntimeError("unexpected detail"),
            "operation_failed",
            "The tray operation failed safely.",
            TrayStatus.ERROR,
        ),
    ),
)
def test_auth_rejected_and_daemon_unavailable_states_are_bounded(
    error,
    detail_code,
    message,
    expected_status,
):
    secret = "must-not-escape-tray-errors"
    client = FakeControlClient(status_error=error)
    controller = _controller(client, token=secret)

    state = controller.refresh()
    exposed = repr(state) + state.message + state.detail_code

    assert state.status is expected_status
    assert state.detail_code == detail_code
    assert state.message == message
    assert secret not in exposed
    assert "secret transport detail" not in exposed


def test_missing_token_disables_mutations_with_fixed_state():
    controller = _controller(FakeControlClient(), token=None)

    state = controller.refresh()
    outcome = controller.perform("start")

    assert state.status is TrayStatus.NEEDS_AUTHENTICATION
    assert state.detail_code == "token_missing"
    assert outcome.succeeded is False
    assert outcome.code == "token_missing"
    assert "Authentication" in outcome.message

    menu = build_tray_menu_model(state, window_visible=True)
    assert _menu_enabled(menu, "authentication") is True
    assert all(
        not _menu_enabled(menu, action)
        for action in ("start", "stop", "restart", "repair")
    )
    assert _menu_enabled(menu, "share_internet") is False
    assert _menu_enabled(menu, "hotspot_autostart") is False


def test_explicit_token_never_appears_in_menu_labels():
    secret = "menu-label-secret-must-not-escape"
    controller = _controller(FakeControlClient(), token=secret)

    state = controller.refresh()
    menu = build_tray_menu_model(state, window_visible=True)

    assert secret not in "\n".join(menu.action_labels())
    assert secret not in repr(menu)


def test_authenticated_running_refresh_maps_to_running():
    controller = _controller(
        FakeControlClient(phase="running", running=True)
    )

    state = controller.refresh()

    assert state.status is TrayStatus.RUNNING
    assert state.status_label == "Running"
    assert state.authenticated is True
    assert state.daemon_available is True
    menu = build_tray_menu_model(state, window_visible=True)
    assert _menu_enabled(menu, "start") is False
    assert _menu_enabled(menu, "stop") is True
    assert _menu_enabled(menu, "restart") is True
    assert _menu_enabled(menu, "repair") is True
    assert _menu_enabled(menu, "share_internet") is True
    assert _menu_item(menu, "share_internet").checked is True
    assert _menu_enabled(menu, "hotspot_autostart") is True
    assert _menu_item(menu, "hotspot_autostart").checked is False


def test_authenticated_stopped_state_enables_only_valid_hotspot_commands():
    controller = _controller(
        FakeControlClient(phase="stopped", running=False)
    )

    state = controller.refresh()
    menu = build_tray_menu_model(state, window_visible=True)

    assert state.status is TrayStatus.STOPPED
    assert _menu_enabled(menu, "start") is True
    assert _menu_enabled(menu, "stop") is False
    assert _menu_enabled(menu, "restart") is True
    assert _menu_enabled(menu, "repair") is True


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        (
            TrayState(),
            (False, False, False, False, True),
        ),
        (
            TrayState(
                status=TrayStatus.DAEMON_UNAVAILABLE,
                status_label="Daemon Unavailable",
            ),
            (False, False, False, False, True),
        ),
        (
            TrayState(
                status=TrayStatus.STOPPED,
                status_label="Stopped",
                daemon_available=True,
                authenticated=True,
            ),
            (True, False, True, True, True),
        ),
        (
            TrayState(
                status=TrayStatus.RUNNING,
                status_label="Running",
                running=True,
                daemon_available=True,
                authenticated=True,
            ),
            (False, True, True, True, True),
        ),
        (
            TrayState(
                status=TrayStatus.TRANSITIONING,
                status_label="Transitioning",
                daemon_available=True,
                authenticated=True,
                busy_action="start",
            ),
            (False, False, False, False, True),
        ),
    ),
)
def test_tray_command_sensitivity_matrix_is_deterministic(state, expected):
    model = build_tray_menu_model(state, window_visible=True)

    assert tuple(
        _menu_enabled(model, action)
        for action in (
            "start",
            "stop",
            "restart",
            "repair",
            "authentication",
        )
    ) == expected


def test_unexpected_client_construction_failure_maps_to_error():
    controller = TrayControlController(
        TokenProvider(),
        client_factory=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret construction detail")
        ),
    )

    state = controller.refresh()

    assert state.status is TrayStatus.ERROR
    assert state.status_label == "Error"
    assert state.detail_code == "operation_failed"
    assert "secret construction detail" not in repr(state)


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
        for item in model.all_items()
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
    assert not any(
        label.casefold().startswith("launch vr hotspot at log")
        for label in model.action_labels()
    )
    assert not any(
        argument.startswith("--filesystem=")
        for argument in manifest["finish-args"]
    )


def test_tray_sources_do_not_restore_retired_graphical_symbols():
    names = (
        "run_" + "gui",
        "_populate_" + "native_" + "dashboard_window",
        "Native" + "DashboardModel",
        "Dashboard" + "SectionLabels",
        "build_" + "dashboard_model",
    )
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("flatpak_app/app.py", "flatpak_app/tray.py")
    )

    for name in names:
        assert name not in source


class FakeBackend:
    def __init__(self):
        self.stop_calls = 0
        self.models = []
        self.start_calls = 0
        self.model_at_start = None

    def start(self):
        self.start_calls += 1
        self.model_at_start = self.models[-1]
        return True

    def stop(self):
        self.stop_calls += 1

    def update(self, model):
        self.models.append(model)


class FakeControls:
    state = TrayState()

    def refresh(self):
        return self.state

    def perform(self, *_args, **_kwargs):
        raise AssertionError("Quit must not perform a hotspot action")


@pytest.mark.parametrize(
    ("status", "label", "start_enabled", "stop_enabled"),
    (
        (TrayStatus.RUNNING, "Running", False, True),
        (TrayStatus.STOPPED, "Stopped", True, False),
    ),
)
def test_startup_refresh_registers_authenticated_daemon_state_as_initial_model(
    status,
    label,
    start_enabled,
    stop_enabled,
):
    class Application(FakeApplication):
        def __init__(self):
            super().__init__()
            self.hold_calls = 0

        def hold(self):
            self.hold_calls += 1

    class Controls:
        def __init__(self):
            self.refresh_calls = 0
            self.state = TrayState()

        def refresh(self):
            self.refresh_calls += 1
            self.state = TrayState(
                status=status,
                status_label=label,
                phase=status.value,
                running=status is TrayStatus.RUNNING,
                daemon_available=True,
                authenticated=True,
            )
            return self.state

    class GLib:
        timers = []

        @classmethod
        def timeout_add_seconds(cls, seconds, callback):
            cls.timers.append((seconds, callback))
            return 1

    application = Application()
    controls = Controls()
    backend = FakeBackend()
    runtime = TrayRuntime(
        application=application,
        lifecycle=WindowLifecycleController(
            application=application,
            window=FakeWindow(),
        ),
        controls=controls,
        authentication=object(),
        backend=backend,
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=GLib,
        open_diagnostics=lambda: None,
    )

    assert runtime.start() is True

    initial = backend.model_at_start
    assert controls.refresh_calls == 1
    assert backend.start_calls == 1
    assert initial.icon_name == ICON_NAMES[status]
    assert initial.tooltip == f"VR Hotspot — {label}"
    assert _menu_enabled(initial, "start") is start_enabled
    assert _menu_enabled(initial, "stop") is stop_enabled
    assert _menu_enabled(initial, "restart") is True
    assert application.hold_calls == 1
    assert len(GLib.timers) == 1
    assert GLib.timers[0][0] == 5


def test_auth_change_refresh_is_deferred_until_active_worker_finishes():
    application = FakeApplication()
    runtime = TrayRuntime(
        application=application,
        lifecycle=WindowLifecycleController(
            application=application,
            window=FakeWindow(),
        ),
        controls=FakeControls(),
        authentication=object(),
        backend=FakeBackend(),
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=object(),
        open_diagnostics=lambda: None,
    )
    attempts = []

    def fake_run_worker(call, **_kwargs):
        attempts.append(call)
        return len(attempts) > 1

    runtime._run_worker = fake_run_worker

    runtime.refresh_after_auth_change()

    assert runtime._auth_refresh_pending is True
    assert len(attempts) == 1

    runtime._worker_lock.acquire()
    runtime._finish_worker()

    assert runtime._auth_refresh_pending is False
    assert len(attempts) == 2


def _menu_enabled(model, action):
    return _menu_item(model, action).enabled


def test_saving_and_testing_authentication_refreshes_status_without_leakage():
    secret = "authentication-refresh-secret"

    class Entry:
        def __init__(self):
            self.text = secret

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    class SaveSecurely:
        def __init__(self):
            self.active = True

        def get_active(self):
            return self.active

        def set_active(self, active):
            self.active = active

    class Status:
        def __init__(self):
            self.text = ""

        def set_text(self, text):
            self.text = text

    class Authentication:
        def __init__(self):
            self.supplied = []

        def save_or_replace(self, token, *, save_securely):
            self.supplied.append(("save", bool(token), save_securely))
            return type(
                "Result",
                (),
                {
                    "message": "API token saved in memory.",
                    "securely_saved": False,
                },
            )()

        def test_authentication(self, *, explicit_token):
            self.supplied.append(("test", bool(explicit_token)))
            return FirstRunResult(FirstRunState.TOKEN_ACCEPTED)

    authentication = Authentication()
    runtime = TrayRuntime(
        application=FakeApplication(),
        lifecycle=WindowLifecycleController(
            application=FakeApplication(),
            window=FakeWindow(),
        ),
        controls=FakeControls(),
        authentication=authentication,
        backend=FakeBackend(),
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=object(),
        open_diagnostics=lambda: None,
    )
    refreshes = []
    runtime.refresh_after_auth_change = lambda: refreshes.append("refresh")
    runtime.refresh_async = lambda: refreshes.append("refresh")
    entry = Entry()
    save_securely = SaveSecurely()
    status = Status()

    runtime._auth_save(entry, save_securely, status)
    assert entry.text == ""
    assert refreshes == ["refresh"]
    assert secret not in status.text

    entry.text = secret
    runtime._auth_test(entry, status)
    assert entry.text == ""
    assert refreshes == ["refresh", "refresh"]
    assert status.text == "Authentication succeeded."
    assert authentication.supplied == [
        ("save", True, True),
        ("test", True),
    ]
    assert secret not in repr(authentication.supplied)


def test_clearing_authentication_refreshes_needs_authentication_menu():
    noncredential = "clear-refresh-test-placeholder"
    portal_clears = []

    class Entry:
        def __init__(self):
            self.text = noncredential

        def set_text(self, text):
            self.text = text

    class Status:
        def __init__(self):
            self.text = ""

        def set_text(self, text):
            self.text = text

    class Authentication:
        def __init__(self):
            self.clear_calls = 0

        def clear(self):
            self.clear_calls += 1
            return type(
                "Result",
                (),
                {"message": "VR Hotspot test credential was cleared."},
            )()

    class RefreshingControls:
        def __init__(self):
            self.refresh_calls = 0
            self.state = TrayState(
                status=TrayStatus.RUNNING,
                status_label="Running",
                phase="running",
                running=True,
                daemon_available=True,
                authenticated=True,
            )

        def refresh(self):
            self.refresh_calls += 1
            self.state = TrayState(
                status=TrayStatus.NEEDS_AUTHENTICATION,
                status_label="Needs Authentication",
                detail_code="token_missing",
                message="Authentication is required.",
                daemon_available=True,
                authenticated=False,
            )

    application = FakeApplication()
    authentication = Authentication()
    controls = RefreshingControls()
    backend = FakeBackend()
    runtime = TrayRuntime(
        application=application,
        lifecycle=WindowLifecycleController(
            application=application,
            window=FakeWindow(),
        ),
        controls=controls,
        authentication=authentication,
        backend=backend,
        Gtk=object(),
        Gdk=object(),
        Gio=object(),
        GLib=object(),
        open_diagnostics=lambda: None,
        on_auth_cleared=lambda: portal_clears.append("clear"),
    )

    def refresh_now():
        controls.refresh()
        runtime._update_menu()

    runtime.refresh_after_auth_change = refresh_now
    runtime._update_menu()
    assert _menu_enabled(backend.models[-1], "stop") is True

    entry = Entry()
    status = Status()
    runtime._auth_clear(entry, status)

    refreshed_menu = backend.models[-1]
    assert authentication.clear_calls == 1
    assert controls.refresh_calls == 1
    assert portal_clears == ["clear"]
    assert entry.text == ""
    assert "Current status: Needs Authentication" in refreshed_menu.action_labels()
    assert _menu_enabled(refreshed_menu, "authentication") is True
    assert all(
        not _menu_enabled(refreshed_menu, action)
        for action in ("start", "stop", "restart", "repair")
    )
    assert noncredential not in status.text
    assert noncredential not in repr(refreshed_menu)


def test_close_to_tray_keeps_redundant_show_hide_commands_absent():
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
    )

    runtime.show()
    assert lifecycle.visible is True
    assert all(
        item.action not in {"show", "hide"}
        for item in backend.models[-1].all_items()
    )

    assert runtime.close_request(lifecycle.window) is True
    assert lifecycle.visible is False
    assert all(
        item.label not in {"Show VR Hotspot", "Hide VR Hotspot"}
        for item in backend.models[-1].all_items()
    )


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
            return 7, "enabled"

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


def test_tray_mode_uses_web_portal_shell_when_desktop_modules_are_missing(
    monkeypatch,
):
    from flatpak_app import app

    calls = []
    monkeypatch.setattr(
        app,
        "run_tray",
        lambda: (_ for _ in ()).throw(app.GuiUnavailableError("missing")),
    )
    monkeypatch.setattr(
        app,
        "run_web_portal_shell",
        lambda: calls.append("web-portal") or 0,
    )

    assert app.main(["--tray"]) == 0
    assert calls == ["web-portal"]


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
