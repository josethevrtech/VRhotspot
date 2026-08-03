window.UI_FIELD_VISIBILITY = {
  ssid: 'basic',
  wpa2_passphrase: 'basic',
  band_preference: 'basic',
  ap_security: 'basic',
  country: 'basic',
  ap_adapter: 'basic',
  enable_internet: 'basic',
  qos_preset: 'basic',
  channel_6g: 'advanced',
  channel_width: 'advanced',
  beacon_interval: 'advanced',
  dtim_period: 'advanced',
  short_guard_interval: 'advanced',
  tx_power: 'advanced',
  channel_auto_select: 'advanced',
  ap_ready_timeout_s: 'advanced',
  fallback_channel_2g: 'advanced',
  optimized_no_virt: 'advanced',
  lan_gateway_ip: 'advanced',
  dhcp_start_ip: 'advanced',
  dhcp_end_ip: 'advanced',
  dhcp_dns: 'advanced',
  wifi_power_save_disable: 'advanced',
  usb_autosuspend_disable: 'advanced',
  cpu_governor_performance: 'advanced',
  sysctl_tuning: 'advanced',
  cpu_affinity: 'advanced',
  irq_affinity: 'advanced',
  interrupt_coalescing: 'advanced',
  tcp_low_latency: 'advanced',
  memory_tuning: 'advanced',
  io_scheduler_optimize: 'advanced',
  telemetry_enable: 'advanced',
  telemetry_interval_s: 'advanced',
  watchdog_enable: 'advanced',
  watchdog_interval_s: 'advanced',
  connection_quality_monitoring: 'advanced',
  auto_channel_switch: 'advanced',
  nat_accel: 'advanced',
  bridge_mode: 'advanced',
  bridge_name: 'advanced',
  bridge_uplink: 'advanced',
  firewalld_enabled: 'advanced',
  firewalld_enable_masquerade: 'advanced',
  firewalld_enable_forward: 'advanced',
  firewalld_cleanup_on_stop: 'advanced',
  firewalld_zone: 'advanced',
  debug: 'advanced',
};

(function removeBasicAdapterReadinessCard() {
  function removeCard() {
    document.querySelector('[data-adapter-readiness-card]')?.remove();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', removeCard, { once: true });
  } else {
    removeCard();
  }
})();

(function loadBasicModeLayout() {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/assets/basic_layout.css';
  document.head.appendChild(stylesheet);
})();

(function loadGuidedBasicModeAssets() {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/assets/basic_guided.css';
  document.head.appendChild(stylesheet);
  const script = document.createElement('script');
  script.src = '/assets/basic_guided.js';
  script.async = false;
  document.head.appendChild(script);
})();

(function loadDeveloperHubAssets() {
  const stylesheets = [
    '/assets/devhub.css',
    '/assets/devhub_upload.css',
    '/assets/devhub_workspace.css',
    '/assets/devhub_device_overview.css',
    '/assets/devhub_device_identity.css',
    '/assets/devhub_connection_wizard.css',
  ];
  const scripts = [
    '/assets/devhub.js',
    '/assets/devhub_upload.js',
    '/assets/devhub_workspace.js',
    '/assets/devhub_device_overview.js',
    '/assets/devhub_device_identity.js',
    '/assets/devhub_connection_wizard.js',
  ];
  for (const href of stylesheets) {
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = href;
    document.head.appendChild(stylesheet);
  }
  for (const src of scripts) {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    document.head.appendChild(script);
  }
})();

(function addManagedPlatformToolsControls() {
  const TERMS_URL = 'https://developer.android.com/studio/terms';

  function feedback(message, state) {
    const node = document.getElementById('devhubFeedback');
    if (!node) return;
    node.textContent = message;
    node.dataset.state = state || 'idle';
  }

  async function toolsRequest(path, body, button) {
    if (button) button.disabled = true;
    feedback(
      path.endsWith('/install')
        ? 'Downloading and installing Android Platform-Tools...'
        : 'Removing VRhotspot-managed Android Platform-Tools...',
      'loading',
    );
    try {
      const response = await api(path, {
        method: 'POST',
        body: JSON.stringify(body || {}),
      });
      if (!response.ok) {
        const code = response.json && response.json.result_code
          ? response.json.result_code
          : `HTTP ${response.status}`;
        feedback(`Managed tools operation failed: ${code}`, 'error');
        return;
      }
      const result = response.json && response.json.data ? response.json.data : {};
      feedback(result.message || 'Managed tools operation completed.', 'success');
      document.getElementById('devhubRefresh')?.click();
    } catch {
      feedback('Managed tools operation could not reach the VRhotspot daemon.', 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function inject() {
    if (document.getElementById('devhubToolsManagement')) return true;
    const toolsGrid = document.querySelector('#tab-devhub .devhub-tools-grid');
    if (!toolsGrid) return false;

    const section = document.createElement('div');
    section.id = 'devhubToolsManagement';
    section.className = 'devhub-span';

    const checks = document.createElement('div');
    checks.className = 'devhub-checks mt-12';
    const label = document.createElement('label');
    const accept = document.createElement('input');
    accept.id = 'devhubAcceptToolsLicense';
    accept.type = 'checkbox';
    const copy = document.createElement('span');
    copy.append('I accept the ');
    const terms = document.createElement('a');
    terms.href = TERMS_URL;
    terms.target = '_blank';
    terms.rel = 'noopener noreferrer';
    terms.textContent = 'Android SDK License Agreement';
    copy.appendChild(terms);
    label.append(accept, copy);
    checks.appendChild(label);

    const actions = document.createElement('div');
    actions.className = 'devhub-actions mt-12';
    const install = document.createElement('button');
    install.id = 'devhubInstallTools';
    install.type = 'button';
    install.className = 'btn primary';
    install.textContent = 'Install Managed ADB';
    const remove = document.createElement('button');
    remove.id = 'devhubRemoveTools';
    remove.type = 'button';
    remove.className = 'btn secondary';
    remove.textContent = 'Remove Managed ADB';
    actions.append(install, remove);

    install.addEventListener('click', () => {
      if (!accept.checked) {
        feedback('Review and accept the Android SDK License Agreement first.', 'error');
        return;
      }
      void toolsRequest('/v1/devbridge/tools/install', { license_accepted: true }, install);
    });
    remove.addEventListener('click', () => {
      void toolsRequest('/v1/devbridge/tools/remove', {}, remove);
    });

    section.append(checks, actions);
    toolsGrid.appendChild(section);
    return true;
  }

  function start() {
    if (inject()) return;
    const observer = new MutationObserver(() => {
      if (inject()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();

(function buildProSetupInformationArchitecture() {
  'use strict';

  const ACTIVE_POLL_MS = '2000';
  const BACKGROUND_POLL_MS = '5000';
  let initialized = false;

  function el(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function addStyles() {
    if (el('proSetupArchitectureStyles')) return;
    const style = make('style');
    style.id = 'proSetupArchitectureStyles';
    style.textContent = `
      body[data-ui-mode="basic"] .basic-guided-tip {
        border-color: var(--accent-primary) !important;
        background: rgba(0, 217, 255, .08) !important;
        color: var(--accent-primary) !important;
        box-shadow: 0 0 0 1px rgba(0, 217, 255, .08);
        opacity: 1 !important;
      }
      body[data-ui-mode="advanced"] .pro-setup-page { width: 100%; }
      body[data-ui-mode="advanced"] .pro-setup-shell {
        display: grid; gap: 24px; width: min(1680px, 100%); margin: 0 auto;
      }
      body[data-ui-mode="advanced"] .pro-service-card {
        width: 100%; padding: 0; overflow: hidden;
        border: 1px solid var(--border-subtle); border-radius: var(--radius-lg);
        background: var(--bg-panel); box-shadow: var(--shadow-sm);
      }
      body[data-ui-mode="advanced"] .pro-service-card > h2 {
        margin: 0; padding: 20px 24px; border-bottom: 1px solid var(--border-subtle);
        background: var(--bg-subtle); color: var(--text-main); font-size: 18px;
        letter-spacing: .055em; text-transform: uppercase;
      }
      body[data-ui-mode="advanced"] .pro-service-inner {
        display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 460px);
        gap: 28px; align-items: center; padding: 28px;
      }
      body[data-ui-mode="advanced"] .pro-service-state-row {
        display: flex; align-items: center; gap: 14px;
      }
      body[data-ui-mode="advanced"] .pro-service-state-dot {
        width: 12px; height: 12px; flex: 0 0 12px; border-radius: 50%;
        background: var(--text-muted);
      }
      body[data-ui-mode="advanced"] .pro-service-state-copy h3 {
        margin: 0; color: var(--text-main); font-size: 28px;
      }
      body[data-ui-mode="advanced"] .pro-service-state-copy p {
        margin: 8px 0 0 26px; color: var(--text-muted); font-size: 15px;
      }
      body[data-ui-mode="advanced"] .pro-service-card[data-service-state="running"] .pro-service-state-dot {
        background: var(--success); box-shadow: 0 0 9px rgba(0, 255, 157, .45);
      }
      body[data-ui-mode="advanced"] .pro-service-card[data-service-state="working"] .pro-service-state-dot {
        background: var(--accent-primary);
      }
      body[data-ui-mode="advanced"] .pro-service-card[data-service-state="error"] .pro-service-state-dot {
        background: var(--danger); box-shadow: 0 0 9px rgba(255, 0, 85, .4);
      }
      body[data-ui-mode="advanced"] .pro-service-actions { display: grid; gap: 12px; }
      body[data-ui-mode="advanced"] .pro-service-primary {
        width: 100%; min-height: 58px; justify-content: center; font-size: 16px !important;
      }
      body[data-ui-mode="advanced"] .pro-service-secondary {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
      }
      body[data-ui-mode="advanced"] .pro-service-secondary .btn {
        min-height: 50px; justify-content: center;
      }
      body[data-ui-mode="advanced"] .pro-service-card #statusPillContainer,
      body[data-ui-mode="advanced"] .pro-service-card #btnStop,
      body[data-ui-mode="advanced"] .pro-service-card .hero-quick-controls {
        display: none !important;
      }
      body[data-ui-mode="advanced"] .pro-service-card .hero-meta,
      body[data-ui-mode="advanced"] .pro-service-card .hero-feedback {
        margin: 0 28px 18px;
      }
      body[data-ui-mode="advanced"] .pro-configuration { display: grid; gap: 18px; }
      body[data-ui-mode="advanced"] .pro-configuration .settings-header {
        position: sticky; top: 12px; z-index: 20; margin: 0; padding: 18px 20px;
        border: 1px solid var(--border-active); border-radius: var(--radius-md);
        background: rgba(12, 14, 29, .96); backdrop-filter: blur(12px);
        box-shadow: var(--shadow-md);
      }
      body[data-ui-mode="advanced"] .pro-config-card,
      body[data-ui-mode="advanced"] .pro-config-details {
        overflow: hidden; border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg); background: var(--bg-panel); box-shadow: var(--shadow-sm);
      }
      body[data-ui-mode="advanced"] .pro-config-card > h3,
      body[data-ui-mode="advanced"] .pro-config-details > summary {
        margin: 0; padding: 18px 22px; border-bottom: 1px solid var(--border-subtle);
        background: var(--bg-subtle); color: var(--text-main); font-size: 16px;
        font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
      }
      body[data-ui-mode="advanced"] .pro-config-details > summary {
        cursor: pointer; list-style: none;
      }
      body[data-ui-mode="advanced"] .pro-config-details > summary::-webkit-details-marker { display: none; }
      body[data-ui-mode="advanced"] .pro-config-details > summary::after {
        content: '+'; float: right; color: var(--accent-primary);
      }
      body[data-ui-mode="advanced"] .pro-config-details[open] > summary::after { content: '−'; }
      body[data-ui-mode="advanced"] .pro-config-body {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px; padding: 22px;
      }
      body[data-ui-mode="advanced"] .pro-config-body > .preset-bar,
      body[data-ui-mode="advanced"] .pro-config-body > .info-banner,
      body[data-ui-mode="advanced"] .pro-config-body > .form-row,
      body[data-ui-mode="advanced"] .pro-config-body > .tog-group { grid-column: 1 / -1; }
      body[data-ui-mode="advanced"] .pro-config-body .form-group,
      body[data-ui-mode="advanced"] .pro-config-body .form-row { margin: 0; }
      body[data-ui-mode="advanced"] #tab-settings { display: none !important; }
      body[data-ui-mode="advanced"] .pro-support-heading {
        margin: 0 0 18px; color: var(--text-main); font-size: 24px;
      }
      body[data-ui-mode="advanced"] .pro-runtime-details,
      body[data-ui-mode="advanced"] .pro-support-bundle-card {
        margin-bottom: 20px; overflow: hidden; border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg); background: var(--bg-panel);
      }
      body[data-ui-mode="advanced"] .pro-runtime-details > summary,
      body[data-ui-mode="advanced"] .pro-support-bundle-card > h2 {
        padding: 18px 22px; border-bottom: 1px solid var(--border-subtle);
        background: var(--bg-subtle); color: var(--text-main); font-size: 16px;
        font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
      }
      body[data-ui-mode="advanced"] .pro-runtime-details > summary {
        cursor: pointer; list-style: none;
      }
      body[data-ui-mode="advanced"] .pro-runtime-details > summary::after {
        content: '+'; float: right; color: var(--accent-primary);
      }
      body[data-ui-mode="advanced"] .pro-runtime-details[open] > summary::after { content: '−'; }
      body[data-ui-mode="advanced"] .pro-runtime-details .debug-details {
        margin: 0; border: 0; border-radius: 0; box-shadow: none;
      }
      body[data-ui-mode="advanced"] .pro-runtime-details .debug-details > .card-header { display: none; }
      body[data-ui-mode="advanced"] .pro-support-bundle-card .support-bundle-action {
        margin: 0; padding: 22px;
      }
      @media (max-width: 1000px) {
        body[data-ui-mode="advanced"] .pro-service-inner,
        body[data-ui-mode="advanced"] .pro-config-body { grid-template-columns: minmax(0, 1fr); }
      }
      @media (max-width: 680px) {
        body[data-ui-mode="advanced"] .pro-service-secondary { grid-template-columns: minmax(0, 1fr); }
        body[data-ui-mode="advanced"] .pro-configuration .settings-header { position: static; }
      }
    `;
    document.head.appendChild(style);
  }

  function setNavLabel(item, icon, label) {
    if (!item) return;
    item.replaceChildren();
    item.append(make('span', 'icon', icon), document.createTextNode(` ${label}`));
  }

  function makeDetails(title, className) {
    const details = make('details', `pro-config-details ${className || ''}`.trim());
    const summary = make('summary', '', title);
    const body = make('div', 'pro-config-body');
    details.append(summary, body);
    return { details, body };
  }

  function moveField(key, target) {
    const node = document.querySelector(`[data-field="${key}"]`);
    if (node && target) target.appendChild(node);
    return node;
  }

  function moveRemaining(group, target) {
    if (!group || !target) return;
    Array.from(group.children).forEach((node) => {
      if (!node.matches('h3')) target.appendChild(node);
    });
  }

  function buildConfiguration(settingsPane) {
    const configuration = make('section', 'pro-configuration');
    configuration.id = 'proHotspotConfiguration';
    const header = settingsPane.querySelector('.settings-header');
    if (header) {
      const heading = header.querySelector('h2');
      if (heading) heading.textContent = 'Hotspot Configuration';
      configuration.appendChild(header);
    }

    const groups = Array.from(settingsPane.querySelectorAll('.settings-group'));
    const wirelessGroup = groups[0] || null;
    const systemGroup = groups[1] || null;
    const preset = settingsPane.querySelector('.preset-bar');

    const essentials = make('section', 'pro-config-card pro-config-essentials');
    essentials.appendChild(make('h3', '', 'Essentials'));
    const essentialsBody = make('div', 'pro-config-body');
    essentials.appendChild(essentialsBody);
    if (preset) essentialsBody.appendChild(preset);
    for (const key of ['ap_adapter', 'ssid', 'wpa2_passphrase', 'qos_preset', 'enable_internet']) {
      moveField(key, essentialsBody);
    }

    const wireless = makeDetails('Wireless', 'pro-config-wireless');
    const network = makeDetails('Network', 'pro-config-network');
    const system = makeDetails('System & Performance', 'pro-config-system');
    moveRemaining(wirelessGroup, wireless.body);

    for (const key of ['lan_gateway_ip', 'dhcp_start_ip', 'nat_accel', 'bridge_mode', 'firewalld_enabled']) {
      const node = document.querySelector(`[data-field="${key}"]`);
      if (!node) continue;
      const row = node.closest('.form-row');
      network.body.appendChild(row || node);
    }

    const supportBundle = settingsPane.querySelector('.support-bundle-action');
    if (supportBundle) supportBundle.dataset.proSupportBundle = '1';
    moveRemaining(systemGroup, system.body);
    configuration.append(essentials, wireless.details, network.details, system.details);
    return { configuration, supportBundle };
  }

  function serviceState(raw) {
    const text = String(raw || 'Loading…').split('|')[0].trim();
    const normalized = text.toLowerCase();
    if (normalized.includes('error') || normalized.includes('failed')) {
      return { name: 'error', label: 'Needs attention', summary: 'The hotspot encountered a problem. Use Repair Network or review Diagnostics.' };
    }
    if (normalized.includes('stopping')) {
      return { name: 'working', label: 'Stopping…', summary: 'VRhotspot is stopping the hotspot.' };
    }
    if (normalized.includes('starting') || normalized.includes('repair') || normalized.includes('restart')) {
      return { name: 'working', label: text, summary: 'VRhotspot is applying the requested operation.' };
    }
    if (normalized.includes('running')) {
      return { name: 'running', label: 'Running', summary: 'The hotspot is active and ready for connected devices.' };
    }
    if (normalized.includes('stopped') || normalized.includes('inactive') || normalized.includes('not running')) {
      return { name: 'stopped', label: 'Stopped', summary: 'The hotspot is not active.' };
    }
    return { name: 'loading', label: text || 'Loading…', summary: 'Checking the hotspot status.' };
  }

  function syncServicePresentation() {
    const card = document.querySelector('.pro-service-card');
    const state = serviceState(el('pillTxt')?.textContent || 'Loading…');
    if (card) card.dataset.serviceState = state.name;
    const title = el('proServiceStateText');
    const summary = el('proServiceStateSummary');
    if (title && title.textContent !== state.label) title.textContent = state.label;
    if (summary && summary.textContent !== state.summary) summary.textContent = state.summary;

    const primary = el('btnStart');
    if (!primary) return;
    primary.disabled = state.name === 'working' || state.name === 'loading';
    primary.classList.remove('primary', 'danger', 'secondary');
    if (state.name === 'running') {
      primary.textContent = 'Stop Hotspot';
      primary.classList.add('danger');
      primary.dataset.proServiceAction = 'stop';
    } else if (state.name === 'working' || state.name === 'loading') {
      primary.textContent = state.name === 'working' ? 'Working…' : 'Checking Status…';
      primary.classList.add('secondary');
      primary.dataset.proServiceAction = 'wait';
    } else {
      primary.textContent = 'Start Hotspot';
      primary.classList.add('primary');
      primary.dataset.proServiceAction = 'start';
    }
  }

  function buildServiceCard(statusCard) {
    statusCard.classList.add('pro-service-card');
    const heading = statusCard.querySelector(':scope > h2');
    if (heading) heading.textContent = 'Hotspot Status';

    const stateCopy = make('div', 'pro-service-state-copy');
    const row = make('div', 'pro-service-state-row');
    const dot = make('span', 'pro-service-state-dot');
    dot.setAttribute('aria-hidden', 'true');
    const title = make('h3', '', 'Loading…');
    title.id = 'proServiceStateText';
    row.append(dot, title);
    const summary = make('p', '', 'Checking the hotspot status.');
    summary.id = 'proServiceStateSummary';
    stateCopy.append(row, summary);

    const actions = make('div', 'pro-service-actions');
    const primary = el('btnStart');
    const restart = el('btnRestart');
    const repair = el('btnRepair');
    const secondary = make('div', 'pro-service-secondary');
    if (primary) {
      primary.classList.add('pro-service-primary');
      actions.appendChild(primary);
    }
    if (restart) secondary.appendChild(restart);
    if (repair) secondary.appendChild(repair);
    actions.appendChild(secondary);

    const inner = make('div', 'pro-service-inner');
    inner.append(stateCopy, actions);
    const meta = statusCard.querySelector('.hero-meta');
    const feedback = Array.from(statusCard.querySelectorAll('.hero-feedback'));
    const controls = statusCard.querySelector('.hero-controls');
    const hiddenControls = make('div', 'pro-service-hidden-controls');
    hiddenControls.hidden = true;
    const stop = el('btnStop');
    const quickControls = statusCard.querySelector('.hero-quick-controls');
    if (stop) hiddenControls.appendChild(stop);
    if (quickControls) hiddenControls.appendChild(quickControls);
    if (controls) controls.remove();
    statusCard.append(inner, hiddenControls);
    if (meta) statusCard.appendChild(meta);
    feedback.forEach((node) => statusCard.appendChild(node));

    const originalPill = el('pillTxt');
    if (originalPill) {
      const observer = new MutationObserver(syncServicePresentation);
      observer.observe(originalPill, { childList: true, subtree: true, characterData: true });
    }
    if (primary) {
      primary.addEventListener('click', (event) => {
        if (primary.dataset.proServiceAction !== 'stop') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const stop = el('btnStop');
        if (stop) stop.click();
      }, true);
    }
    syncServicePresentation();
  }

  function configureAdaptivePolling() {
    const auto = el('autoRefresh');
    const every = el('refreshEvery');
    if (!auto || !every) return;
    const interval = document.hidden ? BACKGROUND_POLL_MS : ACTIVE_POLL_MS;
    try {
      localStorage.setItem('vr_hotspot_auto', '1');
      localStorage.setItem('vr_hotspot_every', interval);
      sessionStorage.setItem('vr_hotspot_auto', '1');
      sessionStorage.setItem('vr_hotspot_every', interval);
    } catch { /* storage is optional */ }
    if (every.value !== interval) every.value = interval;
    if (!auto.checked) auto.checked = true;
    auto.dispatchEvent(new Event('change', { bubbles: true }));
    every.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function moveTelemetrySettings() {
    const telemetryPane = el('tab-telemetry');
    const telemetryToggle = el('telemetry_enable');
    const settingsGroup = telemetryToggle?.closest('.form-group');
    if (!telemetryPane || !settingsGroup) return;
    const interval = document.querySelector('[data-field="telemetry_interval_s"]');
    if (interval && !settingsGroup.contains(interval)) settingsGroup.appendChild(interval);
  }

  function buildSupportAndLogs(logsPane, debugCard, supportBundle) {
    const heading = make('h2', 'pro-support-heading', 'Support & Logs');
    logsPane.prepend(heading);
    if (debugCard) {
      const runtime = make('details', 'pro-runtime-details');
      const summary = make('summary', '', 'Runtime Details');
      runtime.append(summary, debugCard);
      logsPane.insertBefore(runtime, heading.nextSibling);
    }
    if (supportBundle) {
      const card = make('section', 'pro-support-bundle-card');
      card.append(make('h2', '', 'Support Bundle'), supportBundle);
      const anchor = debugCard ? logsPane.querySelector('.pro-runtime-details') : heading;
      anchor.insertAdjacentElement('afterend', card);
    }
  }

  function build() {
    if (initialized) return true;
    const overview = el('tab-overview');
    const settings = el('tab-settings');
    const logs = el('tab-logs');
    const statusCard = overview?.querySelector('.status-hero');
    const debugCard = overview?.querySelector('.debug-details');
    if (!overview || !settings || !logs || !statusCard) return false;

    addStyles();
    const overviewNav = document.querySelector('.nav-item[data-tab="overview"]');
    const settingsNav = document.querySelector('.nav-item[data-tab="settings"]');
    const logsNav = document.querySelector('.nav-item[data-tab="logs"]');
    setNavLabel(overviewNav, '📡', 'Set Up Hotspot');
    setNavLabel(logsNav, '📜', 'Support & Logs');
    if (settingsNav) settingsNav.remove();

    overview.classList.add('pro-setup-page');
    const grid = overview.querySelector('.grid-overview');
    const shell = make('div', 'pro-setup-shell');
    moveTelemetrySettings();
    const { configuration, supportBundle } = buildConfiguration(settings);
    buildServiceCard(statusCard);
    shell.append(statusCard, configuration);
    overview.replaceChildren(shell);
    if (grid && grid.isConnected) grid.remove();

    buildSupportAndLogs(logs, debugCard, supportBundle);
    settings.hidden = true;
    const activeSettings = settings.classList.contains('active');
    if (activeSettings) {
      settings.classList.remove('active');
      overview.classList.add('active');
      settingsNav?.classList.remove('active');
      overviewNav?.classList.add('active');
      if (overviewNav) overviewNav.click();
    }

    configureAdaptivePolling();
    document.addEventListener('visibilitychange', configureAdaptivePolling);
    const bodyObserver = new MutationObserver(() => {
      if (document.body.dataset.authState === 'authenticated') {
        window.setTimeout(configureAdaptivePolling, 0);
      }
    });
    bodyObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-auth-state'],
    });

    initialized = true;
    return true;
  }

  function start() {
    addStyles();
    if (build()) return;
    const observer = new MutationObserver(() => {
      if (build()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
