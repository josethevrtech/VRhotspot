from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "assets" / "devhub_upload.js"
SOURCE = ROOT / "assets" / "pro_guided_workflow.js"
STYLE = ROOT / "assets" / "pro_guided_workflow.css"
SESSION = ROOT / "assets" / "browser_session.js"


def test_pro_runtime_is_loaded_as_versioned_dedicated_assets() -> None:
    source = LOADER.read_text(encoding="utf-8")

    assert "/assets/browser_session.js?v=139-session-hotfix" in source
    assert "/assets/pro_guided_workflow.js?v=141-pro-guided-recovery" in source
    assert "script.async = false" in source


def test_pro_setup_is_one_guided_five_step_workflow() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "buildProGuidedWorkflow" in source
    for step_id in (
        "proStepAdapter",
        "proStepPerformance",
        "proStepHotspot",
        "proStepAdvanced",
        "proStepAction",
    ):
        assert step_id in source
    assert "Choose Wi-Fi adapter" in source
    assert "Choose performance mode" in source
    assert "Configure hotspot" in source
    assert "Fine-tune hotspot" in source
    assert "Start hotspot" in source


def test_pro_setup_removes_sticky_save_bar_and_autosaves() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert ".pro-configuration > .settings-header" in style
    assert "display: none !important" in style
    assert "scheduleSave" in source
    assert "saveConfiguration" in source
    assert "btnSaveConfig" in source
    assert "AUTOSAVE_DELAY_MS" in source
    assert "Apply Changes & Restart" in source
    assert "btnSaveRestart" in source


def test_pro_setup_preserves_order_and_dependencies() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "channel_auto_select" in source
    assert "channel5.disabled" in source
    assert "channel6.disabled" in source
    assert "bridge_mode" in source
    assert "bridge_name" in source
    assert "bridge_uplink" in source


def test_navigation_cleanup_runs_before_optional_dom_transforms() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    enforce = source.index("function enforceNavigation()")
    initialize = source.index("function initialize()")
    first_call = source.index("enforceNavigation();", initialize)
    guided_call = source.index("buildGuidedSetup()", initialize)

    assert enforce < initialize
    assert first_call < guided_call
    assert "RETRY_LIMIT" in source
    assert "new MutationObserver((records) =>" in source
    assert "catch (error)" in source


def test_guided_builder_recovers_from_post_login_base_layout() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "function guidedPrerequisitesReady()" in source
    assert "proHotspotConfiguration" in source
    assert "pro-service-card" in source
    assert "configuration.querySelector('.preset-bar')" in source
    assert "requiredIds.every" in source
    assert "oldShell" not in source
    assert "RETRY_LIMIT = 600" in source
    assert "resetRetryBudget" in source
    assert "data-auth-state" in source
    assert "data-ui-mode" in source
    assert "pageshow" in source
    assert "visibilitychange" in source


def test_guided_runtime_exposes_stage_and_error_state() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "dataset.proGuidedStage" in source
    assert "waiting-for-base" in source
    assert "guided-ready" in source
    assert "dataset.proGuidedError" in source
    assert "/assets/pro_guided_workflow.css?v=141-pro-guided-recovery" in source


def test_connection_quality_is_pro_only_and_not_a_sidebar_tab() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "Connection Quality" in source
    assert "buildConnectionQuality" in source
    assert '.nav-item[data-tab="telemetry"]' in source
    assert "?.remove()" in source
    assert "#tab-telemetry" in style
    assert "View detailed charts and client measurements" in source
    assert "Measure connection quality" in source


def test_diagnostics_logs_and_support_are_one_troubleshooting_page() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "buildTroubleshooting" in source
    assert "Troubleshooting" in source
    assert "tab-troubleshooting" in source
    assert "System Health & Diagnostic Checks" in source
    assert "Runtime Details, Logs & Support" in source
    assert "logsNav?.remove()" in source
    for control in ("btnRepair", "btnRestart", "btnRefreshPreflight"):
        assert control in source


def test_setup_navigation_uses_vector_wifi_icon_not_emoji() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "pro-nav-svg" in source
    assert "replaceNav(overviewNav, 'wifi', 'Set Up Hotspot')" in source
    assert "📡" not in source


def test_density_pass_compacts_header_and_uses_horizontal_step_space() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "polishProSetupDensity" in loader
    assert "pro-guided-header-meta" in loader
    assert "proHeaderStatus" in loader
    assert "width: min(1280px, 100%)" in style
    assert "grid-template-columns: minmax(190px, 238px) minmax(0, 1fr)" in style
    assert "padding: 14px 18px" in style


def test_density_pass_removes_legacy_essentials_and_duplicate_qos_ui() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "removeLegacyEssentials" in loader
    assert "document.querySelectorAll('.pro-config-essentials')" in loader
    assert "qosField.hidden = true" in loader
    assert "hiddenControls.appendChild(qosField)" in loader
    assert '[data-field="qos_preset"]' in style
    assert ".pro-config-essentials" in style


def test_performance_mode_is_the_only_visible_profile_control() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "PROFILE_COPY" in loader
    assert "proPerformanceDescription" in loader
    assert "aria-pressed" in loader
    assert "is-selected" in loader
    assert ".pro-performance-picker .btn-group" in style
    assert "repeat(4, minmax(0, 1fr))" in style


def test_pro_password_row_matches_basic_three_control_pattern() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "function compactPassword()" in loader
    assert "row.append(input, reveal, qr)" in loader
    assert "qr.textContent = 'QR'" in loader
    assert "Show or hide password" in loader
    assert ".pro-password-row" in style
    assert "grid-template-columns: minmax(0, 1fr) 50px 50px" in style
    assert "height: 50px" in style


def test_adapter_controls_are_one_compact_row() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "proAdapterRecommendedBadge" in loader
    assert "Rescan adapters" in loader
    assert "recommended.hidden = true" in loader
    assert ".pro-adapter-row" in style
    assert "grid-template-columns: minmax(0, 1fr) auto auto" in style


@pytest.mark.parametrize("asset", [LOADER, SOURCE, SESSION])
def test_portal_extensions_parse_with_node(asset: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    completed = subprocess.run(
        [node, "--check", str(asset)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr