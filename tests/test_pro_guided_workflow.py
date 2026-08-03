from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "devhub_upload.js"


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

    assert ".pro-configuration > .settings-header" in source
    assert "display: none !important" in source
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


def test_connection_quality_is_pro_only_and_not_a_sidebar_tab() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "Connection Quality" in source
    assert "buildConnectionQuality" in source
    assert '.nav-item[data-tab="telemetry"]' in source
    assert "?.remove()" in source
    assert "#tab-telemetry { display: none !important; }" in source
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


def test_rewritten_extension_parses_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    completed = subprocess.run(
        [node, "--check", str(SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
