from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEVHUB_JS = ROOT / "assets" / "devhub.js"
DEVHUB_CSS = ROOT / "assets" / "devhub.css"
FIELD_VISIBILITY_JS = ROOT / "assets" / "field_visibility.js"


def test_developer_hub_assets_are_loaded_by_existing_web_shell():
    loader = FIELD_VISIBILITY_JS.read_text(encoding="utf-8")

    assert "'/assets/devhub.css'" in loader
    assert "'/assets/devhub.js'" in loader
    assert "document.head.appendChild(stylesheet)" in loader
    assert "document.head.appendChild(script)" in loader


def test_developer_hub_console_exposes_complete_first_workflow():
    source = DEVHUB_JS.read_text(encoding="utf-8")

    required_paths = (
        "/v1/devbridge/tools/status",
        "/v1/devbridge/devices",
        "/v1/devbridge/adb/version",
        "/v1/devbridge/adb/devices",
        "/v1/devbridge/adb/pair",
        "/v1/devbridge/adb/connect",
        "/v1/devbridge/adb/disconnect",
        "/v1/devbridge/adb/packages",
        "/v1/devbridge/adb/install",
        "/v1/devbridge/adb/launch",
        "/v1/devbridge/adb/stop",
        "/v1/devbridge/adb/clear-data",
        "/v1/devbridge/adb/uninstall",
    )
    for path in required_paths:
        assert path in source

    required_controls = (
        'id="devhubPairForm"',
        'id="devhubConnectForm"',
        'id="devhubInstallForm"',
        'id="devhubDeviceList"',
        'id="devhubPackageList"',
        'id="devhubLaunch"',
        'id="devhubStop"',
        'id="devhubClearData"',
        'id="devhubUninstall"',
    )
    for control in required_controls:
        assert control in source


def test_developer_hub_pairing_code_is_not_persisted():
    source = DEVHUB_JS.read_text(encoding="utf-8")

    assert "pairing_code: code.value.trim()" in source
    assert "code.value = ''" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source


def test_developer_hub_has_no_free_form_adb_command_surface():
    source = DEVHUB_JS.read_text(encoding="utf-8")

    assert "eval(" not in source
    assert "new Function" not in source
    assert "adb command" not in source.lower()
    assert "shell:" not in source


def test_developer_hub_css_contains_responsive_console_layout():
    source = DEVHUB_CSS.read_text(encoding="utf-8")

    assert ".devhub-page" in source
    assert ".devhub-grid" in source
    assert ".devhub-list-item" in source
    assert "@media (max-width: 920px)" in source


def test_developer_hub_javascript_parses_with_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    completed = subprocess.run(
        [node, "--check", str(DEVHUB_JS)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
