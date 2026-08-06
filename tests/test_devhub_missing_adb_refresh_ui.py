"""Missing-ADB refresh regression: no repeating tools_unavailable banner.

After system ADB is intentionally removed, the Tools card must keep its
actionable missing state (red light, license checkbox, Install Managed ADB)
while the five-second automatic refresh renders an empty device inventory
without raising `ADB device refresh failed: tools_unavailable` on every tick.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEVHUB_JS = ROOT / "assets" / "devhub.js"
DOM_TEST = ROOT / "tests" / "js" / "devhub_missing_adb_refresh.test.mjs"


def _node_with_jsdom():
    node = shutil.which("node")
    if node is None:
        return None
    probe = subprocess.run(
        [node, "--input-type=module", "-e", "import 'jsdom';"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    return node if probe.returncode == 0 else None


def test_tools_status_is_authoritative_for_skipping_adb_probes():
    source = DEVHUB_JS.read_text(encoding="utf-8")

    assert "const adbKnownMissing = toolsResponse.ok && !!adb && !adb.path" in source
    assert "adbKnownMissing\n        ? [null, null]" in source
    assert "devicesResponse && !devicesResponse.ok" in source
    assert "devicesResponse && devicesResponse.ok ? nestedData(devicesResponse) : {}" in source
    # Candidate discovery stays independent of the ADB runtime probes.
    assert "request(PATHS.networkDevices)" in source


def test_missing_adb_refresh_keeps_error_reporting_for_real_failures():
    source = DEVHUB_JS.read_text(encoding="utf-8")

    assert "Developer tools status failed" in source
    assert "ADB device refresh failed" in source
    assert "Unable to refresh Developer Hub. Check the daemon connection." in source


def test_missing_adb_dom_regression_covers_required_scenarios():
    source = DOM_TEST.read_text(encoding="utf-8")

    assert "missing ADB refresh renders empty inventory without an error banner" in source
    assert "available ADB still refreshes version and device inventory" in source
    assert "unexpected device failure while tools are available still surfaces an error" in source
    assert "repeated automatic refreshes never surface the missing-ADB activity banner" in source
    assert "devhubInstallTools" in source
    assert "devhubAcceptToolsLicense" in source
    assert "tools_unavailable" in source


@pytest.mark.parametrize("asset", [DEVHUB_JS, DOM_TEST])
def test_missing_adb_refresh_sources_parse_with_node(asset):
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


def test_missing_adb_dom_regression_passes_under_node():
    node = _node_with_jsdom()
    if node is None:
        pytest.skip("node with jsdom is not available")

    completed = subprocess.run(
        [node, "--test", "--test-force-exit", str(DOM_TEST)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
