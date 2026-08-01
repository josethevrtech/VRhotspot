from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "assets" / "field_visibility.js"


def test_developer_hub_exposes_one_click_managed_tools_controls():
    source = LOADER.read_text(encoding="utf-8")

    assert "devhubInstallTools" in source
    assert "devhubRemoveTools" in source
    assert "devhubAcceptToolsLicense" in source
    assert "/v1/devbridge/tools/install" in source
    assert "/v1/devbridge/tools/remove" in source
    assert "license_accepted: true" in source
    assert "https://developer.android.com/studio/terms" in source


def test_tools_install_requires_checked_license_control():
    source = LOADER.read_text(encoding="utf-8")

    assert "if (!accept.checked)" in source
    assert "Review and accept the Android SDK License Agreement first." in source


def test_field_visibility_and_tools_extension_parse_with_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    completed = subprocess.run(
        [node, "--check", str(LOADER)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
