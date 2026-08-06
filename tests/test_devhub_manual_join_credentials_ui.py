"""Privacy-aware hotspot credential assistance in the manual-join wizard.

Step 3 of the wireless wizard tells the user to open Settings → Wi-Fi →
VR-Hotspot on the headset, so the wizard offers the hotspot Wi-Fi password
next to those instructions. Privacy Mode governs the panel: with privacy on
the secret is never fetched or placed in the DOM/JS state unless the user
explicitly reveals it, and every wizard exit purges it.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WIZARD_JS = ROOT / "assets" / "devhub_connection_wizard.js"
WIZARD_CSS = ROOT / "assets" / "devhub_connection_wizard.css"
DOM_TEST = ROOT / "tests" / "js" / "devhub_manual_join_credentials.test.mjs"


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


def test_credential_panel_labels_the_secret_and_offers_controls():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Hotspot Wi-Fi credentials" in source
    assert "Hotspot Wi-Fi password" in source
    assert "devhubManualCredSsid" in source
    assert "devhubManualCredSecret" in source
    assert "devhubManualCredToggle" in source
    assert "devhubManualCredCopy" in source
    assert "Copy password" in source


def test_privacy_mode_branch_uses_required_copy_and_actions():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "Password hidden by Privacy Mode." in source
    assert (
        "Privacy Mode hides passwords and sensitive information so they are "
        "not exposed while screen sharing, streaming, or recording." in source
    )
    assert "Show password once" in source
    assert "Turn off Privacy Mode" in source
    assert "Keep hidden" in source
    # Show-once requires explicit confirmation and never flips the global
    # setting; turn-off updates the existing synchronized controls.
    assert "devhubManualCredConfirmShow" in source
    assert "setGlobalPrivacy(false)" in source
    assert "el('privacyMode') || el('privacyModeBasic')" in source
    assert "vr_hotspot_privacy" in source


def test_secret_fetch_is_explicit_and_privacy_gated():
    source = WIZARD_JS.read_text(encoding="utf-8")

    assert "credentials: '/v1/config/hotspot-credentials'," in source
    # Fetch refuses to run while Privacy Mode hides the secret.
    assert "if (privacyEnabled() && !cred.sessionReveal) return;" in source
    # Auto-fetch happens only while the wizard is open on the manual step and
    # only until a value/error exists, so polling never re-requests it.
    assert (
        "if (wizardOpen && state.manualJoin && !cred.value && !cred.fetching "
        "&& !cred.error && cred.auto) {" in source
    )


def test_secret_lifecycle_purges_on_exit_privacy_and_password_change():
    source = WIZARD_JS.read_text(encoding="utf-8")

    close = source[source.index("function closeWizard()"):]
    close = close[:close.index("}\nfunction")]
    assert "purgeCredentialSecret()" in close

    open_fn = source[source.index("function openWizard()"):]
    open_fn = open_fn[:open_fn.index("}\nfunction")]
    assert "purgeCredentialSecret()" in open_fn

    purge = source[source.index("function purgeCredentialSecret("):]
    purge = purge[:purge.index("}\nasync function")]
    assert "cred.value = null;" in purge
    assert "secret.textContent = '';" in purge

    assert "function onPrivacyModeChanged()" in source
    assert "if (privacyEnabled()) purgeCredentialSecret();" in source
    assert "function onSavedPassphraseEdited()" in source
    assert "purgeCredentialSecret({ auto: false });" in source


def test_credential_css_keeps_monospace_and_stable_width():
    source = WIZARD_CSS.read_text(encoding="utf-8")

    assert ".devhub-manual-cred" in source
    secret_block = source[source.index(".devhub-manual-cred-value"):]
    secret_block = secret_block[:secret_block.index("}")]
    assert "font-family: var(--font-mono);" in secret_block
    assert "overflow-wrap: anywhere;" in secret_block
    assert ".devhub-manual-cred-privacy[hidden]" in source
    assert ".devhub-manual-cred-actions[hidden]" in source
    assert ".devhub-manual-cred-row[hidden]" in source


def test_credentials_dom_regression_covers_required_scenarios():
    source = DOM_TEST.read_text(encoding="utf-8")

    assert "privacy off renders credentials" in source
    assert "privacy on never requests the password" in source
    assert "show password once confirms, fetches a single time" in source
    assert "turn off Privacy Mode synchronizes both controls" in source
    assert "turning Privacy Mode back on immediately purges" in source
    assert "closing the wizard with Escape purges the secret" in source
    assert "cancel button purges the secret" in source
    assert "keep hidden leaves the password unfetched" in source
    assert "editing the saved hotspot password purges the stale value" in source
    assert "nothing copied automatically" in source
    assert "background polling never re-requests the secret" in source


@pytest.mark.parametrize("asset", [WIZARD_JS, DOM_TEST])
def test_credential_sources_parse_with_node(asset):
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


def test_credentials_dom_regression_passes_under_node():
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
