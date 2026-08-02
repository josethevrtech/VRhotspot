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
    assert "is_wireless_bootstrap" in source


def test_normal_connection_tab_is_replaced_by_guided_workflow() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Set up wireless headset" in source
    assert "Connect the headset by USB" in source
    assert "Approve USB debugging inside the headset" in source
    assert "Enable Wireless ADB" in source
    assert "connectionTab.remove()" in source
    assert "connectionPanel.remove()" in source
    assert "mergeDiscoveryIntoDevices" in source
    assert "moveManualConnectionToTools" in source
    assert "Advanced ADB" in source
    assert "Manual pairing and IP connection" in source


def test_wizard_preserves_privacy_and_internal_serial_state() -> None:
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "devhub-sensitive-identifier" in source
    assert "dataset.devhubSerial" in source
    assert "serial: String(device.serial || '')" in source
    assert "target" in source


def test_wizard_css_is_modal_responsive_and_has_no_status_lights() -> None:
    source = WIZARD_CSS.read_text(encoding="utf-8")

    assert ".devhub-wizard-overlay" in source
    assert ".devhub-wizard-dialog" in source
    assert ".devhub-wizard-step.current" in source
    assert "@media (max-width: 620px)" in source
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
