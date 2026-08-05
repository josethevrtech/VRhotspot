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
  let lastStateName = '';

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

  function createStep(number, title, help, slotId) {
    const step = make('section', 'basic-guided-step');
    step.dataset.step = String(number);

    // The numbered badge is the step-purpose help: same shared floating
    // tooltip component as Pro, keyboard focusable, no static caption below.
    const badge = make('span', 'basic-guided-step-number', String(number));
    if (typeof attachStepHelp === 'function') {
      attachStepHelp(badge, help);
    } else {
      badge.setAttribute('aria-hidden', 'true');
    }

    const content = make('div', 'basic-guided-step-content');
    const heading = make('div', 'basic-guided-step-heading');
    const titleNode = make('h3', '', title);
    titleNode.id = `${slotId}Title`;
    heading.appendChild(titleNode);

    const slot = make('div', 'basic-guided-step-slot');
    slot.id = slotId;

    content.append(heading, slot);
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
        'Select the Wi-Fi adapter that will create the hotspot. The recommended USB adapter normally provides the best VR performance.',
        'basicGuidedAdapterSlot',
      ),
      createStep(
        2,
        'Choose performance mode',
        'Choose how aggressively VRHotspot tunes the network for performance, responsiveness, or stability.',
        'basicGuidedProfileSlot',
      ),
      createStep(
        3,
        'Hotspot name',
        'Set the hotspot name and password used by connecting devices.',
        'basicGuidedSsidSlot',
      ),
      createStep(
        4,
        'Password',
        'Use 8–63 characters. Control characters are not allowed. Use the eye to reveal the password or the QR button to connect another device.',
        'basicGuidedPassSlot',
      ),
      createStep(
        5,
        'Start hotspot',
        'Review the selected adapter, profile, network name, and password, then start the hotspot.',
        'basicGuidedActionSlot',
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

    const hiddenStatus = make('div', 'basic-guided-hidden-status');
    hiddenStatus.id = 'basicGuidedHiddenStatus';
    hiddenStatus.hidden = true;

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

    if (copySsid) staging.appendChild(copySsid);
    if (copyPass) staging.appendChild(copyPass);

    const copyHint = el('copyHint');
    if (copyHint && passSlot) passSlot.appendChild(copyHint);
    if (actionGroup && !actionGroup.children.length) actionGroup.remove();
    if (oldConnectArea) oldConnectArea.hidden = true;

    body.replaceChildren(
      notices,
      steps,
      technicalDefaults,
      staging,
      hiddenStatus,
    );
    return card;
  }

  function buildHotspotStep() {
    const pill = el('basicPill');
    const actionSlot = el('basicGuidedActionSlot');
    const hiddenStatus = el('basicGuidedHiddenStatus');
    if (!pill || !actionSlot || !hiddenStatus) return null;

    const statusCard = pill.closest('.basic-card');
    if (!statusCard || statusCard.dataset.basicGuidedReady === '1') return statusCard;
    statusCard.dataset.basicGuidedReady = '1';
    statusCard.classList.add('basic-guided-status-source-card');
    statusCard.hidden = true;

    const control = make('section', 'basic-guided-hotspot-control');
    const statusCopy = make('div', 'basic-guided-hotspot-copy');
    const stateRow = make('div', 'basic-guided-status-state');
    const stateDot = make('span', 'basic-guided-status-dot');
    stateDot.setAttribute('aria-hidden', 'true');
    const stateText = make('h4', '', 'Loading…');
    stateText.id = 'basicGuidedStateText';
    stateRow.append(stateDot, stateText);

    const summary = make('p', '', 'Checking the hotspot status.');
    summary.id = 'basicGuidedStatusSummary';
    statusCopy.append(stateRow, summary);

    const startButton = el('btnStartBasic');
    if (startButton) {
      startButton.classList.add('basic-guided-primary-action');
      control.append(statusCopy, startButton);
    } else {
      control.appendChild(statusCopy);
    }
    actionSlot.appendChild(control);

    const originalPillContainer = el('basicPillContainer');
    const originalMeta = el('basicStatusAdapterBand');
    const statusDetails = el('basicStatusDetails');
    const lastError = el('basicLastError');
    const errorDetail = el('basicLastErrorDetail');
    const actions = statusCard.querySelector('.basic-status-actions');
    const stopButton = el('btnStopBasic');
    const repairButton = el('btnRepairBasic');
    const refreshButton = el('btnRefreshBasic');
    const preferences = statusCard.querySelector('.basic-status-preferences');
    const privacyHint = el('privacyHintBasic');
    const telemetry = el('basicTelemetryContainer');
    const message = el('msgBasic');
    const dirty = el('dirtyBasic');

    for (const node of [
      originalPillContainer,
      originalMeta,
      statusDetails,
      lastError,
      errorDetail,
      actions,
      stopButton,
      repairButton,
      refreshButton,
      preferences,
      privacyHint,
      telemetry,
      message,
      dirty,
    ]) {
      if (node) hiddenStatus.appendChild(node);
    }

    return statusCard;
  }

  function buildErrorDialog() {
    if (el('basicHotspotError')) return;

    const overlay = make('div', 'basic-hotspot-error-overlay');
    overlay.id = 'basicHotspotError';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'basicHotspotErrorTitle');

    const dialog = make('div', 'basic-hotspot-error-dialog');
    const header = make('div', 'basic-hotspot-error-header');
    const title = make('h2', '', 'Hotspot could not start');
    title.id = 'basicHotspotErrorTitle';
    const close = make('button', 'btn sm secondary', 'Close');
    close.type = 'button';
    close.id = 'basicHotspotErrorClose';
    close.addEventListener('click', closeHotspotErrorDialog);
    header.append(title, close);

    const body = make('div', 'basic-hotspot-error-body');
    body.append(
      make('p', '', 'VRhotspot encountered a network problem.'),
      make(
        'p',
        'small',
        'Try starting it again, or open Pro mode to review diagnostics and use Repair Network.',
      ),
    );

    const footer = make('div', 'basic-hotspot-error-footer');
    const retry = make('button', 'btn secondary', 'Try Again');
    retry.type = 'button';
    retry.id = 'basicHotspotRetry';
    retry.addEventListener('click', retryHotspotStart);
    const openPro = make('button', 'btn primary', 'Open Pro Mode');
    openPro.type = 'button';
    openPro.id = 'basicHotspotOpenPro';
    openPro.addEventListener('click', openProRepair);
    footer.append(retry, openPro);

    dialog.append(header, body, footer);
    overlay.appendChild(dialog);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) closeHotspotErrorDialog();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !overlay.hidden) closeHotspotErrorDialog();
    });
    document.body.appendChild(overlay);
  }

  function openHotspotErrorDialog() {
    if (!document.body || document.body.dataset.uiMode !== BASIC_MODE) return;
    const overlay = el('basicHotspotError');
    if (!overlay) return;
    overlay.hidden = false;
    document.body.classList.add('basic-hotspot-modal-open');
    const openPro = el('basicHotspotOpenPro');
    if (openPro) openPro.focus();
  }

  function closeHotspotErrorDialog() {
    const overlay = el('basicHotspotError');
    if (!overlay) return;
    overlay.hidden = true;
    document.body.classList.remove('basic-hotspot-modal-open');
    const action = el('btnStartBasic');
    if (action && document.body.dataset.uiMode === BASIC_MODE) action.focus();
  }

  function retryHotspotStart() {
    closeHotspotErrorDialog();
    const action = el('btnStartBasic');
    if (!action) return;
    action.dataset.guidedRetry = '1';
    action.click();
  }

  function openProRepair() {
    closeHotspotErrorDialog();
    const toggle = el('uiModeToggle');
    if (toggle && !toggle.checked) toggle.click();

    window.setTimeout(() => {
      const overview = document.querySelector('.nav-item[data-tab="overview"]');
      if (overview && !overview.classList.contains('active')) overview.click();
      const repair = el('btnRepair');
      if (repair) {
        repair.scrollIntoView({ block: 'center' });
        repair.focus();
      }
    }, 250);
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

  function renameProfileOptions() {
    const names = {
      off: 'Standard',
      ultra_low_latency: 'Speed',
      vr: 'Stable',
    };
    document.querySelectorAll('input[name="qos_basic"]').forEach((input) => {
      const label = input.nextElementSibling;
      if (label && names[input.value]) setTextIfChanged(label, names[input.value]);
    });
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

    renameProfileOptions();
  }

  function hotspotState(rawStatus) {
    const state = (rawStatus.split('|')[0] || 'Loading…').trim();
    const normalized = state.toLowerCase();

    if (normalized.includes('error') || normalized.includes('failed')) {
      return { name: 'error', label: 'Needs attention' };
    }
    if (
      normalized.includes('stopped')
      || normalized.includes('inactive')
      || normalized.includes('not running')
    ) {
      return { name: 'stopped', label: 'Stopped' };
    }
    if (normalized.includes('stopping')) return { name: 'working', label: 'Stopping…' };
    if (normalized.includes('starting') || normalized.includes('repair')) {
      return { name: 'working', label: 'Starting…' };
    }
    if (normalized.includes('running')) return { name: 'running', label: state };
    return { name: 'loading', label: state || 'Loading…' };
  }

  function syncPrimaryAction(stateName) {
    const action = el('btnStartBasic');
    if (!action) return;

    action.classList.remove('primary', 'secondary', 'danger');
    action.disabled = false;

    if (stateName === 'running') {
      action.dataset.guidedAction = 'stop';
      action.textContent = 'Stop hotspot';
      action.classList.add('danger');
      return;
    }
    if (stateName === 'error') {
      action.dataset.guidedAction = 'problem';
      action.textContent = 'View problem';
      action.classList.add('danger');
      return;
    }
    if (stateName === 'working') {
      action.dataset.guidedAction = 'wait';
      action.textContent = 'Working…';
      action.classList.add('secondary');
      action.disabled = true;
      return;
    }
    if (stateName === 'stopped') {
      action.dataset.guidedAction = 'start';
      action.textContent = 'Start hotspot';
      action.classList.add('primary');
      return;
    }

    action.dataset.guidedAction = 'wait';
    action.textContent = 'Checking status…';
    action.classList.add('secondary');
    action.disabled = true;
  }

  function syncStatusPresentation() {
    const rawStatus = (el('basicPillTxt')?.textContent || 'Loading…').trim();
    const current = hotspotState(rawStatus);
    const card = document.querySelector('.basic-guided-setup-card');
    const stateText = el('basicGuidedStateText');
    const summary = el('basicGuidedStatusSummary');
    const title = el('basicGuidedActionSlotTitle');

    setTextIfChanged(stateText, current.label);

    let summaryText = 'Checking the hotspot status.';
    let titleText = 'Start hotspot';
    if (current.name === 'running') {
      titleText = 'Hotspot running';
      summaryText = 'Your hotspot is active and ready for your headset.';
    } else if (current.name === 'error') {
      titleText = 'Hotspot needs attention';
      summaryText = 'VRhotspot encountered a network problem.';
    } else if (current.name === 'working') {
      titleText = current.label.toLowerCase().includes('stopping')
        ? 'Stopping hotspot'
        : 'Starting hotspot';
      summaryText = 'VRhotspot is applying the connection settings.';
    } else if (current.name === 'stopped') {
      summaryText = 'The hotspot is stopped.';
    }

    if (card && card.dataset.hotspotState !== current.name) {
      card.dataset.hotspotState = current.name;
    }
    setTextIfChanged(title, titleText);
    setTextIfChanged(summary, summaryText);
    syncPrimaryAction(current.name);

    if (current.name === 'error' && lastStateName !== 'error') {
      openHotspotErrorDialog();
    }
    lastStateName = current.name;
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

  function wirePrimaryAction() {
    document.addEventListener('click', async (event) => {
      const target = event.target instanceof Element
        ? event.target.closest('#btnStartBasic')
        : null;
      if (!target) return;

      if (target.dataset.guidedRetry === '1') {
        delete target.dataset.guidedRetry;
        return;
      }
      if (target.dataset.guidedResume === '1') {
        delete target.dataset.guidedResume;
        return;
      }

      const action = target.dataset.guidedAction || 'start';
      if (action === 'stop') {
        event.preventDefault();
        event.stopImmediatePropagation();
        const stopButton = el('btnStopBasic');
        if (stopButton) stopButton.click();
        return;
      }
      if (action === 'problem') {
        event.preventDefault();
        event.stopImmediatePropagation();
        openHotspotErrorDialog();
        return;
      }
      if (action !== 'start') {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (!pendingBasicChanges()) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      if (saveBeforeStartRunning) return;
      saveBeforeStartRunning = true;
      target.classList.add('is-saving');
      target.disabled = true;
      setTextIfChanged(el('basicGuidedStatusSummary'), 'Saving your changes before starting…');

      const saveButton = el('btnSaveConfig');
      if (saveButton) saveButton.click();
      const saved = !!saveButton && await waitForBasicSave();

      target.classList.remove('is-saving');
      saveBeforeStartRunning = false;
      if (!saved) {
        target.disabled = false;
        setTextIfChanged(
          el('basicGuidedStatusSummary'),
          'Your changes could not be saved. Switch to Pro mode for details.',
        );
        openHotspotErrorDialog();
        return;
      }

      target.dataset.guidedResume = '1';
      target.click();
    }, true);
  }

  function wireGuidedInteractions() {
    wirePrimaryAction();
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
      if (document.body.dataset.uiMode === BASIC_MODE) {
        queueReposition();
        syncStatusPresentation();
      } else {
        closeHotspotErrorDialog();
      }
    });
    bodyObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-ui-mode'],
    });
  }

  function initialize() {
    buildSetupCard();
    buildHotspotStep();
    buildErrorDialog();
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
