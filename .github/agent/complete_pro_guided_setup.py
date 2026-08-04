from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


field_path = Path("assets/field_visibility.js")
field = field_path.read_text(encoding="utf-8")
field = replace_once(
    field,
    """    const statusCard = overview?.querySelector('.status-hero');
    const debugCard = overview?.querySelector('.debug-details');
    if (!overview || !settings || !logs || !statusCard) return false;
""",
    """    const statusCard = overview?.querySelector('.status-hero');
    const debugCard = overview?.querySelector('.debug-details');
    const readinessCard = overview?.querySelector('.adapter-readiness-card');
    if (!overview || !settings || !logs || !statusCard) return false;
""",
    "preserve adapter readiness reference",
)
field = replace_once(
    field,
    """    shell.append(statusCard, configuration);
    overview.replaceChildren(shell);
""",
    """    shell.append(statusCard, configuration);
    if (readinessCard) shell.appendChild(readinessCard);
    overview.replaceChildren(shell);
""",
    "preserve adapter readiness card",
)
field_path.write_text(field, encoding="utf-8")

source_path = Path("assets/pro_guided_workflow.js")
source = source_path.read_text(encoding="utf-8")
source = replace_once(
    source,
    """      'ssid',
      'wpa2_passphrase',
      'enable_internet',
""",
    """      'ssid',
      'wpa2_passphrase',
      'band_preference',
      'ap_security',
      'country',
      'enable_internet',
""",
    "require complete connection fields",
)
source = replace_once(
    source,
    """      step(3, 'Configure hotspot', 'Set the network name, password, and internet-sharing preference.', 'proStepHotspot'),
      step(4, 'Fine-tune hotspot', 'Optional expert settings are grouped by purpose and can be left at their recommended defaults.', 'proStepAdvanced'),
""",
    """      step(3, 'Configure hotspot', 'Set the network name, password, band, security, country, and internet-sharing behavior.', 'proStepHotspot'),
      step(4, 'Fine-tune hotspot', 'Review detailed wireless, network, and system options or leave the recommended defaults unchanged.', 'proStepAdvanced'),
""",
    "expand step descriptions",
)
source = replace_once(
    source,
    """    const configuration = el('proHotspotConfiguration');
    const serviceCard = overview.querySelector('.pro-service-card');
""",
    """    const configuration = el('proHotspotConfiguration');
    const serviceCard = overview.querySelector('.pro-service-card');
    const readinessCard = overview.querySelector('.adapter-readiness-card');
""",
    "capture readiness card",
)
source = replace_once(
    source,
    """    el('proStepAdapter').appendChild(adapter);
""",
    """    const adapterStep = el('proStepAdapter');
    adapterStep.appendChild(adapter);
    if (readinessCard) {
      readinessCard.classList.add('pro-adapter-readiness');
      const readinessHeading = readinessCard.querySelector('.card-header h2');
      if (readinessHeading) readinessHeading.textContent = 'Adapter readiness';
      adapterStep.appendChild(readinessCard);
    }
""",
    "embed readiness in step one",
)
source = replace_once(
    source,
    """    for (const key of ['ssid', 'wpa2_passphrase', 'enable_internet']) {
""",
    """    for (const key of [
      'ssid',
      'wpa2_passphrase',
      'band_preference',
      'ap_security',
      'country',
      'enable_internet',
    ]) {
""",
    "move complete connection fields into step three",
)
source = replace_once(
    source,
    """    const advanced = make('details', 'pro-advanced-settings');
    const advancedSummary = make('summary', '', 'Advanced wireless, network, and system settings');
    const advancedBody = make('div', 'pro-advanced-body');
    configuration.querySelectorAll('.pro-config-details').forEach((details) => advancedBody.appendChild(details));
    advanced.append(advancedSummary, advancedBody);
    el('proStepAdvanced').appendChild(advanced);
""",
    """    const advancedGroups = make('div', 'pro-guided-advanced-groups');
    const groupCopy = {
      Wireless: 'Channels, width, radio timing, transmit power, automatic selection, and fallback behavior.',
      Network: 'Gateway, DHCP, DNS, NAT acceleration, bridge mode, and firewall integration.',
      'System & Performance': 'Startup behavior, interface strategy, power management, CPU tuning, kernel tuning, and debug logging.',
    };
    configuration.querySelectorAll('.pro-config-details').forEach((details) => {
      const summary = details.querySelector(':scope > summary');
      const title = String(summary?.textContent || '').trim();
      const body = details.querySelector(':scope > .pro-config-body');
      if (body && groupCopy[title] && !body.querySelector(':scope > .pro-advanced-group-help')) {
        body.prepend(make('p', 'pro-advanced-group-help', groupCopy[title]));
      }
      advancedGroups.appendChild(details);
    });
    el('proStepAdvanced').appendChild(advancedGroups);
""",
    "show detailed groups directly",
)
source = replace_once(
    source,
    """    const action = make('div', 'pro-guided-action');
    const stateCopy = serviceCard.querySelector('.pro-service-state-copy');
    const primary = el('btnStart');
    if (stateCopy) action.appendChild(stateCopy);
    action.appendChild(primary);
    const actionSlot = el('proStepAction');
    const saveState = make('div', 'pro-save-state', 'All changes saved.');
    saveState.id = 'proSaveState';
    actionSlot.append(action, saveState);
""",
    """    const action = make('div', 'pro-guided-action');
    const stateCopy = serviceCard.querySelector('.pro-service-state-copy');
    const primary = el('btnStart');
    const actionButtons = make('div', 'pro-guided-action-buttons');
    actionButtons.appendChild(primary);

    const manualSave = make('div', 'pro-guided-save-actions');
    const save = el('btnSaveConfig');
    const saveRestart = el('btnSaveRestart');
    if (save) {
      save.textContent = 'Save Changes';
      manualSave.appendChild(save);
    }
    if (saveRestart) {
      saveRestart.textContent = 'Save & Restart';
      manualSave.appendChild(saveRestart);
    }
    if (manualSave.children.length) actionButtons.appendChild(manualSave);

    if (stateCopy) action.appendChild(stateCopy);
    action.appendChild(actionButtons);
    const actionSlot = el('proStepAction');
    const saveState = make('div', 'pro-save-state', 'All changes saved.');
    saveState.id = 'proSaveState';
    actionSlot.append(action, saveState);
""",
    "keep explicit save controls in step five",
)
source_path.write_text(source, encoding="utf-8")

style_path = Path("assets/pro_guided_workflow.css")
style = style_path.read_text(encoding="utf-8")
style_marker = """body[data-ui-mode="advanced"] .pro-performance-picker .btn-group {
"""
style_additions = """body[data-ui-mode="advanced"] .pro-adapter-readiness {
  margin-top: 12px;
  border-radius: var(--radius-md);
  box-shadow: none;
}

body[data-ui-mode="advanced"] .pro-adapter-readiness .card-header {
  padding: 12px 14px;
}

body[data-ui-mode="advanced"] .pro-adapter-readiness .card-header h2 {
  font-size: 14px;
}

body[data-ui-mode="advanced"] .pro-adapter-readiness .card-body {
  padding: 14px;
}

body[data-ui-mode="advanced"] .pro-guided-advanced-groups {
  display: grid;
  gap: 12px;
}

body[data-ui-mode="advanced"] .pro-advanced-group-help {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.45;
}

"""
if style_additions not in style:
    style = replace_once(style, style_marker, style_additions + style_marker, "insert guided detail styles")

action_marker = """body[data-ui-mode="advanced"] .pro-guided-action .pro-service-primary {
"""
action_styles = """body[data-ui-mode="advanced"] .pro-guided-action-buttons {
  display: grid;
  gap: 10px;
}

body[data-ui-mode="advanced"] .pro-guided-save-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

body[data-ui-mode="advanced"] .pro-guided-save-actions .btn {
  min-height: 44px;
  justify-content: center;
}

"""
if action_styles not in style:
    style = replace_once(style, action_marker, action_styles + action_marker, "insert step five action styles")

mobile_marker = """  body[data-ui-mode="advanced"] .pro-adapter-row,
  body[data-ui-mode="advanced"] .pro-performance-picker .btn-group,
  body[data-ui-mode="advanced"] .pro-guided-action {
"""
mobile_replacement = """  body[data-ui-mode="advanced"] .pro-adapter-row,
  body[data-ui-mode="advanced"] .pro-performance-picker .btn-group,
  body[data-ui-mode="advanced"] .pro-guided-action,
  body[data-ui-mode="advanced"] .pro-guided-save-actions {
"""
style = replace_once(style, mobile_marker, mobile_replacement, "make save controls responsive")
style_path.write_text(style, encoding="utf-8")

test_path = Path("tests/test_pro_guided_workflow.py")
tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    """STYLE = ROOT / "assets" / "pro_guided_workflow.css"
SESSION = ROOT / "assets" / "browser_session.js"
""",
    """STYLE = ROOT / "assets" / "pro_guided_workflow.css"
SESSION = ROOT / "assets" / "browser_session.js"
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
""",
    "add field visibility fixture",
)
new_tests = r'''


def test_step_one_embeds_adapter_readiness_details() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    base = FIELD_VISIBILITY.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "const readinessCard = overview.querySelector('.adapter-readiness-card')" in source
    assert "readinessCard.classList.add('pro-adapter-readiness')" in source
    assert "adapterStep.appendChild(readinessCard)" in source
    assert "shell.appendChild(readinessCard)" in base
    assert ".pro-adapter-readiness" in style


def test_step_three_contains_complete_connection_setup() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    for field in (
        "ssid",
        "wpa2_passphrase",
        "band_preference",
        "ap_security",
        "country",
        "enable_internet",
    ):
        assert f"'{field}'" in source
    assert "network name, password, band, security, country" in source


def test_step_four_exposes_three_detailed_option_groups_directly() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "pro-guided-advanced-groups" in source
    assert "Channels, width, radio timing" in source
    assert "Gateway, DHCP, DNS" in source
    assert "Startup behavior, interface strategy" in source
    assert "make('details', 'pro-advanced-settings')" not in source
    assert ".pro-advanced-group-help" in style


def test_step_five_keeps_manual_save_and_restart_actions() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "pro-guided-save-actions" in source
    assert "save.textContent = 'Save Changes'" in source
    assert "saveRestart.textContent = 'Save & Restart'" in source
    assert "actionButtons.appendChild(manualSave)" in source
    assert ".pro-guided-save-actions" in style
'''
if "def test_step_one_embeds_adapter_readiness_details" not in tests:
    tests += new_tests
test_path.write_text(tests, encoding="utf-8")
