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
