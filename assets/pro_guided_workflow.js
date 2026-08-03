(function buildProGuidedWorkflow() {
  'use strict';

  const AUTOSAVE_DELAY_MS = 650;
  const SAVE_TIMEOUT_MS = 12000;
  const RETRY_LIMIT = 120;
  let initialized = false;
  let guidedReady = false;
  let qualityReady = false;
  let troubleshootingReady = false;
  let saveTimer = null;
  let restartRequired = false;
  let retryCount = 0;
  let retryTimer = null;
  let retryQueued = false;
  let observer = null;

  function el(id) { return document.getElementById(id); }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  function icon(kind) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('pro-nav-svg');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', kind === 'wifi'
      ? 'M12 18.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm0-5a6.45 6.45 0 0 0-4.58 1.9l1.42 1.42a4.48 4.48 0 0 1 6.32 0l1.42-1.42A6.45 6.45 0 0 0 12 13.5Zm0-5a11.42 11.42 0 0 0-8.08 3.35l1.42 1.42a9.42 9.42 0 0 1 13.32 0l1.42-1.42A11.42 11.42 0 0 0 12 8.5Zm0-5A16.4 16.4 0 0 0 .42 8.3l1.42 1.42a14.4 14.4 0 0 1 20.32 0l1.42-1.42A16.4 16.4 0 0 0 12 3.5Z'
      : 'M12 2a2 2 0 0 1 2 2v1.1a7 7 0 0 1 2.27.94l.78-.78a2 2 0 1 1 2.83 2.83l-.78.78A7 7 0 0 1 20 11h1a2 2 0 1 1 0 4h-1a7 7 0 0 1-.9 2.13l.78.78a2 2 0 0 1-2.83 2.83l-.78-.78A7 7 0 0 1 14 20.9V22a2 2 0 1 1-4 0v-1.1a7 7 0 0 1-2.27-.94l-.78.78a2 2 0 0 1-2.83-2.83l.78-.78A7 7 0 0 1 4 15H3a2 2 0 1 1 0-4h1a7 7 0 0 1 .9-2.13l-.78-.78a2 2 0 1 1 2.83-2.83l.78.78A7 7 0 0 1 10 5.1V4a2 2 0 0 1 2-2Zm0 6a5 5 0 1 0 0 10 5 5 0 0 0 0-10Z');
    svg.appendChild(path);
    return svg;
  }

  function replaceNav(item, kind, label) {
    if (!item) return;
    if (item.dataset.proNavLabel === label) return;
    item.dataset.proNavLabel = label;
    item.replaceChildren(icon(kind), document.createTextNode(label));
  }

  function addStyles() {
    if (document.querySelector('link[data-pro-guided-styles]')) return;
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/assets/pro_guided_workflow.css?v=139-pro-hotfix';
    stylesheet.dataset.proGuidedStyles = '1';
    document.head.appendChild(stylesheet);
  }

  function step(number, title, help, id) {
    const section = make('section', 'pro-guided-step');
    const badge = make('span', 'pro-guided-number', String(number));
    const content = make('div', 'pro-guided-content');
    content.append(make('h3', 'pro-guided-title', title), make('p', 'pro-guided-help', help));
    const slot = make('div', 'pro-guided-slot');
    slot.id = id;
    content.appendChild(slot);
    section.append(badge, content);
    return section;
  }

  function serviceIsRunning() {
    const text = String(el('pillTxt')?.textContent || '').toLowerCase();
    return text.includes('running') && !text.includes('not running');
  }

  function savingState(state, text) {
    const node = el('proSaveState');
    if (!node) return;
    node.dataset.state = state;
    setText(node, text);
  }

  function waitUntilSaved() {
    const started = Date.now();
    return new Promise((resolve) => {
      function check() {
        const dirty = String(el('dirty')?.textContent || '').trim();
        if (!dirty) return resolve(true);
        if (Date.now() - started >= SAVE_TIMEOUT_MS) return resolve(false);
        window.setTimeout(check, 120);
      }
      check();
    });
  }

  async function saveConfiguration() {
    const save = el('btnSaveConfig');
    if (!save) return false;
    savingState('saving', 'Saving changes…');
    save.click();
    const saved = await waitUntilSaved();
    if (!saved) {
      savingState('error', 'Changes could not be saved. Open Troubleshooting for details.');
      return false;
    }
    savingState(restartRequired ? 'restart' : 'saved', restartRequired
      ? 'Changes saved. Restart the hotspot to apply them.'
      : 'All changes saved.');
    syncPrimaryAction();
    return true;
  }

  function scheduleSave(markRestart = true) {
    if (markRestart && serviceIsRunning()) restartRequired = true;
    savingState('saving', 'Saving changes…');
    if (saveTimer) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => void saveConfiguration(), AUTOSAVE_DELAY_MS);
  }

  function syncPrimaryAction() {
    const primary = el('btnStart');
    if (!primary) return;
    if (serviceIsRunning() && restartRequired) {
      primary.dataset.proServiceAction = 'start';
      primary.dataset.proGuidedAction = 'apply';
      primary.textContent = 'Apply Changes & Restart';
      primary.classList.remove('danger', 'secondary');
      primary.classList.add('primary');
      primary.disabled = false;
      return;
    }
    delete primary.dataset.proGuidedAction;
  }

  function wireAutosave(root) {
    if (!root || root.dataset.proAutosaveWired === '1') return;
    root.dataset.proAutosaveWired = '1';
    root.addEventListener('change', (event) => {
      if (!(event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement)) return;
      if (!event.isTrusted) return;
      scheduleSave(true);
      updateDependencies();
    });
    root.addEventListener('input', (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      if (!event.isTrusted) return;
      scheduleSave(true);
    });
    root.querySelectorAll('.preset-bar .btn').forEach((button) => {
      button.addEventListener('click', () => window.setTimeout(() => scheduleSave(true), 0));
    });
  }

  function updateDependencies() {
    const autoChannel = el('channel_auto_select');
    const channel5 = el('channel_5g');
    const channel6 = el('channel_6g');
    if (channel5) channel5.disabled = !!autoChannel?.checked;
    if (channel6) channel6.disabled = !!autoChannel?.checked;
    const bridge = el('bridge_mode');
    for (const id of ['bridge_name', 'bridge_uplink']) {
      const input = el(id);
      if (input) input.disabled = !bridge?.checked;
    }
  }

  function enforceNavigation() {
    addStyles();
    const overviewNav = document.querySelector('.nav-item[data-tab="overview"]');
    replaceNav(overviewNav, 'wifi', 'Set Up Hotspot');

    document.querySelector('.nav-item[data-tab="telemetry"]')?.remove();
    const logsNav = document.querySelector('.nav-item[data-tab="logs"]');
    logsNav?.remove();

    const troubleshootingNav = document.querySelector(
      '.nav-item[data-tab="troubleshooting"], .nav-item[data-tab="diagnostics"]',
    );
    if (troubleshootingNav) {
      troubleshootingNav.dataset.tab = 'troubleshooting';
      replaceNav(troubleshootingNav, 'trouble', 'Troubleshooting');
    }

    const diagnosticsPane = el('tab-diagnostics');
    if (diagnosticsPane) diagnosticsPane.id = 'tab-troubleshooting';
  }

  function buildGuidedSetup() {
    if (el('proGuidedWorkflow')) return true;
    const overview = el('tab-overview');
    const oldShell = overview?.querySelector('.pro-setup-shell');
    const configuration = el('proHotspotConfiguration');
    const serviceCard = overview?.querySelector('.pro-service-card');
    if (!overview || !oldShell || !configuration || !serviceCard) return false;

    const shell = make('div', 'pro-guided-shell');
    shell.id = 'proGuidedWorkflow';
    const card = make('section', 'pro-guided-card');
    const header = make('div', 'pro-guided-header');
    header.append(
      make('h2', '', 'Set Up Hotspot'),
      make('p', '', 'Configure the hotspot in order, then start it or apply changes safely.'),
    );
    const steps = make('div', 'pro-guided-steps');
    steps.append(
      step(1, 'Choose Wi-Fi adapter', 'Select the adapter VRhotspot should use. Recommended choices are labeled automatically.', 'proStepAdapter'),
      step(2, 'Choose performance mode', 'Choose the behavior that best matches your VR workflow.', 'proStepPerformance'),
      step(3, 'Configure hotspot', 'Set the network name, password, and internet-sharing preference.', 'proStepHotspot'),
      step(4, 'Fine-tune hotspot', 'Optional expert settings are grouped by purpose and can be left at their recommended defaults.', 'proStepAdvanced'),
      step(5, 'Start hotspot', 'Start, stop, or safely apply saved changes to a running hotspot.', 'proStepAction'),
    );
    card.append(header, steps);
    shell.appendChild(card);

    const adapter = document.querySelector('[data-field="ap_adapter"]');
    if (adapter) {
      const label = adapter.querySelector('label');
      if (label) label.textContent = 'Wi-Fi adapter';
      el('proStepAdapter').appendChild(adapter);
    }

    const preset = configuration.querySelector('.preset-bar');
    if (preset) {
      preset.classList.add('pro-performance-picker');
      const group = preset.querySelector('.btn-group');
      const order = [
        el('btnApplyVrProfileUltra'),
        el('btnApplyVrProfile'),
        el('btnApplyVrProfileHigh'),
        el('btnApplyVrProfileStable'),
      ].filter(Boolean);
      if (group) order.forEach((button) => group.appendChild(button));
      el('proStepPerformance').appendChild(preset);
    }
    const qos = document.querySelector('[data-field="qos_preset"]');
    if (qos) {
      qos.classList.add('pro-guided-hidden');
      el('proStepPerformance').appendChild(qos);
    }

    const hotspotFields = make('div', 'pro-hotspot-fields');
    for (const key of ['ssid', 'wpa2_passphrase', 'enable_internet']) {
      const field = document.querySelector(`[data-field="${key}"]`);
      if (field && key === 'ssid') {
        const label = field.querySelector('label');
        if (label) label.textContent = 'Hotspot name';
      }
      if (field && key === 'wpa2_passphrase') {
        const label = field.querySelector('label');
        if (label) label.textContent = 'Password';
      }
      if (field) hotspotFields.appendChild(field);
    }
    el('proStepHotspot').appendChild(hotspotFields);

    const advanced = make('details', 'pro-advanced-settings');
    const advancedSummary = make('summary', '', 'Advanced wireless, network, and system settings');
    const advancedBody = make('div', 'pro-advanced-body');
    configuration.querySelectorAll('.pro-config-details').forEach((details) => advancedBody.appendChild(details));
    advanced.append(advancedSummary, advancedBody);
    el('proStepAdvanced').appendChild(advanced);

    const action = make('div', 'pro-guided-action');
    const stateCopy = serviceCard.querySelector('.pro-service-state-copy');
    const primary = el('btnStart');
    if (stateCopy) action.appendChild(stateCopy);
    if (primary) action.appendChild(primary);
    const actionSlot = el('proStepAction');
    const saveState = make('div', 'pro-save-state', 'All changes saved.');
    saveState.id = 'proSaveState';
    actionSlot.append(action, saveState);

    const hidden = make('div', 'pro-guided-hidden');
    hidden.id = 'proGuidedHiddenControls';
    const headerBar = configuration.querySelector('.settings-header');
    const serviceSecondary = serviceCard.querySelector('.pro-service-secondary');
    const serviceMeta = serviceCard.querySelector('.hero-meta');
    const feedback = serviceCard.querySelectorAll('.hero-feedback');
    for (const node of [headerBar, serviceSecondary, serviceMeta, ...feedback]) {
      if (node) hidden.appendChild(node);
    }
    card.appendChild(hidden);

    overview.replaceChildren(shell);
    wireAutosave(card);
    updateDependencies();

    if (primary && primary.dataset.proGuidedWired !== '1') {
      primary.dataset.proGuidedWired = '1';
      primary.addEventListener('click', async (event) => {
        if (primary.dataset.proGuidedAction !== 'apply') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const saved = await saveConfiguration();
        if (!saved) return;
        restartRequired = false;
        savingState('saving', 'Applying changes and restarting…');
        el('btnSaveRestart')?.click();
      }, true);
    }

    const statusSource = el('pillTxt');
    if (statusSource && statusSource.dataset.proGuidedObserved !== '1') {
      statusSource.dataset.proGuidedObserved = '1';
      const statusObserver = new MutationObserver(() => {
        if (!serviceIsRunning()) restartRequired = false;
        syncPrimaryAction();
      });
      statusObserver.observe(statusSource, { childList: true, subtree: true, characterData: true });
    }
    return true;
  }

  function qualityMessage() {
    if (!serviceIsRunning()) return 'Start the hotspot to measure connection performance.';
    const summary = String(el('telemetrySummary')?.textContent || '').trim();
    return summary || 'Connection measurements will appear as clients begin using the hotspot.';
  }

  function buildConnectionQuality() {
    if (el('proConnectionQuality')) return true;
    const overview = el('tab-overview');
    const shell = overview?.querySelector('.pro-guided-shell');
    const telemetryPane = el('tab-telemetry');
    const telemetryCard = el('cardTelemetry');
    if (!shell || !telemetryPane || !telemetryCard) return false;

    document.querySelector('.nav-item[data-tab="telemetry"]')?.remove();
    const card = make('section', 'pro-quality-card');
    card.id = 'proConnectionQuality';
    const header = make('div', 'pro-quality-header');
    header.append(make('h2', '', 'Connection Quality'));
    const status = make('span', 'pill', serviceIsRunning() ? 'Measuring' : 'Waiting');
    status.id = 'proQualityStatus';
    header.appendChild(status);

    const body = make('div', 'pro-quality-body');
    const summary = make('div', 'pro-quality-summary', qualityMessage());
    summary.id = 'proQualitySummary';
    const warning = el('telemetryWarnings');
    const details = make('details', 'pro-quality-details');
    details.appendChild(make('summary', '', 'View detailed charts and client measurements'));

    const telemetryBody = telemetryCard.querySelector('.card-body');
    if (telemetryBody) details.appendChild(telemetryBody);
    const settings = details.querySelector('[data-field="telemetry_enable"]');
    if (settings) {
      settings.classList.add('pro-quality-settings');
      const label = settings.querySelector(':scope > label');
      if (label) label.textContent = 'Measurement options';
      const toggles = settings.querySelectorAll('.tog');
      for (const [toggle, text] of [
        [toggles[0], 'Measure connection quality'],
        [toggles[1], 'Watch for connection problems'],
      ]) {
        const input = toggle && toggle.querySelector('input');
        if (toggle && input) toggle.replaceChildren(input, document.createTextNode(` ${text}`));
      }
    }
    const interval = document.querySelector('[data-field="telemetry_interval_s"]');
    if (interval) interval.classList.add('pro-guided-hidden');

    body.append(summary);
    if (warning) body.appendChild(warning);
    body.appendChild(details);
    card.append(header, body);
    shell.appendChild(card);
    telemetryPane.hidden = true;

    const sources = [el('telemetrySummary'), el('telemetryWarnings'), el('pillTxt')].filter(Boolean);
    const qualityObserver = new MutationObserver(() => {
      setText(summary, qualityMessage());
      setText(status, serviceIsRunning() ? 'Live' : 'Waiting');
    });
    sources.forEach((node) => qualityObserver.observe(node, {
      childList: true,
      subtree: true,
      characterData: true,
    }));
    return true;
  }

  function buildTroubleshooting() {
    const diagnosticsPane = el('tab-troubleshooting') || el('tab-diagnostics');
    const logsPane = el('tab-logs');
    const nav = document.querySelector(
      '.nav-item[data-tab="troubleshooting"], .nav-item[data-tab="diagnostics"]',
    );
    if (!diagnosticsPane || !logsPane || !nav) return false;
    if (diagnosticsPane.querySelector('.troubleshooting-shell')) return true;

    nav.dataset.tab = 'troubleshooting';
    diagnosticsPane.id = 'tab-troubleshooting';
    replaceNav(nav, 'trouble', 'Troubleshooting');
    const logsNav = document.querySelector('.nav-item[data-tab="logs"]');
    logsNav?.remove();

    const shell = make('div', 'troubleshooting-shell');
    const header = make('section', 'troubleshooting-header');
    const copy = make('div');
    copy.append(
      make('h2', '', 'Troubleshooting'),
      make('p', '', 'Check system health, repair problems, inspect runtime details, and collect support information.'),
    );
    const actions = make('div', 'troubleshooting-actions');
    for (const id of ['btnRepair', 'btnRestart', 'btnRefreshPreflight']) {
      const control = el(id);
      if (control) actions.appendChild(control);
    }
    header.append(copy, actions);
    shell.append(header, make('h3', 'troubleshooting-section-label', 'System Health & Diagnostic Checks'));

    const preflight = diagnosticsPane.querySelector('.preflight-page');
    if (preflight) {
      preflight.querySelector('.preflight-header .action-group')?.remove();
      shell.appendChild(preflight);
    }

    shell.appendChild(make('h3', 'troubleshooting-section-label', 'Runtime Details, Logs & Support'));
    logsPane.querySelector('.pro-support-heading')?.remove();
    Array.from(logsPane.children).forEach((node) => shell.appendChild(node));
    diagnosticsPane.replaceChildren(shell);
    logsPane.hidden = true;
    return true;
  }

  function initialize() {
    enforceNavigation();
    try {
      if (!guidedReady) guidedReady = buildGuidedSetup();
      if (guidedReady && !qualityReady) qualityReady = buildConnectionQuality();
      if (!troubleshootingReady) troubleshootingReady = buildTroubleshooting();
      initialized = guidedReady && qualityReady && troubleshootingReady;
      if (initialized && document.body) delete document.body.dataset.proGuidedError;
      return initialized;
    } catch (error) {
      if (document.body) {
        document.body.dataset.proGuidedError = String(error && error.message ? error.message : error);
      }
      console.error('VRhotspot Pro workflow retrying after UI error.', error);
      return false;
    }
  }

  function scheduleRetry() {
    if (retryQueued || initialized || retryCount >= RETRY_LIMIT) return;
    retryQueued = true;
    retryTimer = window.setTimeout(() => {
      retryQueued = false;
      retryCount += 1;
      if (initialize()) {
        if (observer) observer.disconnect();
        if (retryTimer) window.clearTimeout(retryTimer);
        return;
      }
      scheduleRetry();
    }, 100);
  }

  function start() {
    enforceNavigation();
    if (initialize()) return;
    observer = new MutationObserver(scheduleRetry);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    scheduleRetry();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
