(function addDeveloperHubDeviceOverview() {
  'use strict';

  const ENDPOINT = '/v1/devbridge/adb/device-overview';
  const REFRESH_MS = 15000;
  const EMPTY_SERIALS = new Set(['', '--', 'no device selected', 'no headset selected']);
  let refreshTimer = null;
  let selectedObserver = null;
  let loading = false;
  let activeSerial = '';
  let lastLoadedAt = 0;

  function el(id) {
    return document.getElementById(id);
  }

  function normalizedSerial(value) {
    const serial = String(value || '').trim();
    return EMPTY_SERIALS.has(serial.toLowerCase()) ? '' : serial;
  }

  function selectedSerial() {
    return normalizedSerial((el('devhubSelectedDevice') || {}).textContent);
  }

  function hubIsVisible() {
    const tab = el('tab-devhub');
    return !!(tab && tab.classList.contains('active') && !document.hidden);
  }

  function infoTip(text, extraClass = '') {
    const tip = document.createElement('span');
    tip.className = ['tip', 'devhub-info-tip', extraClass].filter(Boolean).join(' ');
    tip.textContent = 'ⓘ';
    tip.setAttribute('data-tip', text);
    tip.setAttribute('aria-label', text);
    tip.setAttribute('tabindex', '0');
    return tip;
  }

  function metric(label, id, helpText) {
    const item = document.createElement('div');
    item.className = 'devhub-overview-metric';
    const labelRow = document.createElement('div');
    labelRow.className = 'devhub-overview-label';
    const labelText = document.createElement('span');
    labelText.textContent = label;
    labelRow.appendChild(labelText);
    if (helpText) labelRow.appendChild(infoTip(helpText));
    const value = document.createElement('div');
    value.id = id;
    value.className = 'devhub-overview-value';
    value.textContent = '--';
    item.append(labelRow, value);
    return item;
  }

  function findQuickActionsCard() {
    const cards = document.querySelectorAll(
      '#devhubWorkspace .devhub-workspace-panel[data-view="device"] .card',
    );
    return Array.from(cards).find((card) => {
      const title = card.querySelector('.card-header h2');
      return title && title.textContent.trim() === 'Quick Actions';
    }) || null;
  }

  function buildOverview(card) {
    if (!card || card.dataset.overviewReady === '1') return true;
    const header = card.querySelector('.card-header');
    const title = header && header.querySelector('h2');
    const body = card.querySelector('.card-body');
    if (!header || !title || !body) return false;

    card.dataset.overviewReady = '1';
    card.classList.add('devhub-overview-card');
    title.textContent = 'Headset Overview';
    header.appendChild(infoTip(
      'Live read-only details from the selected headset. Values refresh automatically while Developer Hub is open.',
      'devhub-overview-help',
    ));

    const empty = document.createElement('div');
    empty.id = 'devhubOverviewEmpty';
    empty.className = 'devhub-overview-empty';
    empty.textContent = 'Select a connected headset to view device information.';

    const content = document.createElement('div');
    content.id = 'devhubOverviewContent';
    content.className = 'devhub-overview-content';
    content.hidden = true;

    const metrics = document.createElement('div');
    metrics.className = 'devhub-overview-metrics';
    metrics.append(
      metric('Headset battery', 'devhubOverviewBattery', 'Current battery level and charging state.'),
      metric('Storage', 'devhubOverviewStorage', 'Free space on the headset data partition.'),
      metric('Wi-Fi', 'devhubOverviewWifi', 'The network currently reported by Android.'),
      metric('Network quality', 'devhubOverviewSignal', 'Wi-Fi signal strength and negotiated link speed when available.'),
      metric('IP address', 'devhubOverviewIp', 'The current IPv4 address reported by the headset route table.'),
      metric('System', 'devhubOverviewSystem', 'Android release and SDK level.'),
      metric('Build', 'devhubOverviewBuild', 'The headset firmware build identifier.'),
      metric('Uptime', 'devhubOverviewUptime', 'Time since the headset last restarted.'),
    );

    const controllers = document.createElement('section');
    controllers.className = 'devhub-overview-controllers';
    const controllerHeader = document.createElement('div');
    controllerHeader.className = 'devhub-overview-section-title';
    const controllerTitle = document.createElement('h3');
    controllerTitle.textContent = 'Controllers';
    controllerHeader.append(
      controllerTitle,
      infoTip('Controller battery and tracking data is shown when the headset exposes it through ADB.'),
    );
    const controllerList = document.createElement('div');
    controllerList.id = 'devhubOverviewControllers';
    controllerList.className = 'devhub-controller-list';
    controllers.append(controllerHeader, controllerList);

    content.append(metrics, controllers);
    body.replaceChildren(empty, content);
    return true;
  }

  function setValue(id, value, fallback = 'Not reported') {
    const node = el(id);
    if (!node) return;
    const rendered = value === null || value === undefined || value === ''
      ? fallback
      : String(value);
    node.textContent = rendered;
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return null;
    const gib = bytes / (1024 ** 3);
    if (gib >= 10) return `${gib.toFixed(0)} GB`;
    return `${gib.toFixed(1)} GB`;
  }

  function formatUptime(value) {
    let seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return null;
    seconds = Math.floor(seconds);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function renderControllers(data) {
    const container = el('devhubOverviewControllers');
    if (!container) return;
    container.replaceChildren();

    if (!data.controller_service_available) {
      const unavailable = document.createElement('div');
      unavailable.className = 'devhub-overview-inline-empty';
      unavailable.textContent = 'Controller telemetry is not reported by this headset.';
      container.appendChild(unavailable);
      return;
    }

    const controllers = Array.isArray(data.controllers) ? data.controllers : [];
    if (!controllers.length) {
      const empty = document.createElement('div');
      empty.className = 'devhub-overview-inline-empty';
      empty.textContent = 'No paired controllers were reported.';
      container.appendChild(empty);
      return;
    }

    for (const controller of controllers) {
      const row = document.createElement('div');
      row.className = 'devhub-controller-row';

      const identity = document.createElement('div');
      identity.className = 'devhub-controller-identity';
      const name = document.createElement('strong');
      name.textContent = `${controller.side || 'Unknown'} controller`;
      const firmware = document.createElement('span');
      firmware.textContent = controller.firmware
        ? `Firmware ${controller.firmware}`
        : 'Firmware not reported';
      identity.append(name, firmware);

      const facts = document.createElement('div');
      facts.className = 'devhub-controller-facts';
      const battery = controller.battery_percent === null
        || controller.battery_percent === undefined
        ? 'Battery --'
        : `Battery ${controller.battery_percent}%`;
      const tracking = controller.tracking_status
        ? `Tracking ${controller.tracking_status}`
        : 'Tracking not reported';
      const state = controller.external_status || controller.status;
      facts.append(
        Object.assign(document.createElement('span'), { textContent: battery }),
        Object.assign(document.createElement('span'), { textContent: tracking }),
      );
      if (state) {
        facts.appendChild(Object.assign(document.createElement('span'), {
          textContent: `State ${state}`,
        }));
      }

      row.append(identity, facts);
      container.appendChild(row);
    }
  }

  function renderEmpty(message) {
    const empty = el('devhubOverviewEmpty');
    const content = el('devhubOverviewContent');
    if (empty) {
      empty.textContent = message || 'Select a connected headset to view device information.';
      empty.hidden = false;
    }
    if (content) content.hidden = true;
  }

  function renderOverview(data) {
    const empty = el('devhubOverviewEmpty');
    const content = el('devhubOverviewContent');
    if (empty) empty.hidden = true;
    if (content) content.hidden = false;

    const device = data.device || {};
    const battery = data.battery || {};
    const storage = data.storage || {};
    const wifi = data.wifi || {};

    const batteryValue = battery.percent === null || battery.percent === undefined
      ? null
      : `${battery.percent}%${battery.charging ? ' · Charging' : ''}`;
    const free = formatBytes(storage.available_bytes);
    const total = formatBytes(storage.total_bytes);
    const storageValue = free && total ? `${free} free of ${total}` : (free || null);
    const signalParts = [];
    if (wifi.rssi_dbm !== null && wifi.rssi_dbm !== undefined) {
      signalParts.push(`${wifi.rssi_dbm} dBm`);
    }
    if (wifi.link_speed_mbps !== null && wifi.link_speed_mbps !== undefined) {
      signalParts.push(`${wifi.link_speed_mbps} Mbps`);
    }
    const systemParts = [];
    if (device.android_release) systemParts.push(`Android ${device.android_release}`);
    if (device.android_sdk !== null && device.android_sdk !== undefined) {
      systemParts.push(`SDK ${device.android_sdk}`);
    }

    setValue('devhubOverviewBattery', batteryValue);
    setValue('devhubOverviewStorage', storageValue);
    setValue('devhubOverviewWifi', wifi.ssid || (wifi.enabled === false ? 'Wi-Fi disabled' : null));
    setValue('devhubOverviewSignal', signalParts.join(' · '));
    setValue('devhubOverviewIp', wifi.ip_address);
    setValue('devhubOverviewSystem', systemParts.join(' · '));
    setValue('devhubOverviewBuild', device.build);
    setValue('devhubOverviewUptime', formatUptime(data.uptime_seconds));
    renderControllers(data);

    const workspaceName = el('devhubWorkspaceDeviceName');
    if (workspaceName && device.model) workspaceName.textContent = String(device.model);
  }

  function responseCode(response) {
    if (response && response.json) {
      const result = response.json.data;
      if (result && result.result_code) return String(result.result_code);
      if (response.json.result_code) return String(response.json.result_code);
    }
    return response ? `HTTP ${response.status}` : 'request_failed';
  }

  async function refreshOverview(force = false) {
    const serial = selectedSerial();
    if (!serial) {
      activeSerial = '';
      renderEmpty();
      return;
    }
    if (!hubIsVisible() || loading) return;
    const now = Date.now();
    if (!force && serial === activeSerial && now - lastLoadedAt < REFRESH_MS) return;

    activeSerial = serial;
    loading = true;
    const empty = el('devhubOverviewEmpty');
    if (empty && el('devhubOverviewContent') && el('devhubOverviewContent').hidden) {
      empty.textContent = 'Loading headset information...';
    }

    try {
      if (typeof api !== 'function') {
        renderEmpty('The Developer Hub API is unavailable.');
        return;
      }
      const query = new URLSearchParams({ serial });
      const response = await api(`${ENDPOINT}?${query.toString()}`);
      if (!response.ok) {
        renderEmpty(`Headset information unavailable: ${responseCode(response)}`);
        return;
      }
      const operation = response.json && response.json.data ? response.json.data : {};
      const data = operation && operation.data && typeof operation.data === 'object'
        ? operation.data
        : {};
      if (serial !== selectedSerial()) return;
      renderOverview(data);
      lastLoadedAt = Date.now();
    } catch {
      renderEmpty('Unable to read headset information from the daemon.');
    } finally {
      loading = false;
    }
  }

  function wireSelectionObserver() {
    const selected = el('devhubSelectedDevice');
    if (!selected || selectedObserver) return;
    selectedObserver = new MutationObserver(() => {
      lastLoadedAt = 0;
      void refreshOverview(true);
    });
    selectedObserver.observe(selected, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function inject() {
    const card = findQuickActionsCard();
    if (!card) return false;
    if (!buildOverview(card)) return false;
    wireSelectionObserver();
    void refreshOverview(true);

    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(() => void refreshOverview(false), REFRESH_MS);
    window.addEventListener('focus', () => void refreshOverview(false));
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) void refreshOverview(false);
    });
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
