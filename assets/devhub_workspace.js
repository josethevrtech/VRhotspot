(function addDeveloperHubWorkspace() {
  'use strict';

  const AUTO_REFRESH_MS = 5000;
  const VIEW_KEY = 'vrhs_devhub_workspace_view';
  const VIEW_NAMES = ['device', 'apps', 'connection', 'tools'];
  let refreshTimer = null;
  let activityTimer = null;
  let syncQueued = false;

  function el(id) {
    return document.getElementById(id);
  }

  function hubIsActive() {
    const pane = el('tab-devhub');
    return !!(pane && pane.classList.contains('active') && !document.hidden);
  }

  function cardByTitle(grid, title) {
    return Array.from(grid.children).find((node) => {
      const heading = node.querySelector('.card-header h2');
      return heading && heading.textContent.trim() === title;
    }) || null;
  }

  function normalizedModel(value) {
    return String(value || '')
      .replace(/_/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function selectedDeviceSnapshot() {
    const serial = String((el('devhubSelectedDevice') || {}).textContent || '').trim();
    const selectedRow = document.querySelector('#devhubDeviceList .devhub-list-item.selected');
    const meta = selectedRow
      ? String((selectedRow.querySelector('.devhub-list-meta') || {}).textContent || '').trim()
      : '';
    const state = (meta.split('·')[0] || '').trim().toLowerCase();
    const modelMatch = meta.match(/(?:^|·)\s*model\s+([^·]+)/i);
    const model = normalizedModel(modelMatch ? modelMatch[1] : '');
    const transport = serial && serial.includes(':')
      ? 'Wireless ADB'
      : (serial ? 'USB ADB' : 'No transport');
    const connected = state === 'device';
    return {
      serial: serial && serial !== 'No device selected' ? serial : '',
      model: model || (serial ? 'Android XR headset' : 'No headset selected'),
      state: state || (serial ? 'unknown' : 'offline'),
      transport,
      connected,
    };
  }

  function setText(id, value) {
    const node = el(id);
    if (!node) return;
    const rendered = String(value || '');
    if (node.textContent !== rendered) node.textContent = rendered;
  }

  function updateDeviceBar() {
    const device = selectedDeviceSnapshot();
    const stateDot = el('devhubWorkspaceDeviceState');
    if (stateDot) {
      stateDot.classList.remove('online', 'offline', 'unauthorized');
      if (device.connected) stateDot.classList.add('online');
      else if (device.state === 'unauthorized') stateDot.classList.add('unauthorized');
      else stateDot.classList.add('offline');
    }

    setText('devhubWorkspaceDeviceName', device.model);
    setText(
      'devhubWorkspaceDeviceSerial',
      device.serial || 'Connect or select a headset to begin',
    );
    setText('devhubWorkspaceTransport', device.transport);
    setText(
      'devhubWorkspaceState',
      device.connected ? 'Connected' : (device.state || 'Offline'),
    );

    const source = String((el('devhubToolSource') || {}).textContent || '').trim();
    const runtime = source && source !== '--' ? `${source} ADB` : 'ADB unavailable';
    setText('devhubWorkspaceRuntime', runtime);
    setText('devhubWorkspaceUpdated', `Updated ${new Date().toLocaleTimeString()}`);
  }

  function shouldSuppressActivity(message) {
    const value = String(message || '').trim();
    return !value
      || value.startsWith('Ready.')
      || value.startsWith('Refreshing Developer Hub state')
      || value.startsWith('Open the Hub or refresh');
  }

  function syncActivity() {
    const source = el('devhubFeedback');
    const activity = el('devhubWorkspaceActivity');
    if (!source || !activity) return;

    const message = source.textContent.trim();
    const state = source.dataset.state || 'idle';
    if (activity.textContent !== message) activity.textContent = message;
    activity.dataset.state = state;

    if (shouldSuppressActivity(message)) {
      activity.classList.remove('visible');
      return;
    }

    activity.classList.add('visible');
    if (activityTimer) window.clearTimeout(activityTimer);
    if (state === 'success') {
      activityTimer = window.setTimeout(
        () => activity.classList.remove('visible'),
        4500,
      );
    }
  }

  function requestRefresh() {
    if (!hubIsActive()) return;
    const refresh = el('devhubRefresh');
    if (refresh && !refresh.disabled) refresh.click();
  }

  function loadPackagesWhenUseful() {
    const serial = selectedDeviceSnapshot().serial;
    const list = el('devhubPackageList');
    const load = el('devhubLoadPackages');
    if (!serial || !list || !load || load.disabled) return;
    if (list.querySelector('.devhub-list-item')) return;
    load.click();
  }

  function saveView(name) {
    try { sessionStorage.setItem(VIEW_KEY, name); } catch { }
  }

  function readView() {
    try {
      const value = sessionStorage.getItem(VIEW_KEY);
      return VIEW_NAMES.includes(value) ? value : 'device';
    } catch {
      return 'device';
    }
  }

  function setView(name, options = {}) {
    const next = VIEW_NAMES.includes(name) ? name : 'device';
    saveView(next);

    document.querySelectorAll('.devhub-workspace-tab').forEach((button) => {
      const active = button.dataset.view === next;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
    });

    document.querySelectorAll('.devhub-workspace-panel').forEach((panel) => {
      panel.hidden = panel.dataset.view !== next;
    });

    if (next === 'apps') loadPackagesWhenUseful();
    if (options.focusPicker) {
      window.setTimeout(() => {
        const choose = document.querySelector('#devhubApkPicker .btn');
        if (choose) choose.focus();
      }, 0);
    }
  }

  function workspaceTab(name, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'devhub-workspace-tab';
    button.dataset.view = name;
    button.setAttribute('role', 'tab');
    button.textContent = label;
    button.addEventListener('click', () => setView(name));
    return button;
  }

  function quickAction(title, detail, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'devhub-quick-action';
    const heading = document.createElement('strong');
    heading.textContent = title;
    const copy = document.createElement('span');
    copy.textContent = detail;
    button.append(heading, copy);
    button.addEventListener('click', action);
    return button;
  }

  function buildOverviewCard() {
    const section = document.createElement('section');
    section.className = 'card';
    const header = document.createElement('div');
    header.className = 'card-header';
    const title = document.createElement('h2');
    title.textContent = 'Quick Actions';
    header.appendChild(title);

    const body = document.createElement('div');
    body.className = 'card-body';
    const actions = document.createElement('div');
    actions.className = 'devhub-quick-actions';
    actions.append(
      quickAction(
        'Install APK',
        'Choose a local build and send it directly to the selected headset.',
        () => setView('apps', { focusPicker: true }),
      ),
      quickAction(
        'Manage Apps',
        'Inspect installed packages, launch builds, stop them, or uninstall them.',
        () => setView('apps'),
      ),
      quickAction(
        'Connection',
        'Manage wireless ADB, pairing, discovery, and connected devices.',
        () => setView('connection'),
      ),
      quickAction(
        'Developer Tools',
        'Inspect the ADB runtime and managed Android Platform-Tools.',
        () => setView('tools'),
      ),
    );
    body.appendChild(actions);
    section.append(header, body);
    return section;
  }

  function panel(name) {
    const node = document.createElement('div');
    node.className = 'devhub-workspace-panel';
    node.dataset.view = name;
    node.setAttribute('role', 'tabpanel');
    const grid = document.createElement('div');
    grid.className = 'devhub-workspace-grid';
    node.appendChild(grid);
    return { node, grid };
  }

  function scheduleSourceSync() {
    if (syncQueued) return;
    syncQueued = true;
    window.requestAnimationFrame(() => {
      syncQueued = false;
      updateDeviceBar();
      syncActivity();
    });
  }

  function observeSources(feedback) {
    const observer = new MutationObserver(scheduleSourceSync);
    const sources = [
      el('devhubSelectedDevice'),
      el('devhubDeviceList'),
      el('devhubToolSource'),
      feedback,
    ].filter(Boolean);

    for (const source of sources) {
      observer.observe(source, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['class', 'data-state'],
      });
    }
  }

  function inject() {
    if (el('devhubWorkspace')) return true;

    const page = document.querySelector('#tab-devhub .devhub-page');
    const originalGrid = page && page.querySelector('.devhub-grid');
    const feedback = el('devhubFeedback');
    if (!page || !originalGrid || !feedback) return false;

    const cards = {
      tools: cardByTitle(originalGrid, 'Development Tools'),
      discovery: cardByTitle(originalGrid, 'Network Discovery'),
      devices: cardByTitle(originalGrid, 'ADB Devices'),
      pairing: cardByTitle(originalGrid, 'Wireless Pairing'),
      connect: cardByTitle(originalGrid, 'Connect Headset'),
      apk: cardByTitle(originalGrid, 'APK Deployment'),
      installed: cardByTitle(originalGrid, 'Installed Applications'),
      control: cardByTitle(originalGrid, 'Application Control'),
    };
    if (Object.values(cards).some((card) => !card)) return false;

    const workspace = document.createElement('div');
    workspace.id = 'devhubWorkspace';
    workspace.className = 'devhub-workspace';

    const deviceBar = document.createElement('div');
    deviceBar.className = 'devhub-device-bar';
    const identity = document.createElement('div');
    identity.className = 'devhub-device-identity';
    const state = document.createElement('span');
    state.id = 'devhubWorkspaceDeviceState';
    state.className = 'devhub-device-state offline';
    const copy = document.createElement('div');
    copy.className = 'devhub-device-copy';
    const name = document.createElement('div');
    name.id = 'devhubWorkspaceDeviceName';
    name.className = 'devhub-device-name';
    name.textContent = 'No headset selected';
    const serial = document.createElement('div');
    serial.id = 'devhubWorkspaceDeviceSerial';
    serial.className = 'devhub-device-serial';
    serial.textContent = 'Connect or select a headset to begin';
    copy.append(name, serial);
    identity.append(state, copy);

    const badges = document.createElement('div');
    badges.className = 'devhub-device-badges';
    badges.innerHTML = `
      <span id="devhubWorkspaceState" class="devhub-device-badge accent">Offline</span>
      <span id="devhubWorkspaceTransport" class="devhub-device-badge">No transport</span>
      <span id="devhubWorkspaceRuntime" class="devhub-device-badge">ADB unavailable</span>`;
    deviceBar.append(identity, badges);

    const tabs = document.createElement('div');
    tabs.className = 'devhub-workspace-tabs';
    tabs.setAttribute('role', 'tablist');
    tabs.append(
      workspaceTab('device', 'Device'),
      workspaceTab('apps', 'Apps'),
      workspaceTab('connection', 'Connection'),
      workspaceTab('tools', 'Tools'),
    );

    const activity = document.createElement('div');
    activity.id = 'devhubWorkspaceActivity';
    activity.className = 'devhub-activity';
    activity.setAttribute('role', 'status');
    activity.setAttribute('aria-live', 'polite');

    const panels = {
      device: panel('device'),
      apps: panel('apps'),
      connection: panel('connection'),
      tools: panel('tools'),
    };

    panels.device.grid.append(buildOverviewCard(), cards.devices);
    panels.apps.grid.append(cards.apk, cards.installed, cards.control);
    panels.connection.grid.append(cards.discovery, cards.pairing, cards.connect);
    panels.tools.grid.append(cards.tools);

    const meta = document.createElement('div');
    meta.className = 'devhub-workspace-meta';
    const refreshNote = document.createElement('span');
    refreshNote.textContent = 'Device state refreshes automatically while this tab is open.';
    const updated = document.createElement('span');
    updated.id = 'devhubWorkspaceUpdated';
    updated.textContent = 'Waiting for device state';
    meta.append(refreshNote, updated);

    workspace.append(
      deviceBar,
      tabs,
      activity,
      panels.device.node,
      panels.apps.node,
      panels.connection.node,
      panels.tools.node,
      meta,
    );

    page.insertBefore(workspace, feedback);
    originalGrid.remove();
    const refreshButton = el('devhubRefresh');
    if (refreshButton) {
      refreshButton.hidden = true;
      refreshButton.setAttribute('aria-hidden', 'true');
      refreshButton.tabIndex = -1;
    }

    setView(readView());
    updateDeviceBar();
    syncActivity();
    observeSources(feedback);

    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(requestRefresh, AUTO_REFRESH_MS);
    window.addEventListener('focus', requestRefresh);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) requestRefresh();
    });

    return true;
  }

  function start() {
    if (inject()) return;
    const startupObserver = new MutationObserver(() => {
      if (inject()) startupObserver.disconnect();
    });
    startupObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
