(function guardAdapterSelectorInteraction() {
  'use strict';

  let originalLoadAdapters = null;
  let originalApplyConfig = null;
  let originalMaybeAutoPickAdapterForBand = null;
  let wrapped = false;
  let interacting = false;
  let userChangedSelection = false;
  let pendingAdapterRefresh = false;
  let pendingConfiguredAdapter = '';
  let releaseTimer = null;
  let retryCount = 0;

  function adapterSelect() {
    return document.getElementById('ap_adapter');
  }

  function adapterInteractionActive() {
    const select = adapterSelect();
    return !!select && (interacting || document.activeElement === select);
  }

  function optionExists(select, value) {
    if (!select || !value) return false;
    return Array.from(select.options).some((option) => option.value === value);
  }

  function selectedBand() {
    const select = document.getElementById('band_preference');
    if (!select) return '';
    if (typeof window.resolveBandPref === 'function') {
      return String(window.resolveBandPref(select.value) || '').toLowerCase();
    }
    return String(select.value || '').toLowerCase();
  }

  function syncAdapterBandHint() {
    const hint = document.getElementById('adapterHint');
    if (!hint) return;

    if (selectedBand() !== '6ghz') {
      hint.replaceChildren();
      hint.hidden = true;
      hint.style.display = 'none';
      return;
    }

    const hasMessage = String(hint.textContent || '').trim() !== '';
    hint.hidden = !hasMessage;
    hint.style.display = hasMessage ? '' : 'none';
  }

  async function flushPendingAdapterWork() {
    if (adapterInteractionActive()) return;

    const select = adapterSelect();
    const refreshNeeded = pendingAdapterRefresh;
    const configuredAdapter = pendingConfiguredAdapter;
    const preserveUserChoice = userChangedSelection;

    pendingAdapterRefresh = false;
    pendingConfiguredAdapter = '';
    userChangedSelection = false;

    if (
      select &&
      !preserveUserChoice &&
      configuredAdapter &&
      select.value !== configuredAdapter &&
      optionExists(select, configuredAdapter)
    ) {
      select.value = configuredAdapter;
    }

    if (refreshNeeded && typeof originalLoadAdapters === 'function') {
      await originalLoadAdapters();
    }

    syncAdapterBandHint();
  }

  function scheduleRelease() {
    if (releaseTimer !== null) window.clearTimeout(releaseTimer);
    releaseTimer = window.setTimeout(() => {
      releaseTimer = null;
      const select = adapterSelect();
      if (select && document.activeElement === select) return;
      interacting = false;
      void flushPendingAdapterWork();
    }, 0);
  }

  function wireInteractionEvents() {
    document.addEventListener('pointerdown', (event) => {
      const select = adapterSelect();
      if (!select || event.target !== select) return;
      interacting = true;
      userChangedSelection = false;
      if (releaseTimer !== null) {
        window.clearTimeout(releaseTimer);
        releaseTimer = null;
      }
    }, true);

    document.addEventListener('focusin', (event) => {
      const select = adapterSelect();
      if (!select || event.target !== select) return;
      interacting = true;
      userChangedSelection = false;
      if (releaseTimer !== null) {
        window.clearTimeout(releaseTimer);
        releaseTimer = null;
      }
    }, true);

    document.addEventListener('change', (event) => {
      const select = adapterSelect();
      if (!select || event.target !== select) return;
      userChangedSelection = true;
    }, true);

    document.addEventListener('focusout', (event) => {
      const select = adapterSelect();
      if (!select || event.target !== select) return;
      scheduleRelease();
    }, true);

    document.addEventListener('keydown', (event) => {
      const select = adapterSelect();
      if (!select || event.target !== select) return;
      if (event.key === 'Escape' || event.key === 'Enter' || event.key === 'Tab') {
        scheduleRelease();
      }
    }, true);

    document.addEventListener('change', (event) => {
      if (event.target?.id === 'band_preference') {
        window.setTimeout(syncAdapterBandHint, 0);
      }
    }, true);
  }

  function installGuards() {
    if (wrapped) return true;
    if (
      typeof window.loadAdapters !== 'function' ||
      typeof window.applyConfig !== 'function' ||
      typeof window.maybeAutoPickAdapterForBand !== 'function'
    ) {
      return false;
    }

    originalLoadAdapters = window.loadAdapters;
    originalApplyConfig = window.applyConfig;
    originalMaybeAutoPickAdapterForBand = window.maybeAutoPickAdapterForBand;

    window.loadAdapters = async function guardedLoadAdapters(...args) {
      if (adapterInteractionActive()) {
        pendingAdapterRefresh = true;
        return undefined;
      }
      const result = await originalLoadAdapters.apply(this, args);
      syncAdapterBandHint();
      return result;
    };

    window.applyConfig = function guardedApplyConfig(config, ...args) {
      if (
        adapterInteractionActive() &&
        config &&
        typeof config === 'object' &&
        config.ap_adapter
      ) {
        pendingConfiguredAdapter = String(config.ap_adapter);
        const protectedConfig = Object.assign({}, config);
        delete protectedConfig.ap_adapter;
        const result = originalApplyConfig.call(this, protectedConfig, ...args);
        syncAdapterBandHint();
        return result;
      }

      const result = originalApplyConfig.call(this, config, ...args);
      syncAdapterBandHint();
      return result;
    };

    window.maybeAutoPickAdapterForBand = function guardedAutoPick(...args) {
      if (adapterInteractionActive()) {
        pendingAdapterRefresh = true;
        return undefined;
      }
      const result = originalMaybeAutoPickAdapterForBand.apply(this, args);
      syncAdapterBandHint();
      return result;
    };

    wireInteractionEvents();
    wrapped = true;
    syncAdapterBandHint();
    document.body.dataset.adapterInteractionGuard = 'ready';
    return true;
  }

  function start() {
    if (installGuards()) return;
    retryCount += 1;
    if (retryCount >= 100) {
      document.body.dataset.adapterInteractionGuard = 'unavailable';
      return;
    }
    window.setTimeout(start, 20);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();

(function organizeStepThreeAndRepairQr() {
  'use strict';

  const QR_BUTTON_IDS = new Set(['btnShowQr', 'btnShowQrBasic']);
  let organizing = false;
  let qrBusy = false;
  let retryCount = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function isProMode() {
    return document.body?.dataset.uiMode === 'advanced';
  }

  function ensureCurrentStylesheet() {
    const stylesheet = document.querySelector(
      'link[data-pro-guided-styles="authoritative"]',
    );
    if (!stylesheet) return;
    const href = '/assets/pro_guided_authoritative.css?v=148-step3-qr-1';
    if (!stylesheet.getAttribute('href')?.includes('148-step3-qr-1')) {
      stylesheet.href = href;
    }
  }

  function field(key) {
    return document.querySelector(`[data-field="${key}"]`);
  }

  function setLabel(key, text) {
    const wrapper = field(key);
    const label = wrapper?.querySelector(
      ':scope > label, :scope > .field-label-with-tip > label',
    );
    if (label && label.textContent !== text) label.textContent = text;
  }

  function organizePasswordField() {
    const wrapper = field('wpa2_passphrase');
    const input = byId('wpa2_passphrase');
    const reveal = byId('btnRevealPass');
    const qr = byId('btnShowQr');
    const hint = byId('passHint');
    if (!wrapper || !input || !reveal || !qr) return false;

    let row = wrapper.querySelector(':scope > .pro-password-row');
    if (!row) {
      row = document.createElement('div');
      row.className = 'pro-password-row pro-runtime-wrapper';
      const label = wrapper.querySelector(
        ':scope > label, :scope > .field-label-with-tip',
      );
      if (label?.nextSibling) wrapper.insertBefore(row, label.nextSibling);
      else wrapper.prepend(row);
    }

    reveal.type = 'button';
    reveal.classList.add('btn', 'icon-only', 'pro-password-action');
    reveal.title = 'Show or hide password';
    reveal.setAttribute('aria-label', 'Show or hide password');

    qr.type = 'button';
    qr.className = 'btn icon-only pro-password-action';
    qr.textContent = 'QR';
    qr.title = 'Show connection QR code';
    qr.setAttribute('aria-label', 'Show connection QR code');

    for (const control of [input, reveal, qr]) {
      if (control.parentNode !== row) row.appendChild(control);
    }

    if (hint && hint.parentNode !== wrapper) wrapper.appendChild(hint);
    wrapper.classList.add('pro-password-field', 'pro-step3-field');
    return true;
  }

  function organizeStepThree() {
    if (organizing || !isProMode()) return false;
    const container = document.querySelector('#proStepHotspot .pro-hotspot-fields');
    if (!container) return false;

    organizing = true;
    try {
      ensureCurrentStylesheet();
      const order = [
        'ssid',
        'wpa2_passphrase',
        'band_preference',
        'ap_security',
        'country',
        'enable_internet',
      ];
      const wrappers = order.map(field);
      if (wrappers.some((wrapper) => !wrapper)) return false;

      for (const wrapper of wrappers) {
        wrapper.classList.add('pro-step3-field');
        if (wrapper.parentNode !== container) container.appendChild(wrapper);
      }
      wrappers.forEach((wrapper) => container.appendChild(wrapper));

      setLabel('ssid', 'Hotspot name (SSID)');
      setLabel('wpa2_passphrase', 'Password');
      setLabel('band_preference', 'Band');
      setLabel('ap_security', 'Security');
      setLabel('country', 'Country');
      organizePasswordField();

      container.dataset.step3Layout = 'organized';
      document.body.dataset.proStep3Runtime = 'ready';
      return true;
    } finally {
      organizing = false;
    }
  }

  function responsePayload(response) {
    let data = response?.json;
    if (data && typeof data === 'object' && data.data !== undefined) {
      data = data.data;
    }
    if (data && typeof data === 'object' && data.data !== undefined) {
      data = data.data;
    }
    return data && typeof data === 'object' ? data : {};
  }

  async function request(path, options = {}) {
    if (typeof window.api === 'function') {
      return window.api(path, options);
    }
    if (typeof api === 'function') {
      return api(path, options);
    }

    const response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
    });
    const text = await response.text();
    let json = {};
    try {
      json = text ? JSON.parse(text) : {};
    } catch {
      json = {};
    }
    return { ok: response.ok, status: response.status, json };
  }

  function typedPassphrase() {
    for (const id of ['wpa2_passphrase', 'wpa2_passphrase_basic']) {
      const value = String(byId(id)?.value || '').trim();
      if (value) return value;
    }
    return '';
  }

  async function qrCredentials() {
    let ssid = String(byId('ssid')?.value || '').trim();
    let passphrase = typedPassphrase();

    if (!ssid) {
      const configResponse = await request('/v1/config');
      if (configResponse.ok) {
        const config = responsePayload(configResponse);
        ssid = String(config.ssid || '').trim();
      }
    }

    if (!passphrase) {
      const revealResponse = await request('/v1/config/reveal_passphrase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      });
      if (revealResponse.ok) {
        const reveal = responsePayload(revealResponse);
        passphrase = String(reveal.wpa2_passphrase || '').trim();
      }
    }

    return { ssid, passphrase };
  }

  function escapeWifiValue(value) {
    return String(value).replace(/([\\;,:])/g, '\\$1');
  }

  function setQrToggleState(open) {
    for (const id of QR_BUTTON_IDS) {
      const button = byId(id);
      if (button) button.setAttribute('aria-pressed', open ? 'true' : 'false');
    }
  }

  function closeQr() {
    const modal = byId('qrModal');
    if (!modal) return;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    setQrToggleState(false);
  }

  function feedback(message) {
    if (typeof window.setMsg === 'function') {
      window.setMsg(message, 'dangerText');
      return;
    }
    if (typeof setMsg === 'function') {
      setMsg(message, 'dangerText');
      return;
    }
    const target = isProMode() ? byId('msg') : byId('msgBasic');
    if (target) {
      target.textContent = message;
      target.classList.add('dangerText');
    }
  }

  async function waitForQrLibrary() {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (typeof window.QRCode === 'function' || typeof QRCode === 'function') {
        return true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 25));
    }
    return false;
  }

  async function openQr() {
    if (qrBusy) return;
    const modal = byId('qrModal');
    const placeholder = byId('qrPlaceholder');
    const raw = byId('qrSsidRaw');
    if (!modal || !placeholder || !raw) return;

    qrBusy = true;
    for (const id of QR_BUTTON_IDS) {
      const button = byId(id);
      if (button) button.disabled = true;
    }

    try {
      const { ssid, passphrase } = await qrCredentials();
      if (!ssid) {
        feedback('Enter or save a hotspot name before creating a QR code.');
        return;
      }
      if (!passphrase) {
        feedback('Enter or save a password before creating a QR code.');
        return;
      }
      if (!(await waitForQrLibrary())) {
        feedback('The QR code library did not load. Refresh the page and try again.');
        return;
      }

      const wifi = `WIFI:S:${escapeWifiValue(ssid)};T:WPA;P:${escapeWifiValue(passphrase)};;`;
      placeholder.replaceChildren();
      raw.textContent = `SSID: ${ssid}`;

      const QrConstructor = window.QRCode || QRCode;
      const options = {
        text: wifi,
        width: 256,
        height: 256,
        colorDark: '#000000',
        colorLight: '#ffffff',
      };
      if (QrConstructor.CorrectLevel?.M !== undefined) {
        options.correctLevel = QrConstructor.CorrectLevel.M;
      }
      new QrConstructor(placeholder, options);

      modal.style.display = 'flex';
      modal.setAttribute('aria-hidden', 'false');
      setQrToggleState(true);
      byId('btnCloseQr')?.focus();
    } catch (error) {
      console.error('VRhotspot QR generation failed', error);
      feedback('The connection QR code could not be generated.');
    } finally {
      qrBusy = false;
      for (const id of QR_BUTTON_IDS) {
        const button = byId(id);
        if (button) button.disabled = false;
      }
    }
  }

  function handleClick(event) {
    const target = event.target instanceof Element ? event.target : null;
    const button = target?.closest('button');
    if (!button) return;

    if (QR_BUTTON_IDS.has(button.id)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const modal = byId('qrModal');
      const open = modal && modal.style.display && modal.style.display !== 'none';
      if (button.id === 'btnShowQrBasic' && open) closeQr();
      else void openQr();
      return;
    }

    if (button.id === 'btnCloseQr') {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeQr();
    }
  }

  function start() {
    document.addEventListener('click', handleClick, true);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeQr();
    }, true);

    const modal = byId('qrModal');
    modal?.addEventListener('click', (event) => {
      if (event.target === modal) closeQr();
    });

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => (
        mutation.type === 'attributes' && mutation.attributeName === 'data-ui-mode'
      ))) {
        window.setTimeout(organizeStepThree, 0);
      }
    });
    if (document.body) {
      observer.observe(document.body, {
        attributes: true,
        attributeFilter: ['data-ui-mode'],
      });
    }

    function retryOrganization() {
      if (organizeStepThree()) return;
      retryCount += 1;
      if (retryCount < 120) {
        window.setTimeout(retryOrganization, 25);
      }
    }
    retryOrganization();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
