(function buildGuidedBasicInterface() {
  'use strict';

  const BASIC_MODE = 'basic';
  const TECHNICAL_DEFAULT_FIELDS = [
    'band_preference',
    'ap_security',
    'country',
    'enable_internet',
  ];
  const SAVE_WAIT_MS = 12000;

  let repositionQueued = false;
  let saveBeforeStartRunning = false;

  function el(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setTextIfChanged(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  function makeInfoTip(text) {
    const tip = make('span', 'tip basic-guided-tip', 'i');
    tip.setAttribute('data-tip', text);
    tip.setAttribute('aria-label', text);
    tip.setAttribute('tabindex', '0');
    return tip;
  }

  function setCardHeader(card, title, subtitle) {
    const header = card && card.querySelector(':scope > .card-header');
    if (!header || header.dataset.guidedReady === '1') return;
    header.dataset.guidedReady = '1';
    header.classList.add('basic-guided-card-header');
    header.replaceChildren();

    const copy = make('div', 'basic-guided-card-heading');
    copy.append(
      make('h2', '', title),
      make('p', '', subtitle),
    );
    header.appendChild(copy);
  }

  function createStep(number, title, helper, tipText, slotId) {
    const step = make('section', 'basic-guided-step');
    step.dataset.step = String(number);

    const badge = make('span', 'basic-guided-step-number', String(number));
    badge.setAttribute('aria-hidden', 'true');

    const content = make('div', 'basic-guided-step-content');
    const heading = make('div', 'basic-guided-step-heading');
    heading.append(make('h3', '', title), makeInfoTip(tipText));

    const slot = make('div', 'basic-guided-step-slot');
    slot.id = slotId;

    const helperNode = make('p', 'basic-guided-step-help', helper);
    helperNode.id = `${slotId}Help`;
    content.append(heading, slot, helperNode);
    step.append(badge, content);
    return step;
  }

  function buildSetupCard() {
    const quickStaging = el('basicQuickFields');
    if (!quickStaging) return null;
    const card = quickStaging.closest('.basic-card');
    if (!card || card.dataset.basicGuidedReady === '1') return card;
    card.dataset.basicGuidedReady = '1';
    card.classList.add('basic-guided-card', 'basic-guided-setup-card');

    setCardHeader(
      card,
      'Set Up Hotspot',
      'Follow these simple steps to create your hotspot.',
    );

    const body = card.querySelector(':scope > .card-body');
    if (!body) return card;
    body.classList.add('basic-guided-setup-body');

    const notices = make('div', 'basic-guided-notices');
    const infoBanner = el('basicInfoBanner');
    const channelBanner = el('basicChannelBanner');
    if (infoBanner) notices.appendChild(infoBanner);
    if (channelBanner) notices.appendChild(channelBanner);

    const steps = make('div', 'basic-guided-steps');
    steps.append(
      createStep(
        1,
        'Choose Wi-Fi adapter',
        'The recommended USB adapter gives the best VR performance.',
        'Select the USB Wi-Fi adapter VRhotspot should use to create the hotspot.',
        'basicGuidedAdapterSlot',
      ),
      createStep(
        2,
        'Choose connection profile',
        'Speed prioritizes low latency for VR streaming.',
        'Choose how aggressively VRhotspot tunes the connection. Speed is the recommended default.',
        'basicGuidedProfileSlot',
      ),
      createStep(
        3,
        'Hotspot name (SSID)',
        'This is the Wi-Fi network name other devices will see.',
        'Choose the name shown in the Wi-Fi list on your headset and other devices.',
        'basicGuidedSsidSlot',
      ),
      createStep(
        4,
        'Password (Passphrase)',
        'Use 8–63 characters. Press Enter or choose Save password.',
        'This password protects the hotspot. You can reveal it or generate a QR code for easy connection.',
        'basicGuidedPassSlot',
      ),
    );

    const technicalDefaults = make('div', 'basic-guided-technical-defaults');
    technicalDefaults.id = 'basicGuidedTechnicalDefaults';
    technicalDefaults.hidden = true;

    const staging = make('div', 'basic-guided-staging');
    staging.id = 'basicGuidedHiddenSetup';
    staging.hidden = true;
    staging.append(quickStaging);
    const connectStaging = el('basicConnectFields');
    if (connectStaging) staging.append(connectStaging);

    const passField = el('wpa2_passphrase_basic');
    const passGroup = passField?.closest('.form-group');
    const passSlot = steps.querySelector('#basicGuidedPassSlot');
    if (passGroup && passSlot) passSlot.appendChild(passGroup);

    const passActions = passGroup?.querySelector('.basic-passphrase-actions');
    const revealPass = el('btnRevealPassBasic');
    const showQr = el('btnShowQrBasic');
    if (passActions) {
      passActions.classList.add('basic-guided-password-row');
      if (passField) passActions.appendChild(passField);
      if (revealPass) passActions.appendChild(revealPass);
      if (showQr) passActions.appendChild(showQr);
    }

    const oldConnectArea = card.querySelector('.basic-connect-inline');
    const actionGroup = oldConnectArea?.querySelector('.basic-connect-actions');
    const savePass = el('btnSavePassBasic');
    const copySsid = el('btnCopySsid');
    const copyPass = el('btnCopyPass');

    if (savePass && passSlot) {
      savePass.textContent = 'Save password';
      savePass.classList.add('basic-guided-save-password');
      passSlot.appendChild(savePass);
    }

    // Keep the existing copy controls and their handlers alive without exposing
    // an empty disclosure in the simplified Basic interface.
    if (copySsid) staging.appendChild(copySsid);
    if (copyPass) staging.appendChild(copyPass);

    const copyHint = el('copyHint');
    if (copyHint && passSlot) passSlot.appendChild(copyHint);
    if (actionGroup && !actionGroup.children.length) actionGroup.remove();
    if (oldConnectArea) oldConnectArea.hidden = true;

    body.replaceChildren(notices, steps, technicalDefaults, staging);
    return card;
  }

  function buildStatusCard() {
    const pill = el('basicPill');
    if (!pill) return null;
    const card = pill.closest('.basic-card');
    if (!card || card.dataset.basicGuidedReady === '1') return card;
    card.dataset.basicGuidedReady = '1';
    card.classList.add('basic-guided-card', 'basic-guided-status-card');

    setCardHeader(
      card,
      'Status & Control',
      'See the hotspot state and control it from one place.',
    );

    const body = card.querySelector(':scope > .card-body');
    if (!body) return card;
    body.classList.add('basic-guided-status-body');

    const hero = make('section', 'basic-guided-status-hero');
    const stateRow = make('div', 'basic-guided-status-state');
    const stateDot = make('span', 'basic-guided-status-dot');
    stateDot.setAttribute('aria-hidden', 'true');
    const stateText = make('h3', '', 'Loading…');
    stateText.id = 'basicGuidedStateText';
    stateRow.append(stateDot, stateText);

    const summary = make('p', '', 'Checking the hotspot status.');
    summary.id = 'basicGuidedStatusSummary';
    hero.append(stateRow, summary);

    const hiddenStatus = make('div', 'basic-guided-hidden-status');
    hiddenStatus.id = 'basicGuidedHiddenStatus';
    hiddenStatus.hidden = true;

    const originalPillContainer = el('basicPillContainer');
    if (originalPillContainer) hiddenStatus.appendChild(originalPillContainer);

    const originalMeta = el('basicStatusAdapterBand');
    if (originalMeta) hiddenStatus.appendChild(originalMeta);

    const statusDetails = el('basicStatusDetails');
    const lastError = el('basicLastError');
    const errorDetail = el('basicLastErrorDetail');

    const diagnosticDetails = make('div', 'basic-guided-status-details');
    if (lastError) diagnosticDetails.appendChild(lastError);
    if (errorDetail) diagnosticDetails.appendChild(errorDetail);

    const actions = card.querySelector('.basic-status-actions');
    if (actions) actions.classList.add('basic-guided-status-actions');

    // These controls remain connected so switching to Pro mode and the existing
    // state synchronization keep working, but Basic mode intentionally does not
    // expose privacy, refresh, telemetry, or technical feedback controls.
    const preferences = card.querySelector('.basic-status-preferences');
    const privacyHint = el('privacyHintBasic');
    const telemetry = el('basicTelemetryContainer');
    const message = el('msgBasic');
    const dirty = el('dirtyBasic');
    for (const node of (
      [statusDetails, preferences, privacyHint, telemetry, message, dirty]
    )) {
      if (node) hiddenStatus.appendChild(node);
    }

    body.replaceChildren(hero, diagnosticDetails);
    if (actions) body.appendChild(actions);
    body.appendChild(hiddenStatus);
    return card;
  }

  function fieldNode(key) {
    return document.querySelector(`[data-field="${key}"]`);
  }

  function isInStaging(node) {
    if (!node) return false;
    const quick = el('basicQuickFields');
    const connect = el('basicConnectFields');
    return !!(
      (quick && quick.contains(node))
      || (connect && connect.contains(node))
    );
  }

  function moveManagedField(key, slotId) {
    const node = fieldNode(key);
    const slot = el(slotId);
    if (!node || !slot || slot.contains(node)) return;
    if (isInStaging(node)) slot.appendChild(node);
  }

  function repositionBasicFields() {
    repositionQueued = false;
    if (!document.body || document.body.dataset.uiMode !== BASIC_MODE) return;

    moveManagedField('ap_adapter', 'basicGuidedAdapterSlot');
    moveManagedField('qos_preset', 'basicGuidedProfileSlot');
    moveManagedField('ssid', 'basicGuidedSsidSlot');

    const defaults = el('basicGuidedTechnicalDefaults');
    if (defaults) {
      for (const key of TECHNICAL_DEFAULT_FIELDS) {
        const node = fieldNode(key);
        if (node && !defaults.contains(node) && isInStaging(node)) {
          defaults.appendChild(node);
        }
      }
    }

    decorateMovedFields();
    syncStatusPresentation();
  }

  function queueReposition() {
    if (repositionQueued) return;
    repositionQueued = true;
    window.setTimeout(repositionBasicFields, 0);
  }

  function decorateMovedFields() {
    const adapterField = fieldNode('ap_adapter');
    if (adapterField) {
      adapterField.classList.add('basic-guided-adapter-field');
      const recommended = el('btnUseRecommended');
      if (recommended) recommended.hidden = true;
      const rescan = el('btnReloadAdapters');
      if (rescan) setTextIfChanged(rescan, 'Rescan adapters');
    }

    const profileField = fieldNode('qos_preset');
    if (profileField) profileField.classList.add('basic-guided-profile-field');

    const ssidField = fieldNode('ssid');
    if (ssidField) ssidField.classList.add('basic-guided-ssid-field');

    const passField = el('wpa2_passphrase_basic');
    const passGroup = passField?.closest('.form-group');
    if (passGroup) passGroup.classList.add('basic-guided-pass-field');

    updateProfileHelp();
  }

  function updateProfileHelp() {
    const helper = el('basicGuidedProfileSlotHelp');
    if (!helper) return;
    const selected = document.querySelector('input[name="qos_basic"]:checked');
    const copy = {
      off: 'Uses standard hotspot behavior without extra performance tuning.',
      ultra_low_latency: 'Speed prioritizes low latency for VR streaming.',
      vr: 'Stable favors reliability on busy or noisy wireless networks.',
    };
    setTextIfChanged(
      helper,
      copy[selected?.value] || 'Choose the profile that matches how you use the hotspot.',
    );
  }

  function syncStatusPresentation() {
    const rawStatus = (el('basicPillTxt')?.textContent || 'Loading…').trim();
    const state = (rawStatus.split('|')[0] || 'Loading…').trim();
    const normalized = state.toLowerCase();
    const card = document.querySelector('.basic-guided-status-card');
    const stateText = el('basicGuidedStateText');
    const summary = el('basicGuidedStatusSummary');

    setTextIfChanged(stateText, state);

    let stateName = 'loading';
    let summaryText = 'Checking the hotspot status.';
    if (normalized.includes('running')) {
      stateName = 'running';
      summaryText = 'Your hotspot is active and ready.';
    } else if (normalized.includes('error')) {
      stateName = 'error';
      summaryText = 'The hotspot needs attention.';
    } else if (normalized.includes('starting') || normalized.includes('repair')) {
      stateName = 'working';
      summaryText = 'VRhotspot is preparing the connection.';
    } else if (normalized.includes('stopped')) {
      stateName = 'stopped';
      summaryText = 'Your hotspot is not active.';
    }
    if (card && card.dataset.hotspotState !== stateName) {
      card.dataset.hotspotState = stateName;
    }
    setTextIfChanged(summary, summaryText);
  }

  function pendingBasicChanges() {
    return !!(el('dirtyBasic')?.textContent || '').trim();
  }

  function waitForBasicSave() {
    const started = Date.now();
    return new Promise((resolve) => {
      function check() {
        if (!pendingBasicChanges()) {
          resolve(true);
          return;
        }
        if ((Date.now() - started) >= SAVE_WAIT_MS) {
          resolve(false);
          return;
        }
        window.setTimeout(check, 100);
      }
      check();
    });
  }

  function wireSaveBeforeStart() {
    document.addEventListener('click', async (event) => {
      const target = event.target instanceof Element
        ? event.target.closest('#btnStartBasic')
        : null;
      if (!target) return;

      if (target.dataset.guidedResume === '1') {
        delete target.dataset.guidedResume;
        return;
      }
      if (!pendingBasicChanges()) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      if (saveBeforeStartRunning) return;
      saveBeforeStartRunning = true;
      target.classList.add('is-saving');
      setTextIfChanged(el('basicGuidedStatusSummary'), 'Saving your changes before starting…');

      const saveButton = el('btnSaveConfig');
      if (saveButton) saveButton.click();
      const saved = !!saveButton && await waitForBasicSave();

      target.classList.remove('is-saving');
      saveBeforeStartRunning = false;
      if (!saved) {
        setTextIfChanged(
          el('basicGuidedStatusSummary'),
          'Your changes could not be saved. Switch to Pro mode for details.',
        );
        return;
      }

      target.dataset.guidedResume = '1';
      target.click();
    }, true);
  }

  function wireGuidedInteractions() {
    document.addEventListener('change', (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.matches('input[name="qos_basic"]')) updateProfileHelp();
    });
    wireSaveBeforeStart();
  }

  function observeStagingContainer(container) {
    if (!container) return;
    const observer = new MutationObserver(queueReposition);
    observer.observe(container, { childList: true, subtree: true });
  }

  function observeStatusSource(source) {
    if (!source) return;
    const observer = new MutationObserver(syncStatusPresentation);
    observer.observe(source, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function startObservers() {
    observeStagingContainer(el('basicQuickFields'));
    observeStagingContainer(el('basicConnectFields'));
    observeStatusSource(el('basicPillTxt'));

    const bodyObserver = new MutationObserver(() => {
      if (document.body.dataset.uiMode === BASIC_MODE) queueReposition();
    });
    bodyObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-ui-mode'],
    });
  }

  function initialize() {
    buildSetupCard();
    buildStatusCard();
    wireGuidedInteractions();
    startObservers();
    queueReposition();
    syncStatusPresentation();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();
