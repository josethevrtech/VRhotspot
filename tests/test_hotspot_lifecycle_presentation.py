from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_JS = ROOT / "assets" / "ui.js"
UI_CSS = ROOT / "assets" / "ui.css"
GUIDED_JS = ROOT / "assets" / "basic_guided.js"
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
PRO_GUIDED = ROOT / "assets" / "pro_guided_workflow.js"
CI = ROOT / ".github" / "workflows" / "ci.yml"

LIFECYCLE_LABELS = (
    "Starting…",
    "Stopping…",
    "Restarting…",
    "Repairing…",
    "Needs attention",
    "Checking status…",
)


def test_canonical_resolver_is_phase_first_and_never_contradicts() -> None:
    source = UI_JS.read_text(encoding="utf-8")

    assert "function resolveHotspotLifecycle" in source
    assert "HOTSPOT_LIFECYCLE_LABELS" in source
    for label in LIFECYCLE_LABELS:
        assert label in source

    # The old pill composition rendered "Stopped | Starting" style
    # contradictions: running/stopped decided the first segment and the
    # phase was appended afterward. Both halves must stay gone.
    assert "statusParts.push('Stopped')" not in source
    assert "statusParts.push('Running')" not in source
    assert "phase.charAt(0).toUpperCase()" not in source
    assert "statusParts = [HOTSPOT_LIFECYCLE_LABELS[lifecycle]]" in source

    # setPill publishes the canonical lifecycle for every consumer.
    assert "pill.dataset.hotspotState = lifecycle;" in source


def test_lifecycle_actions_render_optimistic_transient_states() -> None:
    source = UI_JS.read_text(encoding="utf-8")

    assert "function setOptimisticHotspotPhase" in source
    assert "setOptimisticHotspotPhase('starting');" in source
    assert "setOptimisticHotspotPhase('stopping');" in source
    assert "setOptimisticHotspotPhase('restarting');" in source
    assert "setOptimisticHotspotPhase('repairing');" in source
    # Stale in-flight status responses may not overwrite the optimistic
    # state; only a refresh issued after the action replaces it.
    assert "refreshRequestSeq += 1;" in source


def test_wifi_state_icon_is_local_decorative_and_reduced_motion_aware() -> None:
    ui_js = UI_JS.read_text(encoding="utf-8")
    ui_css = UI_CSS.read_text(encoding="utf-8")

    assert "function createHotspotWifiIcon" in ui_js
    assert "document.createElementNS(SVG_NS, 'svg')" in ui_js
    assert "svg.setAttribute('aria-hidden', 'true');" in ui_js
    assert "function ensurePillWifiIcon" in ui_js
    # Inline SVG only: no emoji, icon font, or remote image sources.
    assert "📶" not in ui_js
    assert "createHotspotWifiIcon" in ui_js and "<img" not in ui_js

    assert ".hotspot-wifi-icon" in ui_css
    for state in ("running", "starting", "stopping", "restarting", "repairing", "stopped", "error"):
        assert f'[data-hotspot-state="{state}"] .hotspot-wifi-icon' in ui_css
    assert "@keyframes hotspotWifiPulse" in ui_css
    reduced = ui_css.split("@media (prefers-reduced-motion: reduce)", 1)
    assert len(reduced) == 2, "reduced-motion handling must exist"
    assert "animation: none !important;" in reduced[1]


def test_basic_guided_consumes_canonical_state_with_exact_lifecycle_copy() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "dataset.hotspotState" in source
    assert "function lifecycleFromText" in source
    assert "Working…" not in source

    # Exact Basic lifecycle copy.
    assert "'Starting hotspot'" in source
    assert "'Stopping hotspot'" in source
    assert "'Hotspot running'" in source
    assert "'Start hotspot'" in source
    assert "VRhotspot is applying the connection settings." in source
    assert "VRhotspot is shutting down the hotspot safely." in source
    assert "Your hotspot is active and ready for your headset." in source
    assert "The hotspot is stopped." in source

    # The transitional button mirrors the exact lifecycle action and the
    # icon comes from the shared factory.
    assert "TRANSIENT_STATES" in source
    assert "createHotspotWifiIcon('basic-guided-status-icon')" in source


def test_pro_service_card_consumes_canonical_state() -> None:
    field_visibility = FIELD_VISIBILITY.read_text(encoding="utf-8")
    pro_guided = PRO_GUIDED.read_text(encoding="utf-8")

    assert "SERVICE_STATE_PRESENTATION" in field_visibility
    assert "card.dataset.hotspotState = state.name" in field_visibility
    assert "createHotspotWifiIcon('pro-service-state-icon')" in field_visibility
    assert "VRhotspot is applying the connection settings." in field_visibility
    assert "VRhotspot is shutting down the hotspot safely." in field_visibility

    assert "dataset.hotspotState" in pro_guided


def test_lifecycle_dom_suite_is_registered_in_ci() -> None:
    ci = CI.read_text(encoding="utf-8")

    assert "tests/js/hotspot_lifecycle_status.test.mjs" in ci
