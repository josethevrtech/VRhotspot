(function buildProGuidedWorkflow() {
  'use strict';

  const ADVANCED_MODE = 'advanced';
  const AUTOSAVE_DELAY_MS = 650;
  const SAVE_TIMEOUT_MS = 12000;
  const PROFILE_COPY = {
    btnApplyVrProfileUltra: {
      value: 'ultra_low_latency',
      description: 'Prioritizes the lowest possible response time for demanding VR streaming.',
    },
    btnApplyVrProfile: {
      value: 'balanced',
      description: 'Recommended default for a strong balance of responsiveness and stability.',
    },
    btnApplyVrProfileHigh: {
      value: 'high_throughput',
      description: 'Prioritizes sustained transfer speed for large or bandwidth-heavy workloads.',
    },
    btnApplyVrProfileStable: {
      value: 'vr',
      description: 'Favors connection consistency when the wireless environment is unpredictable.',
    },
  };
  const CONNECTION_FIELDS = [
    'ssid',
    'wpa2_passphrase',
    'band_preference',
    'ap_security',
    'country',
    'enable_internet',
  ];

  let reconcileQueued = false;
  let composeRetryTimer = null;
  let saveTimer = null;
  let restartRequired = false;
  let statusObserver = null;
  let qualityObserver = null;
  let adapterOptionsObserver = null;
  const internalHomes = new Map();

  window.VRHOTSPOT_PRO_COMPOSER = 'authoritative-v1';

  function el(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  function isAdvancedMode() {
    return document.body?.dataset.uiMode === ADVANCED_MODE;
  }

  function setStage(stage, error) {
    if (!document.body) return;
    document.body.dataset.proGuidedStage = stage;
    if (error) document.body.dataset.proGuidedError = error;
    else delete document.body.dataset.proGuidedError;
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
    if (!item || item.dataset.proNavLabel === label) return;
    item.dataset.proNavLabel = label;
    item.replaceChildren(icon(kind), document.createTextNode(label));
  }

  function ensureStyles() {
    const styles = [
      ['/assets/pro_guided_workflow.css?v=148-authoritative-composer', 'base'],
      ['/assets/pro_guided_authoritative.css?v=148-owned-cells-1', 'authoritative'],
    ];
    for (const [href, kind] of styles) {
      if (document.querySelector(`link[data-pro-guided-styles="${kind}"]`)) continue;
      const stylesheet = document.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = href;
      stylesheet.dataset.proGuidedStyles = kind;
      document.head.appendChild(stylesheet);
    }
  }

  function enforceNavigation() {
    ensureStyles();
    const overviewNav = document.querySelector('.nav-item[data-tab="overview"]');
    replaceNav(overviewNav, 'wifi', 'Set Up Hotspot');

    document.querySelector('.nav-item[data-tab="telemetry"]')?.remove();
    document.querySelector('.nav-item[data-tab="logs"]')?.remove();

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

  function step(number, title, help, id) {
    const section = make('section', 'pro-guided-step');
    section.dataset.step = String(number);
    const badge = make('span', 'pro-guided-number', String(number));
    badge.setAttribute('aria-hidden', 'true');
    const content = make('div', 'pro-guided-content');
    content.append(make('h3', 'pro-guided-title', title), make('p', 'pro-guided-help', help));
    const slot = make('div', 'pro-guided-slot');
    slot.id = id;
    content.appendChild(slot);
    section.append(badge, content);
    return section;
  }

  function guidedSlot(shell, id) {
    const node = shell?.querySelector(`#${id}`);
    if (!node) throw new Error(`missing guided slot: ${id}`);
    return node;
  }

  function appendIfNeeded(parent, node) {
    if (parent && node && node.parentNode !== parent) parent.appendChild(node);
  }

  function prependIfNeeded(parent, node) {
    if (!parent || !node) return;
    if (node.parentNode !== parent || parent.firstElementChild !== node) parent.prepend(node);
  }

  function ensureChildOrder(parent, nodes) {
    if (!parent) return;
    const desired = nodes.filter(Boolean);
    const current = Array.from(parent.children).filter((node) => desired.includes(node));
    if (current.length === desired.length && current.every((node, index) => node === desired[index])) return;
    desired.forEach((node) => parent.appendChild(node));
  }

  function rememberInternalHome(node) {
    if (!node || internalHomes.has(node)) return;
    internalHomes.set(node, { parent: node.parentNode, next: node.nextSibling });
  }

  function restoreInternalNode(node) {
    const home = internalHomes.get(node);
    if (!node || !home?.parent) return;
    if (home.next && home.next.parentNode === home.parent) home.parent.insertBefore(node, home.next);
    else home.parent.appendChild(node);
    internalHomes.delete(node);
  }

  function applyRecommendedButtonState(recommended, advanced) {
    // The composer is the sole Pro/Basic visibility owner for this button.
    // Pro hides it (friendly adapter labels already carry the recommendation);
    // the complete state must hold before the 'ready' stage is published.
    if (!recommended) return;
    if (advanced) {
      if (!recommended.hidden) recommended.hidden = true;
      if (recommended.getAttribute('aria-hidden') !== 'true') {
        recommended.setAttribute('aria-hidden', 'true');
      }
      if (recommended.tabIndex !== -1) recommended.tabIndex = -1;
      if (recommended.style.display !== 'none') recommended.style.display = 'none';
      return;
    }
    if (recommended.hidden) recommended.hidden = false;
    if (recommended.hasAttribute('aria-hidden')) recommended.removeAttribute('aria-hidden');
    if (recommended.tabIndex !== 0) recommended.tabIndex = 0;
    if (recommended.style.display) recommended.style.removeProperty('display');
  }

  function restoreBasicPresentation() {
    if (composeRetryTimer) {
      window.clearTimeout(composeRetryTimer);
      composeRetryTimer = null;
    }
    if (adapterOptionsObserver) {
      adapterOptionsObserver.disconnect();
      adapterOptionsObserver = null;
    }
    if (document.body) delete document.body.dataset.proBand;
    for (const node of Array.from(internalHomes.keys())) restoreInternalNode(node);
    document.querySelectorAll('.pro-runtime-wrapper').forEach((node) => node.remove());
    document.querySelectorAll('.pro-adapter-field, .pro-password-field').forEach((node) => {
      node.classList.remove('pro-adapter-field', 'pro-password-field');
      delete node.dataset.proComposerDecorated;
    });
    applyRecommendedButtonState(el('btnUseRecommended'), false);
    setStage('waiting-for-pro');
  }

  function serviceState() {
    const raw = String(el('proServiceStateText')?.textContent || el('pillTxt')?.textContent || 'Checking…').trim();
    const value = raw.toLowerCase();
    if (value.includes('error') || value.includes('failed') || value.includes('attention')) {
      return { name: 'error', label: 'Needs attention' };
    }
    if (value.includes('starting') || value.includes('stopping') || value.includes('working') || value.includes('repair')) {
      return { name: 'working', label: raw || 'Working…' };
    }
    if (value.includes('running') && !value.includes('not running')) {
      return { name: 'running', label: 'Running' };
    }
    if (value.includes('stopped') || value.includes('inactive') || value.includes('not running')) {
      return { name: 'stopped', label: 'Stopped' };
    }
    return { name: 'loading', label: raw || 'Checking…' };
  }

  function serviceIsRunning() {
    return serviceState().name === 'running';
  }

  function syncHeaderStatus() {
    const status = el('proHeaderStatus');
    if (!status) return;
    const state = serviceState();
    status.dataset.state = state.name;
    setText(status, state.label);
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
    if (!isAdvancedMode()) return;
    if (markRestart && serviceIsRunning()) restartRequired = true;
    savingState('saving', 'Saving changes…');
    if (saveTimer) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => void saveConfiguration(), AUTOSAVE_DELAY_MS);
  }

  function syncPrimaryAction() {
    const primary = el('btnStart');
    if (!primary) return;
    if (serviceIsRunning() && restartRequired) {
      primary.dataset.proGuidedAction = 'apply';
      setText(primary, 'Apply Changes & Restart');
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
      syncPerformanceSelection();
      syncAdapterBandNotice();
    });
    root.addEventListener('input', (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      if (!event.isTrusted) return;
      scheduleSave(true);
    });
    root.addEventListener('click', (event) => {
      const button = event.target instanceof Element
        ? event.target.closest('.preset-bar .btn')
        : null;
      if (!button || !root.contains(button)) return;
      window.setTimeout(() => {
        scheduleSave(true);
        syncPerformanceSelection();
        syncAdapterBandNotice();
      }, 0);
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

  function prerequisitesReady() {
    const overview = el('tab-overview');
    const configuration = el('proHotspotConfiguration');
    const serviceCard = overview?.querySelector('.pro-service-card');
    const requiredIds = [
      'ap_adapter',
      'btnUseRecommended',
      'btnReloadAdapters',
      'btnApplyVrProfileUltra',
      'btnApplyVrProfile',
      'btnApplyVrProfileHigh',
      'btnApplyVrProfileStable',
      'qos_preset',
      ...CONNECTION_FIELDS,
      'btnStart',
      'btnSaveConfig',
      'btnSaveRestart',
      'btnRepair',
    ];
    const preset = configuration?.querySelector('.preset-bar')
      || overview?.querySelector('#proStepPerformance .preset-bar');
    return !!overview
      && !!configuration
      && !!serviceCard
      && !!preset
      && requiredIds.every((id) => !!el(id));
  }

  function ensureWorkflowShell() {
    const existing = el('proGuidedWorkflow');
    if (existing) return existing;
    if (!prerequisitesReady()) return null;

    const overview = el('tab-overview');
    const originalNodes = Array.from(overview.children);
    const shell = make('div', 'pro-guided-shell');
    shell.id = 'proGuidedWorkflow';

    const card = make('section', 'pro-guided-card');
    const header = make('div', 'pro-guided-header');
    const headerCopy = make('div', 'pro-guided-header-copy');
    headerCopy.append(
      make('h2', '', 'Set Up Hotspot'),
      make('p', '', 'Configure the hotspot in order, then start it or apply changes safely.'),
    );
    const headerMeta = make('div', 'pro-guided-header-meta');
    const status = make('div', 'pro-header-status', 'Checking…');
    status.id = 'proHeaderStatus';
    status.setAttribute('aria-live', 'polite');
    const saveState = make('div', 'pro-save-state', 'All changes saved.');
    saveState.id = 'proSaveState';
    saveState.setAttribute('aria-live', 'polite');
    headerMeta.append(status, saveState);
    header.append(headerCopy, headerMeta);
    header.dataset.proDensityReady = '1';

    const steps = make('div', 'pro-guided-steps');
    steps.append(
      step(1, 'Choose Wi-Fi adapter', 'Use Recommended for the best available adapter, or rescan after connecting new hardware.', 'proStepAdapter'),
      step(2, 'Choose performance mode', 'Choose the tradeoff that best matches latency, throughput, balance, or stability.', 'proStepPerformance'),
      step(3, 'Configure hotspot', 'Set the hotspot name, password, band, security, country, and internet-sharing behavior.', 'proStepHotspot'),
      step(4, 'Fine-tune hotspot', 'Review detailed Wireless, Network, and System & Performance options or keep the recommended defaults.', 'proStepAdvanced'),
      step(5, 'Start hotspot', 'Review pending changes, start or stop the hotspot, save safely, or repair the network.', 'proStepAction'),
    );
    card.append(header, steps);

    const staging = make('div', 'pro-guided-hidden');
    staging.id = 'proGuidedStaging';
    originalNodes.forEach((node) => staging.appendChild(node));
    card.appendChild(staging);
    shell.appendChild(card);
    overview.replaceChildren(shell);

    wireAutosave(card);
    syncHeaderStatus();
    return shell;
  }

  function syncStepCopy(shell) {
    const copy = {
      proStepAdapter: [
        'Choose Wi-Fi adapter',
        'Use Recommended for the best available adapter, or rescan after connecting new hardware.',
      ],
      proStepPerformance: [
        'Choose performance mode',
        'Choose the tradeoff that best matches latency, throughput, balance, or stability.',
      ],
      proStepHotspot: [
        'Configure hotspot',
        'Set the hotspot name, password, band, security, country, and internet-sharing behavior.',
      ],
      proStepAdvanced: [
        'Fine-tune hotspot',
        'Review detailed Wireless, Network, and System & Performance options or keep the recommended defaults.',
      ],
      proStepAction: [
        'Start hotspot',
        'Review pending changes, start or stop the hotspot, save safely, or repair the network.',
      ],
    };
    for (const [id, [title, help]] of Object.entries(copy)) {
      const content = guidedSlot(shell, id).closest('.pro-guided-content');
      setText(content?.querySelector('.pro-guided-title'), title);
      setText(content?.querySelector('.pro-guided-help'), help);
    }
  }

  function adapterTechnicalSummary(adapter, ifname, fallback = '') {
    if (!adapter) return fallback || `Interface: ${ifname || '--'}`;
    const parts = [];
    const name = String(adapter.name || adapter.model || '').trim();
    if (name) parts.push(name);
    parts.push(`Interface: ${ifname || adapter.ifname || '--'}`);
    if (adapter.phy) parts.push(`Radio: ${adapter.phy}`);
    const bus = String(adapter.bus || '').trim();
    if (bus) parts.push(`Bus: ${bus.toUpperCase()}`);
    const bands = [];
    if (adapter.supports_2ghz) bands.push('2.4 GHz');
    if (adapter.supports_5ghz) bands.push('5 GHz');
    if (adapter.supports_6ghz) bands.push('6 GHz');
    if (bands.length) parts.push(`Bands: ${bands.join(', ')}`);
    parts.push(adapter.supports_ap ? 'AP mode supported' : 'AP mode not supported');
    const country = adapter.regdom?.country || adapter.country || '';
    if (country) parts.push(`Regulatory: ${country}`);
    if (Number.isFinite(Number(adapter.score))) parts.push(`Score: ${adapter.score}`);
    return parts.join(' · ');
  }

  function adapterRecord(ifname) {
    try {
      if (typeof getAdapterByIfname === 'function') return getAdapterByIfname(ifname);
    } catch {
      // The synthetic DOM fixture intentionally has no adapter inventory.
    }
    return null;
  }

  function friendlyAdapterKind(adapter, rawLabel) {
    const bus = String(adapter?.bus || '').trim().toLowerCase();
    const identity = `${bus} ${adapter?.name || ''} ${rawLabel || ''}`.toLowerCase();
    if (bus === 'usb' || identity.includes('usb')) return 'usb';
    if (['pci', 'pcie', 'platform', 'sdio', 'internal'].includes(bus)) return 'internal';
    return adapter ? 'internal' : 'other';
  }

  function adapterDetailsIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('pro-adapter-details-icon');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', 'M12 5c5.5 0 9.5 5.2 9.7 5.4a1 1 0 0 1 0 1.2C21.5 11.8 17.5 17 12 17S2.5 11.8 2.3 11.6a1 1 0 0 1 0-1.2C2.5 10.2 6.5 5 12 5Zm0 2c-3.7 0-6.8 3-7.6 4 .8 1 3.9 4 7.6 4s6.8-3 7.6-4c-.8-1-3.9-4-7.6-4Zm0 1.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5Z');
    svg.appendChild(path);
    return svg;
  }

  function syncFriendlyAdapterOptions(select) {
    const counters = { usb: 0, internal: 0, other: 0 };
    const recommended = String(select.dataset.recommended || '');
    let selected = null;

    for (const option of Array.from(select.options)) {
      const rawLabel = option.dataset.rawAdapterLabel || String(option.textContent || '').trim();
      if (!option.dataset.rawAdapterLabel) option.dataset.rawAdapterLabel = rawLabel;
      const adapter = adapterRecord(option.value);
      const kind = friendlyAdapterKind(adapter, rawLabel);
      let label;
      if (kind === 'usb') {
        counters.usb += 1;
        label = `USB Wi-Fi ${counters.usb}`;
      } else if (kind === 'internal') {
        label = `Internal Wi-Fi ${counters.internal}`;
        counters.internal += 1;
      } else {
        counters.other += 1;
        label = `Wi-Fi Adapter ${counters.other}`;
      }
      if (option.value === recommended) label += ' (Recommended)';

      const technical = adapterTechnicalSummary(adapter, option.value, rawLabel);
      if (option.textContent !== label) option.textContent = label;
      option.removeAttribute('title');
      option.dataset.technicalLabel = technical;
      if (option.selected) selected = { label, technical };
    }
    return selected;
  }

  function syncAdapterPresentation(select, info, details) {
    const selected = syncFriendlyAdapterOptions(select);
    const technical = selected?.technical || 'Select a Wi-Fi adapter to view its technical identity.';
    const expanded = info.getAttribute('aria-expanded') === 'true';
    const action = expanded ? 'Hide adapter details' : 'Show adapter details';
    info.hidden = select.options.length === 0;
    info.title = action;
    info.setAttribute('aria-label', action);
    info.removeAttribute('data-tip');
    setText(details, technical);
  }

  function syncAdapterBandNotice() {
    const hint = el('adapterHint');
    const bandSelect = el('band_preference');
    if (!hint || !bandSelect || !document.body) return;

    let band = String(bandSelect.value || '');
    try {
      if (typeof resolveBandPref === 'function') band = resolveBandPref(band);
    } catch {
      // The isolated DOM fixture does not expose every production helper.
    }
    document.body.dataset.proBand = band;

    if (band !== '6ghz') {
      hint.textContent = '';
      hint.hidden = true;
      return;
    }

    hint.hidden = false;
    try {
      if (typeof maybeAutoPickAdapterForBand === 'function') maybeAutoPickAdapterForBand();
    } catch {
      // Keep the notice visible even when the production helper is unavailable.
    }
  }

  function decorateAdapter(shell) {
    const field = document.querySelector('[data-field="ap_adapter"]');
    const select = el('ap_adapter');
    const recommended = el('btnUseRecommended');
    const rescan = el('btnReloadAdapters');
    if (!field || !select || !recommended || !rescan) return false;

    prependIfNeeded(guidedSlot(shell, 'proStepAdapter'), field);
    field.classList.add('pro-adapter-field');
    field.dataset.proDensityReady = '1';

    let row = field.querySelector(':scope > .pro-adapter-row');
    if (!row) {
      rememberInternalHome(select);
      rememberInternalHome(recommended);
      rememberInternalHome(rescan);
      row = make('div', 'pro-adapter-row pro-runtime-wrapper');
      const label = field.querySelector(':scope > label, :scope > .field-label-with-tip');
      if (label?.nextSibling) field.insertBefore(row, label.nextSibling);
      else field.prepend(row);
    }

    let info = row.querySelector('#proAdapterInfo');
    if (!info) {
      info = make('button', 'btn pro-adapter-info');
      info.id = 'proAdapterInfo';
      info.type = 'button';
      info.setAttribute('aria-expanded', 'false');
      info.setAttribute('aria-controls', 'proAdapterDetails');
      info.appendChild(adapterDetailsIcon());
    }
    info.classList.remove('tip');
    info.removeAttribute('data-tip');

    let details = field.querySelector(':scope > #proAdapterDetails');
    if (!details) {
      details = make('div', 'pro-adapter-details pro-runtime-wrapper');
      details.id = 'proAdapterDetails';
      details.hidden = true;
      details.setAttribute('role', 'status');
      field.appendChild(details);
    }

    applyRecommendedButtonState(recommended, true);
    setText(recommended, 'Recommended');
    setText(rescan, 'Rescan adapters');
    ensureChildOrder(row, [select, info, recommended, rescan]);

    if (row.dataset.proAdapterWired !== '1') {
      row.dataset.proAdapterWired = '1';
      row.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : null;
        const detailsControl = target?.closest('#proAdapterInfo');
        if (detailsControl === info) {
          const expanded = info.getAttribute('aria-expanded') === 'true';
          info.setAttribute('aria-expanded', expanded ? 'false' : 'true');
          details.hidden = expanded;
          syncAdapterPresentation(select, info, details);
          return;
        }
        if (target === recommended || recommended.contains(target)) {
          window.setTimeout(() => {
            if (!isAdvancedMode()) return;
            syncAdapterPresentation(select, info, details);
            syncAdapterBandNotice();
          }, 0);
        }
      });
      row.addEventListener('change', (event) => {
        if (event.target !== select || !isAdvancedMode()) return;
        info.setAttribute('aria-expanded', 'false');
        details.hidden = true;
        syncAdapterPresentation(select, info, details);
        syncAdapterBandNotice();
      });
    }

    if (!adapterOptionsObserver) {
      adapterOptionsObserver = new MutationObserver(() => {
        if (!isAdvancedMode()) return;
        syncAdapterPresentation(select, info, details);
        syncAdapterBandNotice();
      });
      adapterOptionsObserver.observe(select, { childList: true, subtree: true });
    }

    syncAdapterPresentation(select, info, details);
    syncAdapterBandNotice();
    field.dataset.proComposerDecorated = '1';
    document.querySelectorAll('#tab-overview [data-adapter-readiness-card]')
      .forEach((node) => node.remove());
    return true;
  }

  function syncPerformanceSelection() {
    const qos = el('qos_preset');
    const description = el('proPerformanceDescription');
    if (!qos || !description) return;
    const selected = String(qos.value || 'off');
    let selectedCopy = 'Choose the performance behavior that best matches this hotspot.';
    for (const [id, profile] of Object.entries(PROFILE_COPY)) {
      const button = el(id);
      if (!button) continue;
      const active = selected === profile.value;
      button.classList.toggle('is-selected', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      if (active) selectedCopy = profile.description;
    }
    setText(description, selectedCopy);
  }

  function decoratePerformance(shell) {
    const configuration = el('proHotspotConfiguration');
    const preset = configuration?.querySelector('.preset-bar')
      || shell.querySelector('.pro-performance-picker');
    const qosField = document.querySelector('[data-field="qos_preset"]');
    if (!preset || !qosField) return false;

    preset.classList.add('pro-performance-picker');
    preset.dataset.proDensityReady = '1';
    const group = preset.querySelector('.btn-group');
    const order = [
      el('btnApplyVrProfileUltra'),
      el('btnApplyVrProfile'),
      el('btnApplyVrProfileHigh'),
      el('btnApplyVrProfileStable'),
    ];
    if (group) ensureChildOrder(group, order);

    let description = el('proPerformanceDescription');
    if (!description) {
      description = make('p', 'pro-performance-description');
      description.id = 'proPerformanceDescription';
      preset.appendChild(description);
    }
    appendIfNeeded(guidedSlot(shell, 'proStepPerformance'), preset);

    qosField.classList.add('pro-guided-hidden');
    appendIfNeeded(el('proGuidedStaging'), qosField);
    syncPerformanceSelection();
    return true;
  }

  function setHotspotFieldLabel(field, text) {
    const label = field?.querySelector(':scope > label, :scope > .field-label-with-tip > label');
    if (label) setText(label, text);
  }

  function passwordRowComplete(field, input, reveal, qr, hint) {
    // Verify only the application-owned nodes. Password managers and other
    // extensions may inject siblings, wrappers, or shadow hosts at any time;
    // those must never demote readiness or trigger a DOM fight.
    const row = field.querySelector(':scope > .pro-password-row');
    if (!row) return false;
    const cell = row.querySelector(':scope > .pro-password-input-cell');
    if (!cell || !cell.contains(input)) return false;
    if (reveal.parentElement !== row || qr.parentElement !== row) return false;
    const children = Array.from(row.children);
    const cellIndex = children.indexOf(cell);
    const revealIndex = children.indexOf(reveal);
    const qrIndex = children.indexOf(qr);
    if (!(cellIndex > -1 && cellIndex < revealIndex && revealIndex < qrIndex)) return false;
    if (hint) {
      if (!field.contains(hint) || row.contains(hint)) return false;
      if (!(row.compareDocumentPosition(hint) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
    }
    return true;
  }

  function decoratePassword(field) {
    const input = el('wpa2_passphrase');
    const reveal = el('btnRevealPass');
    const qr = el('btnShowQr');
    const hint = el('passHint');
    if (!field || !input || !reveal || !qr) return false;
    field.classList.add('pro-password-field');
    field.dataset.proDensityReady = '1';
    setHotspotFieldLabel(field, 'Password');

    let row = field.querySelector(':scope > .pro-password-row');
    if (!row) {
      rememberInternalHome(input);
      rememberInternalHome(reveal);
      rememberInternalHome(qr);
      if (hint) rememberInternalHome(hint);
      row = make('div', 'pro-password-row pro-runtime-wrapper');
      const label = field.querySelector(':scope > label, :scope > .field-label-with-tip');
      if (label?.nextSibling) field.insertBefore(row, label.nextSibling);
      else field.prepend(row);
    }

    let cell = row.querySelector(':scope > .pro-password-input-cell');
    if (!cell) {
      cell = make('div', 'pro-password-input-cell pro-runtime-wrapper');
      row.prepend(cell);
    }
    // The input stays wherever it lives inside the application-owned cell:
    // if a password manager wraps it there, moving it back would start a
    // mutation fight. Only reclaim it when it left the cell entirely.
    if (!cell.contains(input)) cell.appendChild(input);

    // Idempotent writes only: this runs from a childList observer on every
    // reconcile, so a same-value rewrite would observe itself and loop.
    if (reveal.type !== 'button') reveal.type = 'button';
    // classList.add serializes the attribute even for present tokens, which
    // still queues a mutation record — guard to stay observer-quiet.
    if (!reveal.classList.contains('icon-only')) reveal.classList.add('icon-only');
    if (reveal.title !== 'Show or hide password') reveal.title = 'Show or hide password';
    if (reveal.getAttribute('aria-label') !== 'Show or hide password') {
      reveal.setAttribute('aria-label', 'Show or hide password');
    }
    if (qr.type !== 'button') qr.type = 'button';
    setText(qr, 'QR');
    if (qr.className !== 'btn icon-only') qr.className = 'btn icon-only';
    if (qr.title !== 'Show QR code') qr.title = 'Show QR code';
    if (qr.getAttribute('aria-label') !== 'Show QR code') {
      qr.setAttribute('aria-label', 'Show QR code');
    }
    ensureChildOrder(row, [cell, reveal, qr]);

    if (hint) {
      hint.classList.add('pro-password-hint');
      appendIfNeeded(field, hint);
    }
    // Ready may only be published once the composed row is verifiably
    // complete; a transient DOM state must fail the pass, not go silent.
    const complete = passwordRowComplete(field, input, reveal, qr, hint);
    if (complete) field.dataset.proComposerDecorated = '1';
    else delete field.dataset.proComposerDecorated;
    return complete;
  }

  function decorateHotspot(shell) {
    let fields = shell.querySelector('.pro-hotspot-fields');
    if (!fields) {
      fields = make('div', 'pro-hotspot-fields');
      guidedSlot(shell, 'proStepHotspot').appendChild(fields);
    }
    fields.dataset.proLayout = 'organized';

    const labels = {
      ssid: 'Hotspot name (SSID)',
      wpa2_passphrase: 'Password',
      band_preference: 'Band',
      ap_security: 'Security',
      country: 'Country',
    };
    const ordered = [];
    let passwordReady = true;
    for (const key of CONNECTION_FIELDS) {
      const field = document.querySelector(`[data-field="${key}"]`);
      if (!field) return false;
      field.classList.add('pro-hotspot-field');
      field.dataset.proHotspotKey = key;
      if (labels[key]) setHotspotFieldLabel(field, labels[key]);
      if (key === 'wpa2_passphrase') passwordReady = decoratePassword(field);
      ordered.push(field);
    }
    ensureChildOrder(fields, ordered);
    return passwordReady;
  }

  function addAdvancedGroupHelp(details, text) {
    const body = details.querySelector(':scope > .pro-config-body');
    if (!body || body.querySelector(':scope > .pro-advanced-group-help')) return;
    body.prepend(make('p', 'pro-advanced-group-help', text));
  }

  function decorateAdvanced(shell) {
    const configuration = el('proHotspotConfiguration');
    let groups = shell.querySelector('.pro-guided-advanced-groups');
    if (!groups) {
      groups = make('div', 'pro-guided-advanced-groups');
      guidedSlot(shell, 'proStepAdvanced').appendChild(groups);
    }
    const copy = {
      Wireless: 'Channels, width, radio timing, transmit power, automatic selection, and fallback behavior.',
      Network: 'Gateway, DHCP, DNS, NAT acceleration, bridge mode, and firewall integration.',
      'System & Performance': 'Startup behavior, interface strategy, power management, CPU tuning, kernel tuning, and debug logging.',
    };
    const candidates = configuration?.querySelectorAll('.pro-config-details') || [];
    candidates.forEach((details) => {
      const title = String(details.querySelector(':scope > summary')?.textContent || '').trim();
      if (copy[title]) addAdvancedGroupHelp(details, copy[title]);
      appendIfNeeded(groups, details);
    });
    return groups.children.length >= 3;
  }

  function wirePrimaryAction(primary) {
    if (!primary || primary.dataset.proGuidedWired === '1') return;
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

  function decorateAction(shell) {
    const serviceCard = el('proGuidedStaging')?.querySelector('.pro-service-card')
      || document.querySelector('.pro-service-card');
    const stateCopy = serviceCard?.querySelector('.pro-service-state-copy');
    const primary = el('btnStart');
    const save = el('btnSaveConfig');
    const saveRestart = el('btnSaveRestart');
    const repair = el('btnRepair');
    if (!primary || !save || !saveRestart || !repair) return false;

    let action = shell.querySelector('.pro-guided-action');
    if (!action) {
      action = make('div', 'pro-guided-action');
      const buttons = make('div', 'pro-guided-action-buttons');
      const saves = make('div', 'pro-guided-save-actions');
      const secondary = make('div', 'pro-guided-secondary-actions');
      buttons.append(primary, saves, secondary);
      action.append(stateCopy || make('div'), buttons);
      guidedSlot(shell, 'proStepAction').appendChild(action);
    }
    const buttons = action.querySelector('.pro-guided-action-buttons');
    const saves = action.querySelector('.pro-guided-save-actions');
    const secondary = action.querySelector('.pro-guided-secondary-actions');
    prependIfNeeded(buttons, primary);
    setText(save, 'Save Changes');
    setText(saveRestart, 'Save & Restart');
    ensureChildOrder(saves, [save, saveRestart]);
    setText(repair, 'Repair Network');
    appendIfNeeded(secondary, repair);
    if (stateCopy) prependIfNeeded(action, stateCopy);
    wirePrimaryAction(primary);
    return true;
  }

  function rehydrateWorkflow(shell) {
    if (!isAdvancedMode()) return false;
    syncStepCopy(shell);
    const ready = [
      decorateAdapter(shell),
      decoratePerformance(shell),
      decorateHotspot(shell),
      decorateAdvanced(shell),
      decorateAction(shell),
    ].every(Boolean);
    wireAutosave(shell.querySelector('.pro-guided-card'));
    updateDependencies();
    syncAdapterBandNotice();
    syncHeaderStatus();
    syncPrimaryAction();
    return ready;
  }

  function qualityMessage() {
    if (!serviceIsRunning()) return 'Start the hotspot to measure connection performance.';
    const summary = String(el('telemetrySummary')?.textContent || '').trim();
    return summary || 'Connection measurements will appear as clients begin using the hotspot.';
  }

  function ensureConnectionQuality(shell) {
    const existing = el('proConnectionQuality');
    if (existing) return true;
    const telemetryPane = el('tab-telemetry');
    const telemetryCard = el('cardTelemetry');
    if (!telemetryPane || !telemetryCard) return false;

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
    const details = make('details', 'pro-quality-details');
    details.appendChild(make('summary', '', 'View detailed charts and client measurements'));
    const telemetryBody = telemetryCard.querySelector('.card-body');
    if (telemetryBody) details.appendChild(telemetryBody);
    const warning = el('telemetryWarnings');
    body.append(summary);
    if (warning) body.appendChild(warning);
    body.appendChild(details);
    card.append(header, body);
    shell.appendChild(card);
    telemetryPane.hidden = true;

    if (qualityObserver) qualityObserver.disconnect();
    qualityObserver = new MutationObserver(() => {
      setText(summary, qualityMessage());
      setText(status, serviceIsRunning() ? 'Live' : 'Waiting');
    });
    [el('telemetrySummary'), el('telemetryWarnings'), el('pillTxt')]
      .filter(Boolean)
      .forEach((node) => qualityObserver.observe(node, {
        childList: true,
        subtree: true,
        characterData: true,
      }));
    return true;
  }

  function ensureTroubleshooting() {
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
    document.querySelector('.nav-item[data-tab="logs"]')?.remove();

    const shell = make('div', 'troubleshooting-shell');
    const header = make('section', 'troubleshooting-header');
    const copy = make('div');
    copy.append(
      make('h2', '', 'Troubleshooting'),
      make('p', '', 'Check system health, restart services, inspect runtime details, and collect support information.'),
    );
    const actions = make('div', 'troubleshooting-actions');
    for (const id of ['btnRestart', 'btnRefreshPreflight']) {
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

  function ensureStatusObserver() {
    const source = el('proServiceStateText') || el('pillTxt');
    if (!source || source.dataset.proComposerObserved === '1') return;
    source.dataset.proComposerObserved = '1';
    statusObserver = new MutationObserver(() => {
      if (!serviceIsRunning()) restartRequired = false;
      syncHeaderStatus();
      syncPrimaryAction();
    });
    statusObserver.observe(source, { childList: true, subtree: true, characterData: true });
  }

  function reconcile() {
    reconcileQueued = false;
    try {
      if (!isAdvancedMode()) {
        restoreBasicPresentation();
        return;
      }
      enforceNavigation();
      if (!prerequisitesReady()) {
        setStage('waiting-for-base');
        return;
      }
      const shell = ensureWorkflowShell();
      if (!shell) {
        setStage('waiting-for-base');
        return;
      }
      const guidedReady = rehydrateWorkflow(shell);
      const qualityReady = ensureConnectionQuality(shell);
      const troubleshootingReady = ensureTroubleshooting();
      ensureStatusObserver();
      if (guidedReady && qualityReady && troubleshootingReady) {
        setStage('ready');
      } else {
        // A failed pass may leave no observable mutation behind, so the
        // observer alone cannot guarantee another attempt.
        setStage('composing');
        scheduleComposeRetry();
      }
    } catch (error) {
      const message = String(error && error.message ? error.message : error);
      setStage('error', message);
      console.error('VRhotspot Pro composer failed.', error);
    }
  }

  function scheduleReconcile() {
    if (reconcileQueued) return;
    reconcileQueued = true;
    window.setTimeout(reconcile, 0);
  }

  function scheduleComposeRetry() {
    if (composeRetryTimer) return;
    composeRetryTimer = window.setTimeout(() => {
      composeRetryTimer = null;
      scheduleReconcile();
    }, 150);
  }

  function start() {
    const observer = new MutationObserver(scheduleReconcile);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-auth-state', 'data-ui-mode'],
    });
    window.addEventListener('pageshow', scheduleReconcile);
    window.addEventListener('load', scheduleReconcile, { once: true });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) scheduleReconcile();
    });
    scheduleReconcile();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
