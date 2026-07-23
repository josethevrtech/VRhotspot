"""Minimal unprivileged native dashboard for the VRhotspot Flatpak."""

from .app import (
    APP_ID,
    APP_NAME,
    DashboardControlsBoundary,
    DashboardSectionLabels,
    FirstRunTokenEntryController,
    MAX_LIVE_SMOKE_JSON_BYTES,
    MAX_SMOKE_JSON_BYTES,
    NativeDashboardModel,
    build_dashboard_model,
    build_initial_model,
    build_smoke_payload,
    main,
    render_smoke_json,
    run_live_pairing_smoke_json,
)

__all__ = [
    "APP_ID",
    "APP_NAME",
    "DashboardControlsBoundary",
    "DashboardSectionLabels",
    "FirstRunTokenEntryController",
    "MAX_LIVE_SMOKE_JSON_BYTES",
    "MAX_SMOKE_JSON_BYTES",
    "NativeDashboardModel",
    "build_dashboard_model",
    "build_initial_model",
    "build_smoke_payload",
    "main",
    "render_smoke_json",
    "run_live_pairing_smoke_json",
]
