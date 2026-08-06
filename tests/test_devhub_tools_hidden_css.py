"""Effective hidden-state CSS checks for the Developer Hub Tools panel.

The Tools panel toggles controls with the DOM ``hidden`` property. The
user-agent rule ``[hidden] { display: none }`` loses to author rules such as
``.btn { display: inline-flex }`` and ``.devhub-checks { display: flex }``,
which once left "Install Managed ADB" and the license row visible in the
system-ADB state. These tests assert the *computed* display of hidden
controls under the real stylesheet cascade, not merely that JavaScript
assigned ``.hidden``.

The browser test renders a fixture that mirrors the Tools panel markup from
``field_visibility.js`` with the real stylesheets in their real load order,
using a headless Chromium-family browser (``VRHOTSPOT_BROWSER_EXECUTABLE``
overrides autodetection). It is skipped when no such browser is installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

BROWSER_CANDIDATES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
)

# Mirrors updateTools() in assets/devhub_connection_wizard.js.
TOOLS_STATES = {
    "system": {"install": True, "license": True, "remove": False},
    "missing": {"install": False, "license": False, "remove": True},
    "managed": {"install": False, "license": False, "remove": False},
    "broken": {"install": False, "license": False, "remove": False},
}


def _browser_executable() -> str | None:
    override = os.environ.get("VRHOTSPOT_BROWSER_EXECUTABLE", "")
    if override:
        return override if Path(override).exists() else None
    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _rule_block(css: str, selector: str) -> str:
    match = re.search(
        r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css
    )
    assert match is not None, f"missing rule for {selector}"
    return match.group(1)


def test_hidden_overrides_exist_for_display_styled_controls() -> None:
    ui_css = (ASSETS / "ui.css").read_text(encoding="utf-8")
    devhub_css = (ASSETS / "devhub.css").read_text(encoding="utf-8")

    assert "display: inline-flex" in _rule_block(ui_css, ".btn")
    assert "display: none !important" in _rule_block(ui_css, ".btn[hidden]")
    assert "display: flex" in _rule_block(devhub_css, ".devhub-checks")
    assert "display: none !important" in _rule_block(
        devhub_css, ".devhub-checks[hidden]"
    )


def _fixture_html() -> str:
    stylesheets = "\n".join(
        f'<link rel="stylesheet" href="{(ASSETS / name).as_uri()}" />'
        for name in ("ui.css", "devhub.css", "devhub_connection_wizard.css")
    )
    states = json.dumps(TOOLS_STATES)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8" />{stylesheets}</head>
<body>
<div id="tab-devhub">
  <div class="devhub-tools-grid">
    <div id="devhubToolsManagement" class="devhub-span">
      <div class="devhub-checks mt-12">
        <label><input id="devhubAcceptToolsLicense" type="checkbox" />
        <span>I accept the Android SDK License Agreement</span></label>
      </div>
      <div class="devhub-actions mt-12">
        <button id="devhubInstallTools" type="button" class="btn primary">Install Managed ADB</button>
        <button id="devhubRemoveTools" type="button" class="btn secondary">Remove Managed ADB</button>
      </div>
    </div>
  </div>
</div>
<pre id="hiddenStateResults"></pre>
<script>
  const states = {states};
  const install = document.getElementById('devhubInstallTools');
  const remove = document.getElementById('devhubRemoveTools');
  const license = document.getElementById('devhubAcceptToolsLicense')
    .closest('.devhub-checks');
  const results = {{}};
  for (const [name, hidden] of Object.entries(states)) {{
    install.hidden = hidden.install;
    license.hidden = hidden.license;
    remove.hidden = hidden.remove;
    results[name] = {{
      install: getComputedStyle(install).display,
      license: getComputedStyle(license).display,
      remove: getComputedStyle(remove).display,
    }};
  }}
  document.getElementById('hiddenStateResults').textContent =
    JSON.stringify(results);
</script>
</body></html>
"""


def _rendered_displays(tmp_path: Path, browser: str) -> dict:
    page = tmp_path / "tools_hidden_state.html"
    page.write_text(_fixture_html(), encoding="utf-8")
    completed = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={tmp_path / 'chrome-profile'}",
            "--virtual-time-budget=2000",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    match = re.search(
        r'id="hiddenStateResults">([^<]+)<', completed.stdout
    )
    assert match is not None, "fixture page did not report computed styles"
    return json.loads(match.group(1))


@pytest.mark.skipif(
    _browser_executable() is None,
    reason="no Chromium-family browser is installed",
)
def test_tools_controls_effective_display_per_state(tmp_path: Path) -> None:
    results = _rendered_displays(tmp_path, _browser_executable())

    # Visible buttons compute to "flex": the .btn inline-flex display is
    # blockified because .devhub-actions is a flex container. A missing
    # stylesheet would yield "block" (blockified UA inline-block) instead.
    system = results["system"]
    assert system["install"] == "none"
    assert system["license"] == "none"
    assert system["remove"] == "flex"

    missing = results["missing"]
    assert missing["install"] == "flex"
    assert missing["license"] == "flex"
    assert missing["remove"] == "none"

    for state in ("managed", "broken"):
        assert results[state]["install"] == "flex"
        assert results[state]["license"] == "flex"
        assert results[state]["remove"] == "flex"
