(function addDeveloperHubDeviceIdentityPrivacy() {
  'use strict';

  const HIDDEN_VALUE = 'Hidden by Privacy Mode';
  const UPLOAD_PATH = '/v1/devbridge/adb/install-upload';
  const EMPTY_SERIALS = new Set(['', '--', 'no device selected', 'no headset selected']);
  let syncQueued = false;
  let tabObserver = null;
  let lastDeploymentAction = '';

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

  function transportForSerial(serial) {
    const value = normalizedSerial(serial);
    if (!value) return '';
    return value.includes(':') ? 'Wireless' : 'Wired';
  }

  function identityLabel(model, serial) {
    const name = normalized(model) || 'Android XR headset';
    const transport = transportForSerial(serial);
    return transport ? `${name} (${transport})` : name;
  }

  function modelFromMeta(metaText) {
    const match = String(metaText || '').match(/(?:^|·)\s*model\s+([^·]+)/i);
    return normalized(match ? match[1] : '');
  }

  function stateFromMeta(metaText) {
    return normalized(String(metaText || '').split('·')[0]) || 'ADB device';
  }

  function infoTip(text) {
    const tip = document.createElement('span');
    tip.className = 'tip devhub-info-tip';
    tip.textContent = 'ⓘ';
    tip.setAttribute('data-tip', text);
    tip.setAttribute('aria-label', text);
    tip.setAttribute('tabindex', '0');
    return tip;
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
        row.dataset.devhubTransport = transportForSerial(serial);

        title.classList.add('devhub-device-model');

        const stateNode = document.createElement('span');
        stateNode.className = 'devhub-device-state';
        stateNode.textContent = state;

        const modelSource = document.createElement('span');
        modelSource.className = 'devhub-device-model-source';
        modelSource.textContent = ` · model ${model}`;

        const addressNode = document.createElement('span');
        addressNode.className = 'devhub-device-address devhub-sensitive-identifier';
        addressNode.textContent = ` · ${serial}`;

        meta.replaceChildren(stateNode, modelSource, addressNode);
        meta.classList.add('devhub-device-identity-meta');
      }

      const serial = normalizedSerial(row.dataset.devhubSerial);
      const model = normalized(row.dataset.devhubModel) || 'Android XR headset';
      const label = identityLabel(model, serial);
      if (title.textContent !== label) title.textContent = label;
    });
  }

  function selectedRow() {
    return document.querySelector('#devhubDeviceList .devhub-list-item.selected');
  }

  function selectedIdentity() {
    const row = selectedRow();
    const serial = normalizedSerial((el('devhubSelectedDevice') || {}).textContent);
    const fallbackModel = normalized((el('devhubWorkspaceDeviceName') || {}).textContent);
    const model = normalized(row && row.dataset.devhubModel)
      || fallbackModel
      || (serial ? 'Android XR headset' : 'No headset selected');
    return {
      serial,
      model,
      transport: transportForSerial(serial),
      label: serial ? identityLabel(model, serial) : 'No headset selected',
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

  function ensureTargetDisplay() {
    const raw = el('devhubPackageSerial');
    if (!raw || !raw.parentNode) return null;

    raw.classList.add('devhub-raw-device-serial');
    raw.setAttribute('aria-hidden', 'true');
    const label = document.querySelector('label[for="devhubPackageSerial"]');
    if (label && label.textContent !== 'Target headset') label.textContent = 'Target headset';

    let display = el('devhubTargetDeviceIdentity');
    if (!display) {
      display = document.createElement('div');
      display.id = 'devhubTargetDeviceIdentity';
      display.className = 'devhub-target-device-display';

      const name = document.createElement('strong');
      name.id = 'devhubTargetDeviceName';
      const address = document.createElement('span');
      address.id = 'devhubTargetDeviceAddress';
      address.className = 'devhub-sensitive-identifier';
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
    setText('devhubSelectedDeviceName', identity.label);
    setText('devhubSelectedDeviceAddress', identity.serial);
    display.classList.toggle('empty', !identity.serial);
  }

  function syncTargetDisplay() {
    const display = ensureTargetDisplay();
    if (!display) return;

    const identity = selectedIdentity();
    setText('devhubTargetDeviceName', identity.label);
    setText('devhubTargetDeviceAddress', identity.serial);
    display.classList.toggle('empty', !identity.serial);
  }

  function syncAppsContext() {
    const list = el('devhubPackageList');
    const body = list && list.closest('.card-body');
    if (!body) return;

    let context = el('devhubAppsDeviceContext');
    if (!context) {
      context = document.createElement('div');
      context.id = 'devhubAppsDeviceContext';
      context.className = 'devhub-apps-device-context';
      const caption = document.createElement('span');
      caption.textContent = 'Headset';
      const value = document.createElement('strong');
      value.id = 'devhubAppsDeviceName';
      context.append(caption, value);
      body.insertBefore(context, body.firstChild);
    }
    setText('devhubAppsDeviceName', selectedIdentity().label);
  }

  function syncInstallControls() {
    const form = el('devhubInstallForm');
    const reinstall = el('devhubReinstall');
    const grant = el('devhubGrantPermissions');
    if (!form || !reinstall || !grant) return;

    reinstall.checked = true;
    const reinstallLabel = reinstall.closest('label');
    if (reinstallLabel) reinstallLabel.classList.add('devhub-install-option-hidden');

    const checks = reinstall.closest('.devhub-checks') || grant.closest('.devhub-checks');
    if (!el('devhubAdvancedInstallOptions')) {
      const details = document.createElement('details');
      details.id = 'devhubAdvancedInstallOptions';
      details.className = 'devhub-wide devhub-advanced-install-options';
      const summary = document.createElement('summary');
      summary.append(
        document.createTextNode('Advanced install options '),
        infoTip('Normal installs should use Android permission prompts. Automatic permission grants are intended for controlled testing and automation.'),
      );
      const body = document.createElement('div');
      body.className = 'devhub-advanced-install-body';
      const grantLabel = grant.closest('label');
      if (grantLabel) body.appendChild(grantLabel);
      details.append(summary, body);
      if (checks && checks.parentNode) checks.parentNode.insertBefore(details, checks);
    }

    if (checks && !checks.querySelector('label:not(.devhub-install-option-hidden)')) {
      checks.classList.add('devhub-install-options-empty');
    }

    const submit = form.querySelector('button[type="submit"]');
    if (submit && !submit.disabled && submit.textContent !== 'Install or update app') {
      submit.textContent = 'Install or update app';
    }
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

  function serialIdentityMap() {
    const identities = new Map();
    document.querySelectorAll('#devhubDeviceList .devhub-list-item').forEach((row) => {
      const serial = normalizedSerial(row.dataset.devhubSerial);
      const model = normalized(row.dataset.devhubModel) || 'Android XR headset';
      if (serial) identities.set(serial, identityLabel(model, serial));
    });
    const selected = selectedIdentity();
    if (selected.serial) identities.set(selected.serial, selected.label);
    return identities;
  }

  function sanitizeFeedback() {
    const node = el('devhubFeedback');
    if (!node) return;
    let message = String(node.textContent || '');
    const original = message;

    const identities = Array.from(serialIdentityMap().entries())
      .sort((left, right) => right[0].length - left[0].length);
    for (const [serial, label] of identities) {
      if (message.includes(serial)) message = message.split(serial).join(label);
    }

    message = message
      .replace(/Loading packages from/gi, 'Loading apps from')
      .replace(/Loaded (\d+) package\(s\) from/gi, 'Loaded $1 app(s) from')
      .replace(/Package inventory failed/gi, 'App inventory failed');

    if (lastDeploymentAction && /^Installed\s+/i.test(message)) {
      if (lastDeploymentAction === 'updated') {
        message = message.replace(/^Installed/i, 'Updated');
      } else if (lastDeploymentAction === 'installed_or_updated') {
        message = message.replace(/^Installed/i, 'Installed or updated');
      }
      lastDeploymentAction = '';
    }

    if (privacyEnabled()) {
      message = message.replace(
        /\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b/g,
        selectedIdentity().serial ? selectedIdentity().label : 'selected headset',
      );
    }

    if (message !== original) node.textContent = message;
  }

  function bindApiCapture() {
    if (typeof window.api !== 'function' || window.api.devhubDeploymentCapture === true) return;
    const original = window.api;
    const wrapped = async function devhubIdentityAwareApi(path, options) {
      const response = await original(path, options);
      if (path === UPLOAD_PATH && response && response.ok) {
        const publicResult = response.json && response.json.data;
        const details = publicResult && publicResult.data;
        lastDeploymentAction = details && typeof details.deployment_action === 'string'
          ? details.deployment_action
          : '';
      }
      return response;
    };
    wrapped.devhubDeploymentCapture = true;
    wrapped.devhubOriginalApi = original;
    window.api = wrapped;
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
    bindApiCapture();
    bindPrivacyToggle();
    prepareDeviceRows();
    syncSelectedDisplay();
    syncTargetDisplay();
    syncAppsContext();
    syncInstallControls();
    syncTopIdentity(privacy);
    syncOverviewIp(privacy);
    sanitizeFeedback();
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
      attributeFilter: ['class', 'hidden', 'value'],
    });
    scheduleSync();
    return true;
  }

  function start() {
    bindApiCapture();
    if (observeTab()) return;
    const startupObserver = new MutationObserver(() => {
      bindApiCapture();
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
