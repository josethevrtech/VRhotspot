from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "assets" / "field_visibility.js"
UPLOAD_JS = ROOT / "assets" / "devhub_upload.js"
DEVHUB_CSS = ROOT / "assets" / "devhub.css"
UPLOAD_CSS = ROOT / "assets" / "devhub_upload.css"


def test_developer_hub_loads_apk_picker_assets():
    source = LOADER.read_text(encoding="utf-8")

    assert "'/assets/devhub_upload.css'" in source
    assert "'/assets/devhub_upload.js'" in source
    assert "document.head.appendChild(uploadStylesheet)" in source
    assert "document.head.appendChild(uploadScript)" in source


def test_developer_hub_exposes_browser_apk_picker():
    source = UPLOAD_JS.read_text(encoding="utf-8")

    assert "devhubApkPicker" in source
    assert "devhubApkFile" in source
    assert "Choose APK" in source
    assert "Install or update app" in source
    assert "/v1/devbridge/adb/install-upload" in source
    assert "application/vnd.android.package-archive" in source
    assert "X-VRhotspot-Serial" in source
    assert "X-VRhotspot-Apk-Name" in source
    assert "body: file" in source


def test_upload_always_allows_update_and_reports_detected_action():
    source = UPLOAD_JS.read_text(encoding="utf-8")

    assert "'X-VRhotspot-Reinstall': '1'" in source
    assert "deploymentData" in source
    assert "deployment_action" in source
    assert "deploymentVerb" in source
    assert "Updated" in source
    assert "Installed or updated" in source
    assert "selectedHeadsetLabel" in source
    assert "on ${headset}" in source


def test_host_path_install_is_preserved_as_advanced_fallback():
    source = UPLOAD_JS.read_text(encoding="utf-8")

    assert "Advanced: install from a path on the daemon host" in source
    assert "Use this only when the APK already exists on the Linux machine running VRhotspot." in source
    assert "pathInput.required = false" in source


def test_picker_is_inserted_before_host_path_moves_into_advanced_section():
    source = UPLOAD_JS.read_text(encoding="utf-8")

    insert_picker = source.index("form.insertBefore(uploadField, pathField)")
    move_host_path = source.index("advanced.append(summary, pathField, help)")
    insert_advanced = source.index("form.insertBefore(advanced, checks)")

    assert insert_picker < move_host_path < insert_advanced


def test_developer_hub_desktop_layout_uses_available_space():
    source = DEVHUB_CSS.read_text(encoding="utf-8")
    upload_source = UPLOAD_CSS.read_text(encoding="utf-8")

    assert "max-width: 1680px" in source
    assert "margin: 0;" in source
    assert ".devhub-heading-copy" in source
    assert "display: none;" in source
    assert ".devhub-file-drop" in upload_source
    assert "@media (max-width: 920px)" in upload_source


@pytest.mark.parametrize("asset", [LOADER, UPLOAD_JS])
def test_developer_hub_extensions_parse_with_node(asset):
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
