from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WIZARD_JS = ASSETS / "devhub_connection_wizard.js"
WIZARD_CSS = ASSETS / "devhub_connection_wizard.css"
FIELD_VISIBILITY = ASSETS / "field_visibility.js"
DEVHUB_API = ROOT / "backend/vr_hotspotd/devtools/devhub_api.py"


def test_connection_wizard_assets_load_after_identity_layer() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")

    assert "/assets/devhub_connection_wizard.css" in loader
    assert "/assets/devhub_connection_wizard.js" in loader
    assert loader.index("/assets/devhub_connection_wizard.css") > loader.index(
        "/assets/devhub_device_identity.css"
    )
    assert loader.index("/assets/devhub_connection_wizard.js") > loader.index(
        "/assets/devhub_device_identity.js"
    )


def test_connection_wizard_assets_and_endpoint_are_served() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert (
        '"/assets/devhub_connection_wizard.css": "text/css; charset=utf-8"'
        in source
    )
    assert (
        '"/assets/devhub_connection_wizard.js": '
        '"application/javascript; charset=utf-8"'
        in source
    )
    assert 'WIRELESS_BOOTSTRAP_PATH = "/v1/devbridge/adb/enable-wireless"' in source
    assert "enable_wireless_adb" in source


def test_hotspot_precondition_is_outside_five_step_wizard() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Start VRhotspot first" in source
    assert "Developer Hub will never enable wireless ADB through another Wi-Fi network." in source
    assert "startHotspot(null, 'Developer Hub')" in source
    assert "waitForRunning(30000)" in source
    assert "Open Hotspot Setup" in source
    assert "Connect USB" in source
    assert "Approve debugging" in source
    assert "Join VRhotspot" in source
    assert "Enable wireless" in source
    assert "Complete" in source


def test_wireless_request_contains_only_usb_serial_and_port() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    request = source[source.index("body: JSON.stringify({"):source.index(
        "body: JSON.stringify({"
    ) + 180]
    assert "serial:" in request
    assert "port: 5555" in request
    assert "passphrase" not in request
    assert "ssid" not in request


def test_manual_pairing_and_ip_forms_are_removed_from_workspace() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "const pairing = card(connectionPanel, 'Wireless Pairing')" in source
    assert "pairing.remove()" in source
    assert "connect.remove()" in source
    assert "connectionPanel.remove()" in source
    assert "Advanced ADB" not in source
    assert "Manual pairing and IP connection" not in source
    assert "Connect by IP" not in source


def test_apps_ui_removes_daemon_path_and_editable_serial() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "function cleanApps()" in source
    assert "pathInput.required = false" in source
    assert "container?.remove()" in source
    assert "serial.hidden = true" in source
    assert "APK file upload is temporarily unavailable in the desktop companion" in source
    assert "Open the browser Web Portal to deploy an APK" in source


def test_tools_actions_follow_structured_ownership_state() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    for label in (
        "Install Managed ADB",
        "Reinstall Managed ADB",
        "Repair Managed ADB",
        "Uninstall System ADB",
        "System ADB ready",
        "Managed ADB ready",
        "Managed ADB needs repair",
    ):
        assert label in source
    assert "const remove = bindToolsRemoveButton()" in source
    assert "model.source === 'system'" in source
    assert "remove.dataset.toolsSource = 'system'" in source
    assert "window.confirm" in source
    assert "model.managed.verified === false" in source


def test_raw_healthy_adb_state_is_hidden() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "if (raw === 'device')" in source
    assert "node.hidden = true" in source
    assert "Approve USB debugging" in source
    assert "Recovery mode" in source


def test_wizard_css_is_modal_responsive_and_five_stage() -> None:
    source = WIZARD_CSS.read_text(encoding="utf-8")

    assert ".devhub-wizard-overlay" in source
    assert ".devhub-wizard-dialog" in source
    assert ".devhub-wizard-step.current" in source
    assert "grid-template-columns: repeat(5" in source
    assert "@media (max-width: 620px)" in source
    assert 'body[data-ui-mode="advanced"] .devhub-precondition-dialog' in source
    assert "min-height: 0;" in source
    assert "font-size: 18px;" in source
    assert "status-dot" not in source


def test_connection_wizard_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    subprocess.run(
        [node, "--check", str(WIZARD_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
