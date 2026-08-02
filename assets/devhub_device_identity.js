(function addDeveloperHubDeviceIdentityPrivacy() {
  'use strict';

  const HIDDEN_VALUE = 'Hidden by Privacy Mode';
  const EMPTY_SERIALS = new Set(['', '--', 'no device selected', 'no headset selected']);
  let syncQueued = false;
  let tabObserver = null;

  function el(id) { return document.getElementById(id); }

  function normalized(value) {
    return String(value || '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function normalizedSerial(value) {
    const serial = String(value || '').trim();
    return EMPTY_SERIALS.has(serial.toLowerCase()) ? '' : serial;
  }

  function privacyEnabled() {
    const privacy = el('privacyMode');
    return !!(privacy && privacy.checked);
  }

  function modelFromMeta(metaText) {
    const match = String(metaText || '').match(/(?:^|·)\s*model\s+([^·]+)/i);
    return normalized(match ? match[1] : '');
  }

  function stateFromMeta(metaText) {
    return normalized(String(metaText || '').split('·')[0]) || 'ADB device';
  }

  function prepareDeviceRows() {
    document.querySelectorAll('#devhubDeviceList .devhub-list-item').forEach((row) => {
      const title = row.querySelector('.devhub-list-title');
      const meta = row.querySelector('.devhub-list-meta');
      if (!title || !meta) return;

      if (row.dataset.devhubIdentityReady !== '1') {
        const serial = normalizedSerial(title.textContent);
        const originalMeta = meta.textContent || '';
        const model = modelFromMeta(originalMeta) || 'Android XR headset';
        const state = stateFromMeta(originalMeta);

        row.dataset.devhubIdentityReady = '1';
        row.dataset.devhubSerial = serial;
        row.dataset.devhubModel = model;
        row.dataset.devhubState = state;

        title.textContent = model;
        title.classList.add('devhub-device-model');

        const stateNode = document.createElement('span');
        stateNode.className = 'devhub-device-state';
        stateNode.textContent = state;

        const addressNode = document.createElement('span');
        addressNode.className = 'devhub-device-address devhub-sensitive-identifier';
        addressNode.textContent = serial;

        meta.replaceChildren(stateNode, addressNode);
        meta.classList.add('devhub-device-identity-meta');
      }
    });
  }

  function selectedRow() {
    return document.querySelector('#devhubDeviceList .devhub-list-item.selected');
  }

  function selectedIdentity() {
    const row = selectedRow();
    const serial = normalizedSerial((el('devhubSelectedDevice') || {}).textContent);
    const fallbackModel = normalized((el('devhubWorkspaceDeviceName') || {}).textContent);
    return {
      serial,
      model: normalized(row && row.dataset.devhubModel)
        || fallbackModel
        || (serial ? 'Android XR headset' : 'No headset selected'),
    };
  }

  function ensureSelectedDisplay() {
    const raw = el('devhubSelectedDevice');
    if (!raw || !raw.parentNode) return null;

    raw.classList.add('devhub-raw-device-serial');

    let display = el('devhubSelectedDeviceIdentity');
    if (!display) {
      display = document.createElement('div');
      display.id = 'devhubSelectedDeviceIdentity';
      display.className = 'devhub-selected-device-display';

      const name = document.createElement('div');
      name.id = 'devhubSelectedDeviceName';
      name.className = 'devhub-selected-device-name';

      const address = document.createElement('div');
      address.id = 'devhubSelectedDeviceAddress';
      address.className = 'devhub-selected-device-address devhub-sensitive-identifier';

      display.append(name, address);
      raw.parentNode.insertBefore(display, raw.nextSibling);
    }
    return display;
  }

  function setText(id, value) {
    const node = el(id);
    if (node && node.textContent !== value) node.textContent = value;
  }

  function syncSelectedDisplay() {
    const display = ensureSelectedDisplay();
    if (!display) return;

    const identity = selectedIdentity();
    setText('devhubSelectedDeviceName', identity.model);
    setText('devhubSelectedDeviceAddress', identity.serial);
    display.classList.toggle('empty', !identity.serial);
  }

  function syncTopIdentity(privacy) {
    const serial = el('devhubWorkspaceDeviceSerial');
    if (!serial) return;
    serial.classList.add('devhub-sensitive-identifier');
    serial.hidden = privacy;
  }

  function syncOverviewIp(privacy) {
    const ip = el('devhubOverviewIp');
    if (!ip) return;

    const value = String(ip.textContent || '').trim();
    if (privacy) {
      if (value && value !== HIDDEN_VALUE && value !== 'Not reported' && value !== '--') {
        ip.dataset.devhubActualValue = value;
      }
      if (value !== HIDDEN_VALUE) ip.textContent = HIDDEN_VALUE;
      return;
    }

    if (value === HIDDEN_VALUE && ip.dataset.devhubActualValue) {
      ip.textContent = ip.dataset.devhubActualValue;
    }
  }

  function bindPrivacyToggle() {
    const privacy = el('privacyMode');
    if (!privacy || privacy.dataset.devhubIdentityBound === '1') return;
    privacy.dataset.devhubIdentityBound = '1';
    privacy.addEventListener('change', scheduleSync);
  }

  function sync() {
    const privacy = privacyEnabled();
    document.documentElement.classList.toggle('devhub-privacy-active', privacy);
    bindPrivacyToggle();
    prepareDeviceRows();
    syncSelectedDisplay();
    syncTopIdentity(privacy);
    syncOverviewIp(privacy);
  }

  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    window.requestAnimationFrame(() => {
      syncQueued = false;
      sync();
    });
  }

  function observeTab() {
    const tab = el('tab-devhub');
    if (!tab || tabObserver) return false;

    tabObserver = new MutationObserver(scheduleSync);
    tabObserver.observe(tab, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class', 'hidden'],
    });
    scheduleSync();
    return true;
  }

  function start() {
    if (observeTab()) return;
    const startupObserver = new MutationObserver(() => {
      if (observeTab()) startupObserver.disconnect();
    });
    startupObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
