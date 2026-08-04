from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "assets" / "adapter_interaction_guard.js"
LOADER = ROOT / "assets" / "devhub_upload.js"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_guard_is_loaded_after_the_pro_workflow() -> None:
    source = LOADER.read_text(encoding="utf-8")

    workflow_index = source.index("/assets/pro_guided_workflow.js")
    guard_index = source.index("/assets/adapter_interaction_guard.js?v=148-focus-guard-1")

    assert guard_index > workflow_index


def test_guard_asset_is_served_by_the_daemon() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert (
        '"/assets/adapter_interaction_guard.js": '
        '"application/javascript; charset=utf-8"'
    ) in source


def test_guard_defers_polling_and_config_writes_while_select_is_active() -> None:
    source = GUARD.read_text(encoding="utf-8")

    assert "adapterInteractionActive" in source
    assert "document.activeElement === select" in source
    assert "pendingAdapterRefresh = true" in source
    assert "pendingConfiguredAdapter" in source
    assert "delete protectedConfig.ap_adapter" in source
    assert "focusin" in source
    assert "focusout" in source
    assert "flushPendingAdapterWork" in source
    assert "preserveUserChoice" in source


def test_guard_keeps_six_ghz_notice_contextual() -> None:
    source = GUARD.read_text(encoding="utf-8")

    assert "selectedBand() !== '6ghz'" in source
    assert "hint.hidden = true" in source
    assert "hint.style.display = 'none'" in source


def test_guard_parses_with_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    completed = subprocess.run(
        [node, "--check", str(GUARD)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
