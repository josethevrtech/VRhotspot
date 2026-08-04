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

(function organizeStepThreeLayout() {
  'use strict';

  let organizing = false;
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

    const href = (
      '/assets/pro_guided_authoritative.css'
      + '?v=148-step3-layout-2'
    );

    if (
      !stylesheet
        .getAttribute('href')
        ?.includes('148-step3-layout-2')
    ) {
      stylesheet.href = href;
    }
  }

  function field(key) {
    return document.querySelector(`[data-field="${key}"]`);
  }

  function setLabel(key, text) {
    const wrapper = field(key);
    const label = wrapper?.querySelector(
      ':scope > label, '
      + ':scope > .field-label-with-tip > label',
    );

    if (label && label.textContent !== text) {
      label.textContent = text;
    }
  }

  function organizePasswordField() {
    const wrapper = field('wpa2_passphrase');
    const input = byId('wpa2_passphrase');
    const reveal = byId('btnRevealPass');
    const qr = byId('btnShowQr');
    const hint = byId('passHint');

    if (!wrapper || !input || !reveal || !qr) {
      return false;
    }

    let row = wrapper.querySelector(
      ':scope > .pro-password-row'
    );

    if (!row) {
      row = document.createElement('div');
      row.className = (
        'pro-password-row pro-runtime-wrapper'
      );

      const label = wrapper.querySelector(
        ':scope > label, '
        + ':scope > .field-label-with-tip',
      );

      if (label?.nextSibling) {
        wrapper.insertBefore(row, label.nextSibling);
      } else {
        wrapper.prepend(row);
      }
    }

    reveal.type = 'button';
    reveal.classList.add(
      'btn',
      'icon-only',
      'pro-password-action',
    );
    reveal.title = 'Show or hide password';
    reveal.setAttribute(
      'aria-label',
      'Show or hide password',
    );

    qr.type = 'button';
    qr.className = (
      'btn icon-only pro-password-action'
    );
    qr.textContent = 'QR';
    qr.title = 'Show connection QR code';
    qr.setAttribute(
      'aria-label',
      'Show connection QR code',
    );

    for (const control of [input, reveal, qr]) {
      if (control.parentNode !== row) {
        row.appendChild(control);
      }
    }

    if (hint && hint.parentNode !== wrapper) {
      wrapper.appendChild(hint);
    }

    wrapper.classList.add(
      'pro-password-field',
      'pro-step3-field',
    );

    return true;
  }

  function organizeStepThree() {
    if (organizing || !isProMode()) {
      return false;
    }

    const container = document.querySelector(
      '#proStepHotspot .pro-hotspot-fields',
    );

    if (!container) {
      return false;
    }

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

      if (wrappers.some((wrapper) => !wrapper)) {
        return false;
      }

      for (const wrapper of wrappers) {
        wrapper.classList.add('pro-step3-field');

        if (wrapper.parentNode !== container) {
          container.appendChild(wrapper);
        }
      }

      wrappers.forEach((wrapper) => {
        container.appendChild(wrapper);
      });

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

  function start() {
    const observer = new MutationObserver((mutations) => {
      const changedMode = mutations.some(
        (mutation) => (
          mutation.type === 'attributes'
          && mutation.attributeName === 'data-ui-mode'
        ),
      );

      if (changedMode) {
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
      if (organizeStepThree()) {
        return;
      }

      retryCount += 1;

      if (retryCount < 120) {
        window.setTimeout(retryOrganization, 25);
      }
    }

    retryOrganization();
  }

  if (document.readyState === 'loading') {
    document.addEventListener(
      'DOMContentLoaded',
      start,
      { once: true },
    );
  } else {
    start();
  }
})();
