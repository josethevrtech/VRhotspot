from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "assets" / "field_visibility.js"
WORKSPACE_JS = ROOT / "assets" / "devhub_workspace.js"
WORKSPACE_CSS = ROOT / "assets" / "devhub_workspace.css"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_workspace_assets_are_loaded_after_core_developer_hub_assets():
    source = LOADER.read_text(encoding="utf-8")

    assert "'/assets/devhub_workspace.css'" in source
    assert "'/assets/devhub_workspace.js'" in source
    assert source.index("'/assets/devhub_upload.js'") < source.index(
        "'/assets/devhub_workspace.js'"
    )


def test_workspace_assets_are_served_by_developer_hub_handler():
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert '"/assets/devhub_workspace.css": "text/css; charset=utf-8"' in source
    assert (
        '"/assets/devhub_workspace.js": "application/javascript; charset=utf-8"'
        in source
    )


def test_workspace_is_device_first_and_removes_manual_refresh_ui():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "devhub-device-bar" in source
    assert "No headset selected" in source
    assert "refreshButton.hidden = true" in source
    assert "window.setInterval(requestRefresh, AUTO_REFRESH_MS)" in source
    assert "document.addEventListener('visibilitychange'" in source
    assert "window.addEventListener('focus', requestRefresh)" in source


def test_workspace_groups_existing_features_into_clear_views():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    for view in ("device", "apps", "connection", "tools"):
        assert f"workspaceTab('{view}'" in source
        assert f"panel('{view}')" in source

    assert "Install APK" in source
    assert "Manage Apps" in source
    assert "Connection" in source
    assert "Developer Tools" in source
    assert "cards.apk" in source
    assert "cards.installed" in source
    assert "cards.control" in source


def test_workspace_uses_shared_info_tips_instead_of_visible_subtext():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "function infoTip(" in source
    assert "tip.className = ['tip', 'devhub-info-tip'" in source
    assert "tip.textContent = 'ⓘ'" in source
    assert "tip.setAttribute('data-tip', text)" in source
    assert "devhub-quick-action-tip" in source
    assert "devhub-device-help" in source
    assert "devhub-refresh-help" in source
    assert "copy.textContent = detail" not in source
    assert "refreshNote.textContent" not in source


def test_workspace_has_no_decorative_device_status_light():
    javascript = WORKSPACE_JS.read_text(encoding="utf-8")
    stylesheet = WORKSPACE_CSS.read_text(encoding="utf-8")

    assert "devhubWorkspaceDeviceState" not in javascript
    assert "devhub-device-state" not in javascript
    assert ".devhub-device-state" not in stylesheet


def test_workspace_handles_empty_device_labels_without_fake_usb_status():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "EMPTY_DEVICE_LABELS" in source
    assert "'no device selected'" in source
    assert "normalizedSerial" in source
    assert "badges.hidden = !device.serial" in source


def test_workspace_suppresses_polling_noise_but_preserves_operation_feedback():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "value.startsWith('Ready.')" in source
    assert "value.startsWith('Refreshing Developer Hub state')" in source
    assert "activity.classList.add('visible')" in source
    assert "state === 'success'" in source


def test_workspace_observes_source_fields_without_observing_its_own_summary():
    source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "observeSources(feedback)" in source
    assert "el('devhubSelectedDevice')" in source
    assert "el('devhubDeviceList')" in source
    assert "el('devhubToolSource')" in source
    assert "observer.observe(page" not in source


def test_workspace_styles_are_responsive_and_compact():
    source = WORKSPACE_CSS.read_text(encoding="utf-8")

    assert ".devhub-page" in source
    assert "max-width: none" in source
    assert ".devhub-device-bar" in source
    assert ".devhub-workspace-tabs" in source
    assert ".devhub-quick-action-button" in source
    assert ".devhub-info-tip" in source
    assert ".devhub-workspace-panel[hidden]" in source
    assert "@media (max-width: 920px)" in source


@pytest.mark.parametrize("asset", [LOADER, WORKSPACE_JS])
def test_workspace_javascript_parses_with_node(asset):
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
