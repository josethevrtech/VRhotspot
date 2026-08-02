from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
IDENTITY_JS = ASSETS / "devhub_device_identity.js"
IDENTITY_CSS = ASSETS / "devhub_device_identity.css"
FIELD_VISIBILITY = ASSETS / "field_visibility.js"
DEVHUB_API = ROOT / "backend/vr_hotspotd/devtools/devhub_api.py"
INDEX = ASSETS / "index.html"


def test_identity_assets_are_loaded_after_device_overview() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")

    assert "/assets/devhub_device_identity.css" in loader
    assert "/assets/devhub_device_identity.js" in loader
    assert loader.index("/assets/devhub_device_identity.css") > loader.index(
        "/assets/devhub_device_overview.css"
    )
    assert loader.index("/assets/devhub_device_identity.js") > loader.index(
        "/assets/devhub_device_overview.js"
    )


def test_identity_assets_are_served_by_devhub_handler() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert '"/assets/devhub_device_identity.css": "text/css; charset=utf-8"' in source
    assert (
        '"/assets/devhub_device_identity.js": '
        '"application/javascript; charset=utf-8"'
    ) in source


def test_model_first_identity_and_privacy_contract() -> None:
    source = IDENTITY_JS.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")

    assert 'el("privacyMode")' in source or "el('privacyMode')" in source
    assert 'id="privacyMode"' in index
    assert "modelFromMeta" in source
    assert "devhubSelectedDeviceName" in source
    assert "devhubWorkspaceDeviceSerial" in source
    assert "devhubOverviewIp" in source
    assert "Hidden by Privacy Mode" in source
    assert "devhub-sensitive-identifier" in source
    assert "devhub-device-model-source" in source


def test_privacy_css_hides_sensitive_identifiers_only_when_enabled() -> None:
    source = IDENTITY_CSS.read_text(encoding="utf-8")

    assert ".devhub-privacy-active .devhub-sensitive-identifier" in source
    assert "display: none !important" in source
    assert ".devhub-device-model-source" in source


def test_identity_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    subprocess.run(
        [node, "--check", str(IDENTITY_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
