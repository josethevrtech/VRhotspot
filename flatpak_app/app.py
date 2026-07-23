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
_DASHBOARD_CSS = """
.dashboard-card {
  border-radius: 10px;
}
.status-badge {
  border-radius: 9999px;
  padding: 2px 8px;
}
.recommended-card {
  border-width: 2px;
}
.unavailable-card {
  opacity: 0.82;
}
"""

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
class DashboardSectionLabels:
    """Stable product labels shared by the native dashboard sections."""

    overview: str = "Dashboard Overview"
    connection_pairing: str = "Connection & Pairing"
    readiness_summary: str = "Readiness & Adapter Summary"
    adapter_readiness: str = "Adapter Readiness"
    preflight_diagnostics: str = "Preflight Diagnostics"
    host_summary: str = "Readiness & Host Summary"
    facts: str = "Facts"
    blocking_issues: str = "Blocking Issues"
    warnings: str = "Warnings"
    other_issues: str = "Other Issues"
    recommended_actions: str = "Recommended Actions"
    support_bundle: str = "Support Bundle"
    controls_boundary: str = "Controls Boundary"
    unavailable_features: str = "Unavailable Features"


@dataclass(frozen=True)
class NativeDashboardModel:
    """Safe sections consumed by the native GTK dashboard."""

    labels: DashboardSectionLabels
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
    labels = DashboardSectionLabels()
    return NativeDashboardModel(
        labels=labels,
        daemon=model.daemon,
        pairing=model.pairing,
        adapter_readiness=model.adapters,
        preflight=model.preflight,
        support_bundle=model.support_bundle,
        controls=DashboardControlsBoundary(
            visible=True,
            severity=StatusSeverity.UNKNOWN,
            title=labels.controls_boundary,
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


def _install_dashboard_styles(Gtk, widget) -> None:
    """Install the small native-dashboard stylesheet when GTK supports it."""

    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(_DASHBOARD_CSS)
        display = widget.get_display()
        if display is None:
            return
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    except (AttributeError, TypeError, ValueError):
        return


def _add_status_badge(
    Gtk,
    container,
    severity: StatusSeverity,
    *,
    text: str | None = None,
):
    if not isinstance(severity, StatusSeverity):
        severity = StatusSeverity.UNKNOWN
    badge = Gtk.Frame()
    badge.add_css_class("status-badge")
    badge.add_css_class(f"severity-{severity.value}")
    badge.add_css_class(_severity_css_class(severity))

    label = Gtk.Label(label=text or _severity_text(severity))
    label.add_css_class("caption")
    label.add_css_class("heading")
    label.add_css_class(_severity_css_class(severity))
    label.set_margin_top(3)
    label.set_margin_bottom(3)
    label.set_margin_start(8)
    label.set_margin_end(8)
    badge.set_child(label)
    container.append(badge)
    return badge


def _add_section_heading(
    Gtk,
    container,
    title: str,
    description: str,
) -> None:
    _add_text_label(Gtk, container, title, css_class="title-2")
    _add_text_label(
        Gtk,
        container,
        description,
        css_class="dim-label",
    )


def _new_card(
    Gtk,
    *,
    title: str,
    severity: StatusSeverity | None,
    css_classes: Sequence[str] = (),
):
    frame = Gtk.Frame()
    frame.add_css_class("dashboard-card")
    for css_class in css_classes:
        frame.add_css_class(css_class)
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
    if severity is not None:
        _add_status_badge(Gtk, heading, severity)
    body.append(heading)
    frame.set_child(body)
    return frame, body


def _new_fact_tile(Gtk, *, label: str, value: str):
    frame = Gtk.Frame()
    frame.add_css_class("dashboard-card")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    body.set_margin_top(10)
    body.set_margin_bottom(10)
    body.set_margin_start(12)
    body.set_margin_end(12)
    _add_text_label(Gtk, body, label, css_class="caption")
    _add_text_label(Gtk, body, value, css_class="heading")
    frame.set_child(body)
    return frame


def _add_adapter_card(Gtk, container, card) -> None:
    card_classes = ("recommended-card",) if card.recommended else ()
    frame, body = _new_card(
        Gtk,
        title=card.interface,
        severity=card.severity,
        css_classes=card_classes,
    )
    if card.recommended:
        recommendation_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        _add_status_badge(
            Gtk,
            recommendation_row,
            StatusSeverity.OK,
            text="RECOMMENDED",
        )
        _add_text_label(
            Gtk,
            recommendation_row,
            "Daemon-recommended adapter",
            css_class="heading",
        )
        body.append(recommendation_row)

    _add_text_label(
        Gtk,
        body,
        f"Readiness: {card.readiness_label}",
        css_class="title-4",
    )
    _add_text_label(Gtk, body, card.summary)

    details = Gtk.Grid(column_spacing=10, row_spacing=10)
    details.set_column_homogeneous(True)
    bands = ", ".join(card.supported_bands) or "Not reported"
    score = (
        str(card.recommendation_score)
        if card.recommendation_score is not None
        else "Not reported"
    )
    facts = (
        ("Supported Bands", bands),
        ("Recommendation Score", score),
        ("Driver", card.driver),
        ("Bus", card.bus_type),
    )
    for index, (label, value) in enumerate(facts):
        details.attach(
            _new_fact_tile(Gtk, label=label, value=value),
            index % 2,
            index // 2,
            1,
            1,
        )
    body.append(details)

    _add_text_label(Gtk, body, "Top Reasons", css_class="heading")
    if card.reasons:
        for reason in card.reasons:
            _add_text_label(Gtk, body, f"• {reason}")
    else:
        _add_text_label(
            Gtk,
            body,
            "No readiness reasons were reported.",
            css_class="dim-label",
        )

    container.append(frame)


def _populate_daemon_card(Gtk, container, dashboard: NativeDashboardModel) -> None:
    daemon = dashboard.daemon
    title = _add_text_label(
        Gtk,
        container,
        daemon.title,
        css_class="title-2",
    )
    title.add_css_class(_severity_css_class(daemon.severity))
    _add_text_label(Gtk, container, daemon.message)


def _populate_pairing_card(Gtk, container, dashboard: NativeDashboardModel) -> None:
    pairing = dashboard.pairing
    if pairing.paired:
        _add_status_badge(
            Gtk,
            container,
            StatusSeverity.OK,
            text="PAIRED",
        )
    title = _add_text_label(
        Gtk,
        container,
        pairing.title,
        css_class="title-2",
    )
    title.add_css_class(_severity_css_class(pairing.severity))
    _add_text_label(Gtk, container, pairing.message)


def _populate_adapter_summary_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    adapters = dashboard.adapter_readiness
    _add_text_label(Gtk, container, adapters.title, css_class="title-3")
    _add_text_label(Gtk, container, adapters.summary)

    recommendation = Gtk.Frame()
    recommendation.add_css_class("dashboard-card")
    recommendation.add_css_class("recommended-card")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    body.set_margin_top(12)
    body.set_margin_bottom(12)
    body.set_margin_start(12)
    body.set_margin_end(12)

    _add_text_label(Gtk, body, "Recommended", css_class="caption")
    _add_text_label(
        Gtk,
        body,
        adapters.recommended_interface,
        css_class="title-2",
    )
    if adapters.recommended_interface == "Not reported":
        _add_status_badge(
            Gtk,
            body,
            StatusSeverity.UNKNOWN,
            text="NOT REPORTED",
        )
    else:
        _add_status_badge(
            Gtk,
            body,
            StatusSeverity.OK,
            text="DAEMON RECOMMENDED",
        )
    recommendation.set_child(body)
    container.append(recommendation)


def _add_issue_group(
    Gtk,
    container,
    *,
    title: str,
    issues,
    empty_text: str,
) -> None:
    frame, body = _new_card(Gtk, title=title, severity=None)
    if issues:
        for issue in issues:
            issue_row = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4,
            )
            issue_heading = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            _add_status_badge(Gtk, issue_heading, issue.severity)
            _add_text_label(
                Gtk,
                issue_heading,
                issue.code,
                css_class="caption",
            )
            issue_row.append(issue_heading)
            _add_text_label(Gtk, issue_row, issue.message)
            body.append(issue_row)
    else:
        _add_text_label(Gtk, body, empty_text, css_class="dim-label")
    container.append(frame)


def _populate_preflight_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    preflight = dashboard.preflight
    _add_text_label(
        Gtk,
        container,
        dashboard.labels.host_summary,
        css_class="title-3",
    )
    readiness_row = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=8,
    )
    readiness_label = _add_text_label(
        Gtk,
        readiness_row,
        preflight.readiness_label,
        css_class="title-2",
    )
    readiness_label.set_hexpand(True)
    _add_status_badge(Gtk, readiness_row, preflight.severity)
    container.append(readiness_row)
    _add_text_label(Gtk, container, preflight.summary)

    _add_text_label(
        Gtk,
        container,
        dashboard.labels.facts,
        css_class="heading",
    )
    if preflight.facts:
        facts_grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        facts_grid.set_column_homogeneous(True)
        for index, fact in enumerate(preflight.facts):
            facts_grid.attach(
                _new_fact_tile(Gtk, label=fact.label, value=fact.value),
                index % 2,
                index // 2,
                1,
                1,
            )
        container.append(facts_grid)
    else:
        _add_text_label(
            Gtk,
            container,
            "No preflight facts are available.",
            css_class="dim-label",
        )

    blocking = tuple(
        issue
        for issue in preflight.issues
        if issue.severity in {StatusSeverity.BLOCKED, StatusSeverity.ERROR}
    )
    warnings = tuple(
        issue
        for issue in preflight.issues
        if issue.severity is StatusSeverity.WARNING
    )
    other = tuple(
        issue
        for issue in preflight.issues
        if issue.severity not in {
            StatusSeverity.BLOCKED,
            StatusSeverity.ERROR,
            StatusSeverity.WARNING,
        }
    )
    issues_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
    issues_grid.set_column_homogeneous(True)
    blocking_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    warning_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    _add_issue_group(
        Gtk,
        blocking_box,
        title=dashboard.labels.blocking_issues,
        issues=blocking,
        empty_text="No blocking issues.",
    )
    _add_issue_group(
        Gtk,
        warning_box,
        title=dashboard.labels.warnings,
        issues=warnings,
        empty_text="No warnings.",
    )
    issues_grid.attach(blocking_box, 0, 0, 1, 1)
    issues_grid.attach(warning_box, 1, 0, 1, 1)
    if other:
        other_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _add_issue_group(
            Gtk,
            other_box,
            title=dashboard.labels.other_issues,
            issues=other,
            empty_text="No other issues.",
        )
        issues_grid.attach(other_box, 0, 1, 2, 1)
    container.append(issues_grid)

    actions_frame, actions_body = _new_card(
        Gtk,
        title=dashboard.labels.recommended_actions,
        severity=None,
    )
    _add_text_label(
        Gtk,
        actions_body,
        "Display-only guidance; no action can be run from this dashboard.",
        css_class="dim-label",
    )
    if preflight.actions:
        for action in preflight.actions:
            _add_text_label(
                Gtk,
                actions_body,
                action.message,
            )
            _add_text_label(
                Gtk,
                actions_body,
                action.code,
                css_class="caption",
            )
    else:
        _add_text_label(
            Gtk,
            actions_body,
            "No actions recommended.",
            css_class="dim-label",
        )
    container.append(actions_frame)


def _populate_support_bundle_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    support_bundle = dashboard.support_bundle
    _add_status_badge(
        Gtk,
        container,
        StatusSeverity.UNKNOWN,
        text="NOT AVAILABLE YET",
    )
    _add_text_label(Gtk, container, support_bundle.summary)
    export_button = Gtk.Button(label=support_bundle.action_label)
    export_button.set_sensitive(False)
    container.append(export_button)
    _add_text_label(
        Gtk,
        container,
        "Export is disabled until the desktop save flow is implemented.",
        css_class="dim-label",
    )


def _populate_controls_card(
    Gtk,
    container,
    dashboard: NativeDashboardModel,
) -> None:
    controls = dashboard.controls
    _add_status_badge(
        Gtk,
        container,
        controls.severity,
        text=controls.readiness_label.upper(),
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
    labels = dashboard.labels
    _add_section_heading(
        Gtk,
        container,
        labels.overview,
        "Daemon-owned readiness and diagnostics, presented without host mutation.",
    )

    _add_section_heading(
        Gtk,
        container,
        labels.connection_pairing,
        "Local daemon reachability and caller-validated pairing state.",
    )
    connection_grid = Gtk.Grid(column_spacing=16, row_spacing=16)
    connection_grid.set_column_homogeneous(True)

    daemon_frame, daemon_body = _new_card(
        Gtk,
        title="Daemon Status",
        severity=dashboard.daemon.severity,
    )
    _populate_daemon_card(Gtk, daemon_body, dashboard)
    connection_grid.attach(daemon_frame, 0, 0, 1, 1)

    pairing_frame, pairing_body = _new_card(
        Gtk,
        title="Pairing Status",
        severity=dashboard.pairing.severity,
    )
    _populate_pairing_card(Gtk, pairing_body, dashboard)
    connection_grid.attach(pairing_frame, 1, 0, 1, 1)
    container.append(connection_grid)

    summary_frame, summary_body = _new_card(
        Gtk,
        title=labels.readiness_summary,
        severity=dashboard.adapter_readiness.severity,
    )
    _populate_adapter_summary_card(Gtk, summary_body, dashboard)
    container.append(summary_frame)

    _add_section_heading(
        Gtk,
        container,
        labels.adapter_readiness,
        "Daemon-reported capabilities, readiness, and recommendation details.",
    )
    adapters_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
    )
    if dashboard.adapter_readiness.cards:
        for card in dashboard.adapter_readiness.cards:
            _add_adapter_card(Gtk, adapters_box, card)
    else:
        empty_frame, empty_body = _new_card(
            Gtk,
            title="No Adapters Reported",
            severity=StatusSeverity.UNKNOWN,
        )
        _add_text_label(
            Gtk,
            empty_body,
            "No adapter cards are available.",
            css_class="dim-label",
        )
        adapters_box.append(empty_frame)
    container.append(adapters_box)

    _add_section_heading(
        Gtk,
        container,
        labels.preflight_diagnostics,
        "Read-only readiness report using the canonical daemon response.",
    )
    preflight_frame, preflight_body = _new_card(
        Gtk,
        title=labels.preflight_diagnostics,
        severity=dashboard.preflight.severity,
    )
    _populate_preflight_card(Gtk, preflight_body, dashboard)
    container.append(preflight_frame)

    _add_section_heading(
        Gtk,
        container,
        labels.unavailable_features,
        "Planned capabilities shown explicitly as unavailable.",
    )
    unavailable_grid = Gtk.Grid(column_spacing=16, row_spacing=16)
    unavailable_grid.set_column_homogeneous(True)
    support_frame, support_body = _new_card(
        Gtk,
        title=labels.support_bundle,
        severity=dashboard.support_bundle.severity,
        css_classes=("unavailable-card",),
    )
    _populate_support_bundle_card(Gtk, support_body, dashboard)
    unavailable_grid.attach(support_frame, 0, 0, 1, 1)

    controls_frame, controls_body = _new_card(
        Gtk,
        title=labels.controls_boundary,
        severity=dashboard.controls.severity,
        css_classes=("unavailable-card",),
    )
    _populate_controls_card(Gtk, controls_body, dashboard)
    unavailable_grid.attach(controls_frame, 1, 0, 1, 1)

    container.append(unavailable_grid)


def _render_display_model(Gtk, container, model: DiagnosticsControlUiModel) -> None:
    """Render only bounded, token-free fields from the existing UI model."""

    _render_dashboard_model(Gtk, container, build_dashboard_model(model))


def _set_placeholder_text_compat(widget: Any, value: str) -> None:
    """Set placeholder text when the active GTK binding supports either API."""

    direct_setter = getattr(widget, "set_placeholder_text", None)
    if callable(direct_setter):
        try:
            direct_setter(value)
        except (AttributeError, TypeError):
            pass
        else:
            return

    property_setter = getattr(widget, "set_property", None)
    if not callable(property_setter):
        return

    property_finder = getattr(widget, "find_property", None)
    if callable(property_finder):
        try:
            if property_finder("placeholder-text") is None:
                return
        except (AttributeError, TypeError):
            return

    try:
        property_setter("placeholder-text", value)
    except (AttributeError, TypeError):
        pass


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
        window.set_default_size(1040, 900)
        _install_dashboard_styles(Gtk, window)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(28)
        content.set_margin_bottom(28)
        content.set_margin_start(28)
        content.set_margin_end(28)

        app_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
        )
        header_copy = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        header_copy.set_hexpand(True)
        _add_text_label(Gtk, header_copy, APP_NAME, css_class="title-1")
        _add_text_label(
            Gtk,
            header_copy,
            "Native Dashboard",
            css_class="title-3",
        )
        _add_text_label(
            Gtk,
            header_copy,
            "Local VR readiness and diagnostics at a glance.",
            css_class="dim-label",
        )
        app_header.append(header_copy)
        header_badges = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        _add_status_badge(
            Gtk,
            header_badges,
            StatusSeverity.UNKNOWN,
            text="NATIVE GTK",
        )
        _add_status_badge(
            Gtk,
            header_badges,
            StatusSeverity.OK,
            text="READ-ONLY",
        )
        app_header.append(header_badges)
        content.append(app_header)

        connection_frame, connection_body = _new_card(
            Gtk,
            title="Connect to Local Daemon",
            severity=None,
        )
        _add_text_label(
            Gtk,
            connection_body,
            "Enter the API token configured for the local VRhotspot daemon. "
            "The token is used in memory for this validation only and is not saved.",
        )

        connection_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        token_entry = Gtk.PasswordEntry()
        _set_placeholder_text_compat(token_entry, "API token")
        token_entry.set_show_peek_icon(True)
        token_entry.set_hexpand(True)
        connection_row.append(token_entry)

        connect_button = Gtk.Button(label="Connect / Validate token")
        connection_row.append(connect_button)
        connection_body.append(connection_row)
        _add_text_label(
            Gtk,
            connection_body,
            "The entry is cleared before validation. Pairing does not persist a session.",
            css_class="dim-label",
        )
        content.append(connection_frame)

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
