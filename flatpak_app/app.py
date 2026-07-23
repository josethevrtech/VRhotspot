"""Launchable, read-only Flatpak application shell prototype."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from typing import Any, Sequence

from flatpak_client import (
    DiagnosticsControlUiController,
    DiagnosticsControlUiModel,
    FirstRunResult,
    FirstRunState,
    PresentationMode,
)


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
MAX_SMOKE_JSON_BYTES = 8_192


class GuiUnavailableError(RuntimeError):
    """GTK 4 or PyGObject is unavailable for the graphical shell."""


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


def _add_text_label(Gtk, container, text: str, *, css_class: str | None = None) -> None:
    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_xalign(0.0)
    if css_class:
        label.add_css_class(css_class)
    container.append(label)


def run_gui() -> int:
    """Run the rough GTK window without contacting the host daemon."""

    Gtk = _load_gtk()
    model = build_initial_model()
    application = Gtk.Application(application_id=APP_ID)

    def on_activate(app) -> None:
        window = Gtk.ApplicationWindow(application=app)
        window.set_title(APP_NAME)
        window.set_default_size(560, 420)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        _add_text_label(Gtk, content, APP_NAME, css_class="title-1")
        _add_text_label(
            Gtk,
            content,
            "Flatpak app shell prototype",
            css_class="title-3",
        )
        _add_text_label(
            Gtk,
            content,
            "This rough shell is intentionally offline and unpaired. "
            "Credential entry and live daemon wiring remain future work.",
        )
        _add_text_label(
            Gtk,
            content,
            f"Presentation mode: {model.mode.value.capitalize()}",
        )
        _add_text_label(
            Gtk,
            content,
            f"Daemon: {model.daemon.title}",
        )
        _add_text_label(
            Gtk,
            content,
            f"Pairing: {model.pairing.title}",
        )
        _add_text_label(
            Gtk,
            content,
            "Support-bundle export is visible as a disabled placeholder; "
            "no portal or file access is wired.",
        )
        _add_text_label(
            Gtk,
            content,
            "No lifecycle or configuration controls are available.",
            css_class="dim-label",
        )

        window.set_child(content)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deterministic smoke path or launch the GTK placeholder."""

    args = _argument_parser().parse_args(argv)
    if args.smoke_json:
        print(render_smoke_json())
        return 0

    try:
        return run_gui()
    except GuiUnavailableError:
        print(
            "The graphical prototype requires GTK 4 and PyGObject. "
            "Use --smoke-json for the offline shell check.",
            file=sys.stderr,
        )
        return 2
