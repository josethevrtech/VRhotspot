from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "assets" / "devhub_upload.js"
SOURCE = ROOT / "assets" / "pro_guided_workflow.js"
STYLE = ROOT / "assets" / "pro_guided_workflow.css"
OVERRIDE_STYLE = ROOT / "assets" / "pro_guided_authoritative.css"
SESSION = ROOT / "assets" / "browser_session.js"
DOM_TEST = ROOT / "tests" / "js" / "pro_guided_mode_transition.test.mjs"
FULL_DOM_TEST = ROOT / "tests" / "js" / "pro_guided_full_portal_integration.test.mjs"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_pro_runtime_is_loaded_as_versioned_authoritative_asset() -> None:
    source = LOADER.read_text(encoding="utf-8")

    assert "/assets/browser_session.js?v=139-session-hotfix" in source
    assert "/assets/pro_guided_workflow.js?v=148-authoritative-composer" in source
    assert "script.async = false" in source
    assert "polishProSetupDensity" not in source


def test_pro_setup_is_one_authoritative_five_step_workflow() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "buildProGuidedWorkflow" in source
    assert "window.VRHOTSPOT_PRO_COMPOSER = 'authoritative-v1'" in source
    assert "/assets/pro_guided_authoritative.css?v=148-authoritative-composer" in source
    for number, step_id in enumerate(
        (
            "proStepAdapter",
            "proStepPerformance",
            "proStepHotspot",
            "proStepAdvanced",
            "proStepAction",
        ),
        start=1,
    ):
        assert f"step({number}," in source
        assert step_id in source
    assert "Choose Wi-Fi adapter" in source
    assert "Choose performance mode" in source
    assert "Configure hotspot" in source
    assert "Fine-tune hotspot" in source
    assert "Start hotspot" in source


def test_pro_composer_waits_for_pro_mode_and_restores_basic_presentation() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "document.body?.dataset.uiMode === ADVANCED_MODE" in source
    assert "if (!isAdvancedMode())" in source
    assert "restoreBasicPresentation()" in source
    assert "waiting-for-pro" in source
    assert "internalHomes" in source
    assert "restoreInternalNode" in source
    assert "pro-runtime-wrapper" in source
    assert "RETRY_LIMIT" not in source


def test_pro_composer_resolves_detached_steps_inside_its_shell() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "shell?.querySelector(`#${id}`)" in source
    assert "missing guided slot" in source
    for step_id in (
        "proStepAdapter",
        "proStepPerformance",
        "proStepHotspot",
        "proStepAdvanced",
        "proStepAction",
    ):
        assert f"guidedSlot(shell, '{step_id}')" in source
    assert "el('proStepAdapter').appendChild" not in source
    assert "el('proStepPerformance').appendChild" not in source
    assert "el('proStepHotspot').appendChild" not in source
    assert "el('proStepAdvanced').appendChild" not in source


def test_step_three_contains_the_complete_connection_setup() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8") + OVERRIDE_STYLE.read_text(encoding="utf-8")

    for field in (
        "ssid",
        "wpa2_passphrase",
        "band_preference",
        "ap_security",
        "country",
        "enable_internet",
    ):
        assert f"'{field}'" in source
    assert "hotspot name, password, band, security, country" in source
    assert ".pro-hotspot-fields" in style
    assert "repeat(2, minmax(0, 1fr))" in style


def test_step_four_exposes_detailed_wireless_network_and_system_groups() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8") + OVERRIDE_STYLE.read_text(encoding="utf-8")

    assert "pro-guided-advanced-groups" in source
    assert "Channels, width, radio timing" in source
    assert "Gateway, DHCP, DNS" in source
    assert "Startup behavior, interface strategy" in source
    assert "pro-advanced-group-help" in source
    assert ".pro-guided-advanced-groups" in style
    assert ".pro-advanced-group-help" in style


def test_step_five_keeps_start_save_restart_and_repair_actions() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8") + OVERRIDE_STYLE.read_text(encoding="utf-8")

    for control in ("btnStart", "btnSaveConfig", "btnSaveRestart", "btnRepair"):
        assert control in source
    assert "Save Changes" in source
    assert "Save & Restart" in source
    assert "Repair Network" in source
    assert "Apply Changes & Restart" in source
    assert ".pro-guided-save-actions" in style
    assert ".pro-guided-secondary-actions" in style


def test_adapter_and_password_compaction_are_reversible() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8") + OVERRIDE_STYLE.read_text(encoding="utf-8")

    assert "rememberInternalHome(select)" in source
    assert "rememberInternalHome(recommended)" in source
    assert "rememberInternalHome(rescan)" in source
    assert "rememberInternalHome(input)" in source
    assert "rememberInternalHome(reveal)" in source
    assert "rememberInternalHome(qr)" in source
    assert "recommended.hidden = false" in source
    assert "Rescan adapters" in source
    assert ".pro-adapter-row" in style
    assert ".pro-password-row" in style


def test_legacy_density_pass_is_removed_and_defensively_neutralized() -> None:
    loader = LOADER.read_text(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")

    assert "polishProSetupDensity" not in loader
    assert "proDensityReady" in source
    assert "syncStepCopy" in source


def test_autosave_dependencies_and_status_feedback_are_preserved() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "AUTOSAVE_DELAY_MS" in source
    assert "scheduleSave" in source
    assert "saveConfiguration" in source
    assert "waitUntilSaved" in source
    assert "channel_auto_select" in source
    assert "channel5.disabled" in source
    assert "channel6.disabled" in source
    assert "bridge_mode" in source
    assert "bridge_name" in source
    assert "bridge_uplink" in source


def test_connection_quality_and_troubleshooting_remain_integrated() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "Connection Quality" in source
    assert "ensureConnectionQuality" in source
    assert "View detailed charts and client measurements" in source
    assert "ensureTroubleshooting" in source
    assert "System Health & Diagnostic Checks" in source
    assert "Runtime Details, Logs & Support" in source
    assert "tab-troubleshooting" in source


def test_real_dom_mode_transition_regressions_are_wired_into_ci() -> None:
    synthetic_source = DOM_TEST.read_text(encoding="utf-8")
    full_source = FULL_DOM_TEST.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")

    assert "JSDOM" in synthetic_source
    assert "Basic and Pro transitions" in synthetic_source
    assert "for (let cycle = 0; cycle < 3" in synthetic_source
    assert "assertProLayout" in synthetic_source
    assert "assets/index.html" in full_source
    assert "assets/ui.js" in full_source
    assert "assets/field_visibility.js" in full_source
    assert "assets/basic_guided.js" in full_source
    assert "for (let cycle = 0; cycle < 3" in full_source
    assert "jsdom@24.1.3" in ci
    assert "--test-force-exit" in ci
    assert "tests/js/pro_guided_mode_transition.test.mjs" in ci
    assert "tests/js/pro_guided_full_portal_integration.test.mjs" in ci


@pytest.mark.parametrize("asset", [LOADER, SOURCE, SESSION, DOM_TEST, FULL_DOM_TEST])
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
