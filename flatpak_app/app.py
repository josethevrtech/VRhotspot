"""Launchable, read-only Flatpak application shell prototype."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
import sys
from typing import Any, Sequence
import warnings

from flatpak_client import (
    DiagnosticsControlUiController,
    DiagnosticsControlUiModel,
    FirstRunResult,
    FirstRunState,
    LocalApiClient,
    PresentationMode,
    StatusSeverity,
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


def _add_section_heading(Gtk, container, text: str) -> None:
    _add_text_label(Gtk, container, text, css_class="heading")


def _render_display_model(Gtk, container, model: DiagnosticsControlUiModel) -> None:
    """Render only bounded, token-free fields from the existing UI model."""

    _clear_box(container)

    _add_section_heading(Gtk, container, "Daemon status")
    _add_text_label(Gtk, container, model.daemon.title, css_class="title-4")
    _add_text_label(Gtk, container, model.daemon.message)

    _add_section_heading(Gtk, container, "Pairing status")
    _add_text_label(Gtk, container, model.pairing.title, css_class="title-4")
    _add_text_label(Gtk, container, model.pairing.message)

    _add_section_heading(Gtk, container, "Adapter readiness")
    _add_text_label(Gtk, container, model.adapters.title, css_class="title-4")
    _add_text_label(Gtk, container, model.adapters.summary)
    if model.adapters.cards:
        for card in model.adapters.cards:
            bands = ", ".join(card.supported_bands) or "Bands not reported"
            recommendation = " · Recommended" if card.recommended else ""
            _add_text_label(
                Gtk,
                container,
                f"{card.interface}: {card.readiness_label}{recommendation}",
                css_class="heading",
            )
            _add_text_label(Gtk, container, f"{bands} · {card.summary}")
    else:
        _add_text_label(
            Gtk,
            container,
            "No adapter cards are available.",
            css_class="dim-label",
        )

    _add_section_heading(Gtk, container, "Preflight")
    _add_text_label(
        Gtk,
        container,
        f"{model.preflight.readiness_label}: {model.preflight.summary}",
        css_class="title-4",
    )
    for fact in model.preflight.facts:
        _add_text_label(Gtk, container, f"{fact.label}: {fact.value}")
    for issue in model.preflight.issues:
        _add_text_label(
            Gtk,
            container,
            f"Issue ({issue.severity.value}): {issue.message}",
        )
    for action in model.preflight.actions:
        _add_text_label(
            Gtk,
            container,
            f"Guidance (display only): {action.message}",
        )
    if not (
        model.preflight.facts
        or model.preflight.issues
        or model.preflight.actions
    ):
        _add_text_label(
            Gtk,
            container,
            "No preflight details are available.",
            css_class="dim-label",
        )

    _add_section_heading(Gtk, container, model.support_bundle.title)
    _add_text_label(Gtk, container, model.support_bundle.summary)
    export_button = Gtk.Button(label=model.support_bundle.action_label)
    export_button.set_sensitive(model.support_bundle.action_enabled)
    container.append(export_button)


def run_gui() -> int:
    """Run the first-run GTK prototype against the read-only local API client."""

    Gtk = _load_gtk()
    model = build_initial_model()
    token_entry_controller = FirstRunTokenEntryController()
    application = Gtk.Application(application_id=APP_ID)

    def on_activate(app) -> None:
        window = Gtk.ApplicationWindow(application=app)
        window.set_title(APP_NAME)
        window.set_default_size(680, 760)

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
            "First-run connection prototype",
            css_class="title-3",
        )
        _add_text_label(
            Gtk,
            content,
            "Enter the API token configured for the local VRhotspot daemon. "
            "The token is used in memory for this validation only and is not saved.",
        )

        token_entry = Gtk.PasswordEntry()
        token_entry.set_placeholder_text("API token")
        token_entry.set_show_peek_icon(True)
        content.append(token_entry)

        connect_button = Gtk.Button(label="Connect / Validate token")
        content.append(connect_button)

        display_sections = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        content.append(display_sections)
        _render_display_model(Gtk, display_sections, model)

        def on_connect(_widget) -> None:
            token = token_entry.get_text()
            token_entry.set_text("")
            connect_button.set_sensitive(False)
            try:
                updated_model = token_entry_controller.connect(token=token)
                _render_display_model(Gtk, display_sections, updated_model)
            finally:
                token = ""
                connect_button.set_sensitive(True)

        connect_button.connect("clicked", on_connect)
        token_entry.connect("activate", on_connect)

        _add_text_label(
            Gtk,
            content,
            "Support-bundle export remains disabled. No lifecycle or "
            "configuration controls are available.",
            css_class="dim-label",
        )

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
    """Run the deterministic smoke path or launch the GTK placeholder."""

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
            "The graphical prototype requires GTK 4 and PyGObject. "
            "Use --smoke-json for the offline shell check.",
            file=sys.stderr,
        )
        return 2
