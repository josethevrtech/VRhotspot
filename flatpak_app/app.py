"""Launchable, read-only Flatpak native dashboard foundation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import getpass
import json
import sys
from typing import Any, Sequence
import warnings

from flatpak_client import (
    AdapterReadinessModel,
    DaemonStatusModel,
    DiagnosticsControlUiController,
    DiagnosticsControlUiModel,
    FirstRunResult,
    FirstRunState,
    LocalApiClient,
    PairingStatusModel,
    PreflightSummaryModel,
    PresentationMode,
    StatusSeverity,
    SupportBundleAffordance,
    TokenPairingController,
)


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
MAX_SMOKE_JSON_BYTES = 8_192
MAX_LIVE_SMOKE_JSON_BYTES = 65_536

_LIVE_SMOKE_SUCCESS = "success"
_LIVE_SMOKE_INVALID_RESPONSE = "invalid_response"
_LIVE_SMOKE_INTERACTIVE_INPUT_REQUIRED = "interactive_input_required"
_LIVE_SMOKE_TOKEN_INPUT_EMPTY = "token_input_empty"
_LIVE_SMOKE_TOKEN_INPUT_CANCELLED = "token_input_cancelled"
_LIVE_SMOKE_FAILURE_EXIT = 1
_LIVE_SMOKE_INPUT_EXIT = 2


class GuiUnavailableError(RuntimeError):
    """GTK 4 or PyGObject is unavailable for the graphical shell."""


@dataclass(frozen=True)
class DashboardControlsBoundary:
    """Visible proof that this dashboard has no mutation surface."""

    visible: bool
    severity: StatusSeverity
    title: str
    readiness_label: str
    summary: str
    mutation_actions: tuple[str, ...] = ()
    action_enabled: bool = False


@dataclass(frozen=True)
class NativeDashboardModel:
    """Safe sections consumed by the native GTK dashboard."""

    daemon: DaemonStatusModel
    pairing: PairingStatusModel
    adapter_readiness: AdapterReadinessModel
    preflight: PreflightSummaryModel
    support_bundle: SupportBundleAffordance
    controls: DashboardControlsBoundary


class FirstRunTokenEntryController:
    """Build one token-free display model from an explicitly supplied token."""

    def __init__(self, *, client_factory=LocalApiClient, pairing_controller=None):
        self._client_factory = client_factory
        self._pairing_controller = (
            pairing_controller
            if pairing_controller is not None
            else TokenPairingController(client_factory)
        )

    def __repr__(self) -> str:
        return (
            "FirstRunTokenEntryController("
            "client_factory_configured=True, pairing_controller_configured=True)"
        )

    def connect(self, *, token: str) -> DiagnosticsControlUiModel:
        """Validate caller-provided text and build read-only UI state in memory."""

        try:
            pairing_result = self._pairing_controller.evaluate(token=token)
        except Exception:
            pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)
        if not isinstance(pairing_result, FirstRunResult):
            pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)

        client = None
        if pairing_result.state is FirstRunState.TOKEN_ACCEPTED:
            try:
                client = self._client_factory(token=token)
            except Exception:
                pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)

        try:
            return DiagnosticsControlUiController(client).build(
                pairing_result=pairing_result,
                mode=PresentationMode.BASIC,
            )
        finally:
            client = None


def build_initial_model() -> DiagnosticsControlUiModel:
    """Build a deterministic offline/unpaired model without daemon access."""

    return DiagnosticsControlUiController().build(
        pairing_result=FirstRunResult(FirstRunState.INVALID_RESPONSE),
        mode=PresentationMode.BASIC,
    )


def build_dashboard_model(
    model: DiagnosticsControlUiModel,
) -> NativeDashboardModel:
    """Project the existing safe UI model into the native dashboard sections."""

    if not isinstance(model, DiagnosticsControlUiModel):
        model = build_initial_model()
    return NativeDashboardModel(
        daemon=model.daemon,
        pairing=model.pairing,
        adapter_readiness=model.adapters,
        preflight=model.preflight,
        support_bundle=model.support_bundle,
        controls=DashboardControlsBoundary(
            visible=True,
            severity=StatusSeverity.UNKNOWN,
            title="Controls boundary",
            readiness_label="Unavailable",
            summary=(
                "This foundation is read-only. Lifecycle and configuration "
                "actions are not available."
            ),
        ),
    )


def build_smoke_payload() -> dict[str, Any]:
    """Return a bounded, non-secret description of the initial shell."""

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
            "graphical_shell": "gtk4_placeholder",
            "state": "offline_unpaired",
        },
        "ui": asdict(model),
    }


def render_smoke_json() -> str:
    """Serialize the deterministic smoke payload and enforce its size bound."""

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
    return statuses.get(
        model.pairing.detail_code,
        _LIVE_SMOKE_INVALID_RESPONSE,
    )


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
            model = FirstRunTokenEntryController(client_factory=factory).connect(
                token=token
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
        exit_code=(
            0 if status == _LIVE_SMOKE_SUCCESS else _LIVE_SMOKE_FAILURE_EXIT
        ),
    )


def _load_gtk():
    """Import GTK only when the graphical entry point is launched."""

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
    except (ImportError, ValueError):
        raise GuiUnavailableError(
            "GTK 4 and PyGObject are required for the graphical shell."
        ) from None
    return Gtk


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


def _severity_text(severity: StatusSeverity) -> str:
    if not isinstance(severity, StatusSeverity):
        severity = StatusSeverity.UNKNOWN
    return severity.value.replace("_", " ").upper()


def _severity_css_class(severity: StatusSeverity) -> str:
    return {
        StatusSeverity.OK: "success",
        StatusSeverity.WARNING: "warning",
        StatusSeverity.BLOCKED: "error",
        StatusSeverity.ERROR: "error",
        StatusSeverity.UNKNOWN: "dim-label",
    }.get(severity, "dim-label")


def _new_card(Gtk, *, title: str, severity: StatusSeverity):
    frame = Gtk.Frame()
    frame.add_css_class("card")
    frame.set_hexpand(True)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.set_margin_top(16)
    body.set_margin_bottom(16)
    body.set_margin_start(16)
    body.set_margin_end(16)

    heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title_label = _add_text_label(
        Gtk,
        heading,
        title,
        css_class="title-3",
    )
    title_label.set_hexpand(True)
    status_label = _add_text_label(
        Gtk,
        heading,
        _severity_text(severity),
        css_class=_severity_css_class(severity),
    )
    status_label.set_xalign(1.0)
    body.append(heading)
    frame.set_child(body)
    return frame, body


def _add_adapter_card(Gtk, container, card) -> None:
    frame = Gtk.Frame()
    frame.add_css_class("card")
    frame.set_hexpand(True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    body.set_margin_top(12)
    body.set_margin_bottom(12)
    body.set_margin_start(12)
    body.set_margin_end(12)

    recommendation = " · Recommended" if card.recommended else ""
    _add_text_label(
        Gtk,
        body,
        f"{card.interface}{recommendation}",
        css_class="title-4",
    )
    _add_text_label(
        Gtk,
        body,
        f"Readiness: {card.readiness_label}",
        css_class=_severity_css_class(card.severity),
    )
    _add_text_label(Gtk, body, f"Severity: {_severity_text(card.severity)}")
    _add_text_label(Gtk, body, card.summary)
    bands = ", ".join(card.supported_bands) or "Not reported"
    _add_text_label(Gtk, body, f"Supported bands: {bands}")
    _add_text_label(Gtk, body, f"Driver: {card.driver} · Bus: {card.bus_type}")
    if card.reasons:
        _add_text_label(Gtk, body, "Reasons", css_class="heading")
        for reason in card.reasons:
            _add_text_label(Gtk, body, f"• {reason}")
    else:
        _add_text_label(
            Gtk,
            body,
            "No readiness reasons were reported.",
            css_class="dim-label",
        )

    frame.set_child(body)
    container.append(frame)


def _populate_daemon_card(Gtk, container, dashboard: NativeDashboardModel) -> None:
    _add_text_label(Gtk, container, dashboard.daemon.title, css_class="title-4")
    _add_text_label(Gtk, container, dashboard.daemon.message)


def _populate_pairing_card(Gtk, container, dashboard: NativeDashboardModel) -> None:
    _add_text_label(Gtk, container, dashboard.pairing.title, css_class="title-4")
    _add_text_label(Gtk, container, dashboard.pairing.message)


def _populate_adapter_card(Gtk, container, dashboard: NativeDashboardModel) -> None:
    adapters = dashboard.adapter_readiness
    _add_text_label(Gtk, container, adapters.title, css_class="title-4")
    _add_text_label(Gtk, container, adapters.summary)
    _add_text_label(
        Gtk,
        container,
        f"Recommended interface: {adapters.recommended_interface}",
        css_class="heading",
    )
    if adapters.cards:
        for card in adapters.cards:
            _add_adapter_card(Gtk, container, card)
    else:
        _add_text_label(
            Gtk,
            container,
            "No adapter cards are available.",
            css_class="dim-label",
        )


def _populate_preflight_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    preflight = dashboard.preflight
    _add_text_label(
        Gtk,
        container,
        f"Readiness: {preflight.readiness_label}",
        css_class="title-4",
    )
    _add_text_label(Gtk, container, f"Severity: {_severity_text(preflight.severity)}")
    _add_text_label(Gtk, container, preflight.summary)

    _add_text_label(Gtk, container, "Facts", css_class="heading")
    if preflight.facts:
        for fact in preflight.facts:
            _add_text_label(Gtk, container, f"{fact.label}: {fact.value}")
    else:
        _add_text_label(
            Gtk,
            container,
            "No preflight facts are available.",
            css_class="dim-label",
        )

    _add_text_label(Gtk, container, "Issues", css_class="heading")
    if preflight.issues:
        for issue in preflight.issues:
            _add_text_label(
                Gtk,
                container,
                f"{_severity_text(issue.severity)} · {issue.message}",
            )
    else:
        _add_text_label(
            Gtk,
            container,
            "No preflight issues were reported.",
            css_class="dim-label",
        )

    _add_text_label(
        Gtk,
        container,
        "Noninteractive actions",
        css_class="heading",
    )
    if preflight.actions:
        for action in preflight.actions:
            _add_text_label(
                Gtk,
                container,
                f"Display-only guidance · {action.message}",
            )
    else:
        _add_text_label(
            Gtk,
            container,
            "No noninteractive actions were reported.",
            css_class="dim-label",
        )



def _populate_support_bundle_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    support_bundle = dashboard.support_bundle
    _add_text_label(Gtk, container, support_bundle.summary)
    export_button = Gtk.Button(label=support_bundle.action_label)
    export_button.set_sensitive(False)
    container.append(export_button)
    _add_text_label(
        Gtk,
        container,
        "Export remains disabled in this foundation.",
        css_class="dim-label",
    )


def _populate_controls_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    controls = dashboard.controls
    _add_text_label(
        Gtk,
        container,
        controls.readiness_label,
        css_class="title-4",
    )
    _add_text_label(Gtk, container, controls.summary)
    _add_text_label(
        Gtk,
        container,
        "Mutation actions: none",
        css_class="dim-label",
    )


def _render_dashboard_model(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    """Render the bounded native dashboard with no interactive host actions."""

    _clear_box(container)
    _add_text_label(Gtk, container, "Read-only dashboard", css_class="title-2")
    _add_text_label(
        Gtk,
        container,
        "Daemon-owned readiness and diagnostics, presented without host mutation.",
        css_class="dim-label",
    )

    grid = Gtk.Grid(column_spacing=16, row_spacing=16)
    grid.set_column_homogeneous(True)

    daemon_frame, daemon_body = _new_card(
        Gtk,
        title="Daemon status",
        severity=dashboard.daemon.severity,
    )
    _populate_daemon_card(Gtk, daemon_body, dashboard)
    grid.attach(daemon_frame, 0, 0, 1, 1)

    pairing_frame, pairing_body = _new_card(
        Gtk,
        title="Pairing status",
        severity=dashboard.pairing.severity,
    )
    _populate_pairing_card(Gtk, pairing_body, dashboard)
    grid.attach(pairing_frame, 1, 0, 1, 1)

    adapters_frame, adapters_body = _new_card(
        Gtk,
        title="Adapter readiness",
        severity=dashboard.adapter_readiness.severity,
    )
    _populate_adapter_card(Gtk, adapters_body, dashboard)
    grid.attach(adapters_frame, 0, 1, 1, 1)

    preflight_frame, preflight_body = _new_card(
        Gtk,
        title="Preflight diagnostics",
        severity=dashboard.preflight.severity,
    )
    _populate_preflight_card(Gtk, preflight_body, dashboard)
    grid.attach(preflight_frame, 1, 1, 1, 1)

    support_frame, support_body = _new_card(
        Gtk,
        title=dashboard.support_bundle.title,
        severity=dashboard.support_bundle.severity,
    )
    _populate_support_bundle_card(Gtk, support_body, dashboard)
    grid.attach(support_frame, 0, 2, 1, 1)

    controls_frame, controls_body = _new_card(
        Gtk,
        title=dashboard.controls.title,
        severity=dashboard.controls.severity,
    )
    _populate_controls_card(Gtk, controls_body, dashboard)
    grid.attach(controls_frame, 1, 2, 1, 1)

    container.append(grid)


def _render_display_model(Gtk, container, model: DiagnosticsControlUiModel) -> None:
    """Render only bounded, token-free fields from the existing UI model."""

    _render_dashboard_model(Gtk, container, build_dashboard_model(model))


def _connect_from_token_entry(
    *,
    token_entry,
    connect_button,
    controller: FirstRunTokenEntryController,
    render_model,
) -> None:
    """Clear caller input before validating and render only the safe result."""

    token = token_entry.get_text()
    token_entry.set_text("")
    connect_button.set_sensitive(False)
    try:
        updated_model = controller.connect(token=token)
        render_model(updated_model)
    finally:
        token = ""
        connect_button.set_sensitive(True)


def run_gui() -> int:
    """Run the native read-only dashboard against the local API client."""

    Gtk = _load_gtk()
    model = build_initial_model()
    token_entry_controller = FirstRunTokenEntryController()
    application = Gtk.Application(application_id=APP_ID)

    def on_activate(app) -> None:
        window = Gtk.ApplicationWindow(application=app)
        window.set_title(APP_NAME)
        window.set_default_size(960, 800)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        _add_text_label(Gtk, content, APP_NAME, css_class="title-1")
        _add_text_label(
            Gtk,
            content,
            "Native read-only companion",
            css_class="title-3",
        )
        _add_text_label(
            Gtk,
            content,
            "Enter the API token configured for the local VRhotspot daemon. "
            "The token is used in memory for this validation only and is not saved.",
        )

        connection_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        token_entry = Gtk.PasswordEntry()
        token_entry.set_placeholder_text("API token")
        token_entry.set_show_peek_icon(True)
        token_entry.set_hexpand(True)
        connection_row.append(token_entry)

        connect_button = Gtk.Button(label="Connect / Validate token")
        connection_row.append(connect_button)
        content.append(connection_row)

        display_sections = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
        )
        content.append(display_sections)
        _render_display_model(Gtk, display_sections, model)

        def on_connect(_widget) -> None:
            _connect_from_token_entry(
                token_entry=token_entry,
                connect_button=connect_button,
                controller=token_entry_controller,
                render_model=lambda updated_model: _render_display_model(
                    Gtk,
                    display_sections,
                    updated_model,
                ),
            )

        connect_button.connect("clicked", on_connect)
        token_entry.connect("activate", on_connect)

        scroller.set_child(content)
        window.set_child(scroller)
        window.present()

    application.connect("activate", on_activate)
    return int(application.run([]))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vrhotspot-flatpak",
        description="VR Hotspot Flatpak app shell prototype",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a deterministic smoke path or launch the GTK dashboard."""

    args = _argument_parser().parse_args(argv)
    if args.smoke_json:
        print(render_smoke_json())
        return 0
    if args.live_pairing_smoke_json:
        return run_live_pairing_smoke_json()

    try:
        return run_gui()
    except GuiUnavailableError:
        print(
            "The native dashboard requires GTK 4 and PyGObject. "
            "Use --smoke-json for the offline shell check.",
            file=sys.stderr,
        )
        return 2
