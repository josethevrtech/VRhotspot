"""Developer Hub wireless wizard regression: automatic-first enrollment.

Automatic Quest Wi-Fi enrollment is the primary path: while the daemon runs
`cmd wifi connect-network` and verifies the network, Step 3 shows a live
"Connecting … automatically" state with an animated indicator and no manual
credential instructions. Only after both automatic attempts fail
(`requires_manual_join=true`) does the wizard fall back to the explicit,
non-frozen manual Step 3 waiting state (heading, SSID, Settings → Wi-Fi
instructions, spinner), keep polling without overlapping requests or
repeated global banners, pause on USB disconnect, and stop every timer on
cancel.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WIZARD_JS = ROOT / "assets" / "devhub_connection_wizard.js"
WIZARD_CSS = ROOT / "assets" / "devhub_connection_wizard.css"
DOM_TEST = ROOT / "tests" / "js" / "devhub_manual_join_wizard.test.mjs"


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


def test_manual_join_state_shows_instructions_and_waiting_indicator():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Put on your headset and join ${ssid}" in source
    assert "Automatic Wi-Fi enrollment did not complete" in source
    assert "Settings → Wi-Fi" in source
    assert "Keep the USB cable connected" in source
    assert "devhubManualJoinSsid" in source
    assert "devhubManualJoinStatus" in source
    assert "devhub-manual-join-spinner" in source
    assert "Waiting for ${modelOf(device)} to join ${ssid}…" in source
    assert "I’ve joined — check again" in source


def test_automatic_enrollment_state_is_visible_and_credential_free():
    source = WIZARD_JS.read_text(encoding="utf-8")

    # Step 3 shows a live automatic-connection state with an animated
    # indicator while the daemon enrolls the headset.
    assert "state.autoJoining = true;" in source
    assert "Connecting ${modelOf(device)} to ${ssid} automatically…" in source
    assert "Keep the headset awake and the USB cable connected." in source
    assert "devhubAutoJoin" in source
    assert "devhub-auto-join-spinner" in source
    # The browser never sends the hotspot password; it only opts background
    # manual-join polls out of re-running automatic enrollment.
    assert "auto_join: !state.manualJoin," in source
    # A USB drop aborts enrollment cleanly and asks for the cable back.
    assert "usb_disconnected" in source
    assert "state.usbLost = true;" in source
    assert "Reconnect the USB cable to the headset. Automatic Wi-Fi setup stopped " in source


def test_manual_join_polling_is_single_flight_and_quiet():
    source = WIZARD_JS.read_text(encoding="utf-8")

    # Overlap guard: a new bootstrap request never starts while one runs.
    assert "if (state.busy || state.finishing) return;" in source
    # The join poll timer is armed only once.
    assert "if (!state.joinTimer) startManualPolling();" in source
    # Background polls update in-dialog copy instead of raising banners.
    assert "if (!alreadyManual) {" in source
    assert "state.manualNote = 'Unable to reach VRhotspot. Retrying automatically…';" in source
    assert "The last check did not complete" in source


def test_manual_join_success_advances_through_step_four_to_five():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "state.finishing = true;" in source
    assert "Enabling wireless ADB through the VRhotspot network…" in source
    assert "state.advanceTimer = window.setTimeout(" in source
    assert "is connected through VRhotspot" in source


def test_usb_disconnect_and_cancel_stop_manual_join_polling():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "USB cable disconnected" in source
    assert "Reconnect the USB cable to the headset." in source
    # closeWizard tears down every timer, including the success advance.
    close = source[source.index("function closeWizard()"):]
    close = close[:close.index("}\nfunction")]
    assert "stopTimer('usbTimer')" in close
    assert "stopTimer('joinTimer')" in close
    assert "stopAdvance()" in close


def test_manual_join_request_never_carries_hotspot_secrets():
    """The wizard displays the hotspot password on request, but the secret
    must stay out of the enable-wireless request, storage, logs, and any
    non-dedicated endpoint."""
    source = WIZARD_JS.read_text(encoding="utf-8")

    # The enable-wireless request body carries only the USB serial and port.
    assert "serial: String(device.serial || '')," in source
    assert "port: 5555," in source
    # The secret comes only from the dedicated authenticated endpoint.
    assert "'/v1/config/hotspot-credentials'" in source
    assert "include_secrets" not in source
    assert "reveal_passphrase" not in source
    # The secret is never persisted or logged: the only storage writes are
    # the privacy flag and the workspace view.
    assert source.count("localStorage.setItem") == 1
    assert "localStorage.setItem('vr_hotspot_privacy'" in source
    assert source.count("sessionStorage.setItem") == 1
    assert "sessionStorage.setItem('vrhs_devhub_workspace_view'" in source
    assert "console." not in source
    # Lifecycle: the secret is purged on open, close, and manual-join exit.
    assert source.count("purgeCredentialSecret()") >= 5


def test_manual_join_css_has_animated_waiting_indicator():
    source = WIZARD_CSS.read_text(encoding="utf-8")

    assert ".devhub-manual-join" in source
    assert ".devhub-manual-join-spinner" in source
    assert "@keyframes devhub-manual-join-spin" in source
    assert "prefers-reduced-motion" in source
    assert ".devhub-manual-join[hidden]" in source
    assert ".devhub-auto-join" in source
    assert ".devhub-auto-join-spinner" in source
    assert ".devhub-auto-join[hidden]" in source


def test_manual_join_dom_regression_covers_required_scenarios():
    source = DOM_TEST.read_text(encoding="utf-8")

    assert "wifi_control_unavailable" in source
    assert "requires_manual_join: true" in source
    assert "stage: 'join_vrhotspot'" in source
    assert "ssid: SSID" in source
    assert "'VR-Hotspot'" in source
    assert "gateway: '192.168.68.1'" in source
    assert "subnet: '192.168.68.0/24'" in source
    assert (
        "renders the Step 3 manual-join waiting state" in source
    )
    assert (
        "automatic polling repeats without overlap and without repeated error banners"
        in source
    )
    assert "manual recheck button issues an immediate request" in source
    assert "advances visibly to Step 4 and then Step 5" in source
    assert "USB disconnect during manual join stops polling" in source
    assert "cancelling the wizard stops every polling timer" in source
    assert (
        "automatic enrollment shows the Step 3 connecting state before any "
        "manual instructions" in source
    )
    assert "wireless request bodies carry only serial, port, and auto_join" in source
    assert (
        "usb_disconnected during automatic enrollment stops cleanly and asks "
        "to reconnect" in source
    )
    assert "automatic_join_attempted: true" in source
    assert "fallback_reason: 'verification_timeout'" in source
    assert "passphrase" not in source


@pytest.mark.parametrize("asset", [WIZARD_JS, DOM_TEST])
def test_manual_join_sources_parse_with_node(asset):
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


def test_manual_join_dom_regression_passes_under_node():
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
