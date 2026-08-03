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
    const tip = make('span', 'tip basic-guided-tip', 'ⓘ');
    tip.setAttribute('data-tip', text);
    tip.setAttribute('aria-label', text);
    tip.setAttribute('tabindex', '0');
    return tip;
  }

  function setCardHeader(card, kind, title, subtitle) {
    const header = card && card.querySelector(':scope > .card-header');
    if (!header || header.dataset.guidedReady === '1') return;
    header.dataset.guidedReady = '1';
    header.classList.add('basic-guided-card-header');
    header.replaceChildren();

    const icon = make('span', `basic-guided-card-icon ${kind}`);
    icon.setAttribute('aria-hidden', 'true');

    const copy = make('div', 'basic-guided-card-heading');
    copy.append(
      make('h2', '', title),
      make('p', '', subtitle),
    );
    header.append(icon, copy);
  }

  function createStep(number, title, helper, tipText, slotId) {
    const step = make('section', 'basic-guided-step');
    step.dataset.step = String(number);

    const rail = make('div', 'basic-guided-step-rail');
    const badge = make('span', 'basic-guided-step-number', String(number));
    badge.setAttribute('aria-hidden', 'true');
    rail.appendChild(badge);

    const content = make('div', 'basic-guided-step-content');
    const heading = make('div', 'basic-guided-step-heading');
    heading.append(make('h3', '', title), makeInfoTip(tipText));

    const slot = make('div', 'basic-guided-step-slot');
    slot.id = slotId;

    const helperNode = make('p', 'basic-guided-step-help', helper);
    helperNode.id = `${slotId}Help`;
    content.append(heading, slot, helperNode);
    step.append(rail, content);
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
      'setup',
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
    staging.hidden = true;
    staging.append(quickStaging);
    const connectStaging = el('basicConnectFields');
    if (connectStaging) staging.append(connectStaging);

    const passGroup = el('wpa2_passphrase_basic')?.closest('.form-group');
    const passSlot = steps.querySelector('#basicGuidedPassSlot');
    if (passGroup && passSlot) passSlot.appendChild(passGroup);

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

    if (copySsid || copyPass) {
      const more = make('details', 'basic-guided-more-actions');
      more.appendChild(make('summary', '', 'More actions'));
      const moreBody = make('div', 'basic-guided-more-actions-body');
      if (copySsid) moreBody.appendChild(copySsid);
      if (copyPass) moreBody.appendChild(copyPass);
      more.appendChild(moreBody);
      steps.appendChild(more);
    }

    const copyHint = el('copyHint');
    if (copyHint && passSlot) passSlot.appendChild(copyHint);
    if (actionGroup && !actionGroup.children.length) actionGroup.remove();
    if (oldConnectArea) oldConnectArea.hidden = true;

    body.replaceChildren(notices, steps, technicalDefaults, staging);
    return card;
  }

  function makeWifiOrb() {
    const orb = make('div', 'basic-guided-status-orb');
    orb.setAttribute('aria-hidden', 'true');
    orb.innerHTML = [
      '<svg viewBox="0 0 64 64" focusable="false" aria-hidden="true">',
      '<path d="M10 25.5C22.2 14.7 41.8 14.7 54 25.5"/>',
      '<path d="M17.5 34C25.7 26.8 38.3 26.8 46.5 34"/>',
      '<path d="M25 42.5C29 39 35 39 39 42.5"/>',
      '<circle cx="32" cy="49" r="3.5"/>',
      '</svg>',
    ].join('');
    return orb;
  }

  function makeFact(iconClass, label, valueId) {
    const fact = make('div', 'basic-guided-status-fact');
    const icon = make('span', `basic-guided-fact-icon ${iconClass}`);
    icon.setAttribute('aria-hidden', 'true');
    const copy = make('div', 'basic-guided-fact-copy');
    const value = make('strong', '', '--');
    value.id = valueId;
    copy.append(make('span', '', label), value);
    fact.append(icon, copy);
    return fact;
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
      'status',
      'Status & Control',
      'See the hotspot state and control it from one place.',
    );

    const body = card.querySelector(':scope > .card-body');
    if (!body) return card;
    body.classList.add('basic-guided-status-body');

    const hero = make('section', 'basic-guided-status-hero');
    const orb = makeWifiOrb();
    const heroCopy = make('div', 'basic-guided-status-copy');
    const stateText = make('h3', '', 'Loading…');
    stateText.id = 'basicGuidedStateText';
    const summary = make('p', '', 'Checking the hotspot status.');
    summary.id = 'basicGuidedStatusSummary';
    heroCopy.append(stateText, summary);
    hero.append(orb, heroCopy);

    const originalPillContainer = el('basicPillContainer');
    if (originalPillContainer) {
      originalPillContainer.classList.add('basic-guided-original-status');
      hero.appendChild(originalPillContainer);
    }

    const facts = make('section', 'basic-guided-status-facts');
    facts.append(
      makeFact('adapter', 'Adapter', 'basicGuidedAdapterValue'),
      makeFact('band', 'Band', 'basicGuidedBandValue'),
    );

    const diagnosticDetails = make('div', 'basic-guided-status-details');
    const originalMeta = el('basicStatusAdapterBand');
    const statusDetails = el('basicStatusDetails');
    const lastError = el('basicLastError');
    const errorDetail = el('basicLastErrorDetail');
    if (originalMeta) diagnosticDetails.appendChild(originalMeta);
    if (lastError) diagnosticDetails.appendChild(lastError);
    if (errorDetail) diagnosticDetails.appendChild(errorDetail);

    const actions = card.querySelector('.basic-status-actions');
    if (actions) {
      actions.classList.add('basic-guided-status-actions');
      const buttonIcons = {
        btnStartBasic: '▶',
        btnStopBasic: '■',
        btnRepairBasic: '◆',
        btnRefreshBasic: '↻',
      };
      for (const button of actions.querySelectorAll('button')) {
        button.dataset.guidedIcon = buttonIcons[button.id] || '';
      }
    }

    const notice = make('div', 'basic-guided-apply-notice');
    const noticeIcon = make('span', 'basic-guided-notice-icon');
    noticeIcon.setAttribute('aria-hidden', 'true');
    const noticeCopy = make('div', 'basic-guided-notice-copy');
    noticeCopy.append(
      make('span', '', 'Pending changes are saved automatically when you start the hotspot.'),
    );
    const dirty = el('dirtyBasic');
    if (dirty) noticeCopy.appendChild(dirty);
    notice.append(noticeIcon, noticeCopy);

    const options = make('details', 'basic-guided-options');
    options.appendChild(make('summary', '', 'Options'));
    const optionsBody = make('div', 'basic-guided-options-body');
    const preferences = card.querySelector('.basic-status-preferences');
    const privacyHint = el('privacyHintBasic');
    const telemetry = el('basicTelemetryContainer');
    const message = el('msgBasic');
    if (statusDetails) optionsBody.appendChild(statusDetails);
    if (preferences) optionsBody.appendChild(preferences);
    if (privacyHint) optionsBody.appendChild(privacyHint);
    if (telemetry) optionsBody.appendChild(telemetry);
    if (message) optionsBody.appendChild(message);
    options.appendChild(optionsBody);

    body.replaceChildren(hero, facts, diagnosticDetails);
    if (actions) body.appendChild(actions);
    body.append(notice, options);
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
    const profileIcons = {
      off: '○',
      ultra_low_latency: '↗',
      vr: '◇',
    };
    document.querySelectorAll('input[name="qos_basic"]').forEach((input) => {
      const span = input.closest('label')?.querySelector('span');
      const icon = profileIcons[input.value] || '•';
      if (span && span.dataset.profileIcon !== icon) span.dataset.profileIcon = icon;
    });

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

  function cleanAdapterLabel(text) {
    return String(text || '')
      .replace(/\s*\(Recommended\)\s*/i, '')
      .trim() || '--';
  }

  function adapterLabelForValue(value) {
    const select = el('ap_adapter');
    if (!select) return value || '--';
    const match = Array.from(select.options).find((option) => option.value === value);
    if (match) return cleanAdapterLabel(match.textContent);
    if (!value || value === '--') {
      const selected = select.options[select.selectedIndex];
      return cleanAdapterLabel(selected?.textContent);
    }
    return value;
  }

  function parseStatusMeta() {
    const raw = el('basicStatusAdapterBand')?.textContent || '';
    const adapterMatch = raw.match(/Adapter:\s*([^|]+)/i);
    const bandMatch = raw.match(/Band:\s*([^|]+)/i);
    const adapterValue = adapterMatch ? adapterMatch[1].trim() : '--';
    const band = bandMatch ? bandMatch[1].trim() : '--';
    return {
      adapter: adapterLabelForValue(adapterValue),
      band: band || '--',
    };
  }

  function syncStatusPresentation() {
    const rawStatus = (el('basicPillTxt')?.textContent || 'Loading…').trim();
    const state = (rawStatus.split('|')[0] || 'Loading…').trim();
    const normalized = state.toLowerCase();
    const card = document.querySelector('.basic-guided-status-card');
    const stateText = el('basicGuidedStateText');
    const summary = el('basicGuidedStatusSummary');
    const facts = parseStatusMeta();

    setTextIfChanged(stateText, state);
    setTextIfChanged(el('basicGuidedAdapterValue'), facts.adapter);
    setTextIfChanged(el('basicGuidedBandValue'), facts.band);

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
          'Your changes could not be saved. Review the message under Options.',
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
      if (target.id === 'ap_adapter') syncStatusPresentation();
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
    observeStatusSource(el('basicStatusAdapterBand'));

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
