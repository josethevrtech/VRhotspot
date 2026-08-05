(function addVrhotspotOnlyDeveloperHub() {
  'use strict';

  const STATUS_PATH = '/v1/status';
  const DEVICES_PATH = '/v1/devbridge/adb/devices';
  const WIRELESS_PATH = '/v1/devbridge/adb/enable-wireless';
  const TOOLS_PATH = '/v1/devbridge/tools/status';
  const POLL_MS = 1600;
  const WORKSPACE_VIEW_KEY = 'vrhs_devhub_workspace_view';

  const state = {
    initialized: false,
    requestRunning: false,
    startRunning: false,
    setupComplete: false,
    manualJoinPending: false,
    selectedUsbSerial: '',
    usbDevices: [],
    pollTimer: null,
    manualPollTimer: null,
    observer: null,
    toolsTimer: null,
  };

  function el(id) {
    return document.getElementById(id);
  }

  function normalized(value) {
    return String(value || '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function operationData(response) {
    return response && response.json && response.json.data
      ? response.json.data
      : {};
  }

  function nestedData(response) {
    const result = operationData(response);
    return result && result.data && typeof result.data === 'object'
      ? result.data
      : {};
  }

  function resultCode(response) {
    const result = operationData(response);
    return String(
      (result && result.result_code)
      || (response && response.json && response.json.result_code)
      || (response ? `HTTP ${response.status}` : 'request_failed'),
    );
  }

  function responseMessage(response, fallback) {
    const result = operationData(response);
    return String(
      (result && result.message)
      || (response && response.json && response.json.message)
      || fallback,
    );
  }

  function feedback(message, kind) {
    const node = el('devhubFeedback');
    if (!node) return;
    node.textContent = message || '';
    node.dataset.state = kind || 'idle';
  }

  async function request(path, options) {
    if (typeof api !== 'function') {
      return { ok: false, status: 503, json: null, raw: '' };
    }
    return api(path, options || {});
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

  function cardByTitle(panel, title) {
    if (!panel) return null;
    return Array.from(panel.querySelectorAll(':scope > .devhub-workspace-grid > .card')).find((card) => {
      const heading = card.querySelector('.card-header h2');
      return heading && heading.textContent.trim() === title;
    }) || null;
  }

  function statusState(response) {
    const outer = response && response.json && response.json.data;
    if (!outer || typeof outer !== 'object') return {};
    if (outer.data && typeof outer.data === 'object') return outer.data;
    return outer;
  }

  function stateIsRunning(runtime) {
    return !!(runtime && (runtime.running || runtime.phase === 'running'));
  }

  async function hotspotRunning() {
    const response = await request(STATUS_PATH);
    return {
      response,
      runtime: statusState(response),
      running: response.ok && stateIsRunning(statusState(response)),
    };
  }

  function stopTimer(name) {
    if (state[name]) window.clearInterval(state[name]);
    state[name] = null;
  }

  function stopPolling() {
    stopTimer('pollTimer');
  }

  function stopManualPolling() {
    stopTimer('manualPollTimer');
  }

  function deviceProperties(device) {
    return device && device.properties && typeof device.properties === 'object'
      ? device.properties
      : {};
  }

  function isUsbDevice(device) {
    const serial = String((device && device.serial) || '').trim();
    if (!serial || serial.includes(':')) return false;
    const props = deviceProperties(device);
    return !!props.usb || !serial.includes(':');
  }

  function deviceModel(device) {
    const props = deviceProperties(device);
    return normalized(props.model || props.product || 'Android XR headset');
  }

  function chosenUsbDevice() {
    return state.usbDevices.find(
      (device) => String(device.serial || '') === state.selectedUsbSerial,
    ) || state.usbDevices.find((device) => device.state === 'device')
      || state.usbDevices[0]
      || null;
  }

  async function getDevices() {
    const response = await request(DEVICES_PATH);
    if (!response.ok) {
      throw new Error(responseMessage(response, 'Unable to inspect ADB devices.'));
    }
    const data = nestedData(response);
    return Array.isArray(data.devices) ? data.devices : [];
  }

  function setWizardStep(step) {
    document.querySelectorAll('#devhubWirelessWizard .devhub-wizard-step').forEach((node) => {
      const value = Number(node.dataset.step);
      node.classList.toggle('current', value === step);
      node.classList.toggle('complete', value < step);
    });
  }

  function setWizardCopy(title, detail) {
    const titleNode = el('devhubWizardTitle');
    const detailNode = el('devhubWizardDetail');
    if (titleNode) titleNode.textContent = title || '';
    if (detailNode) detailNode.textContent = detail || '';
  }

  function renderUsbDevice(device) {
    const box = el('devhubWizardDevice');
    if (!box) return;
    box.replaceChildren();

    if (!device) {
      box.hidden = true;
      return;
    }

    box.hidden = false;
    const name = document.createElement('strong');
    name.textContent = deviceModel(device);
    const serial = document.createElement('span');
    serial.className = 'devhub-sensitive-identifier';
    serial.textContent = String(device.serial || '');
    box.append(name, serial);

    if (state.usbDevices.length > 1) {
      const label = document.createElement('label');
      label.textContent = 'USB headset';
      const select = document.createElement('select');
      select.id = 'devhubWizardUsbSelect';
      for (const candidate of state.usbDevices) {
        const option = document.createElement('option');
        option.value = String(candidate.serial || '');
        option.textContent = `${deviceModel(candidate)} — ${candidate.state || 'unknown'}`;
        option.selected = option.value === state.selectedUsbSerial;
        select.appendChild(option);
      }
      select.addEventListener('change', () => {
        state.selectedUsbSerial = select.value;
        renderWizardState();
      });
      label.appendChild(select);
      box.appendChild(label);
    }
  }

  function renderWizardState() {
    const action = el('devhubWizardAction');
    const device = chosenUsbDevice();
    if (!action) return;

    renderUsbDevice(device);

    if (state.setupComplete) {
      setWizardStep(5);
      action.disabled = false;
      action.textContent = 'Done';
      return;
    }

    if (state.manualJoinPending) {
      setWizardStep(3);
      action.disabled = state.requestRunning;
      action.textContent = 'Recheck VRhotspot network';
      return;
    }

    action.textContent = 'Join VRhotspot & enable wireless';

    if (!device) {
      setWizardStep(1);
      setWizardCopy(
        'Connect the headset by USB',
        'Use a data-capable USB cable. Developer Hub will detect the headset automatically.',
      );
      action.disabled = true;
      return;
    }

    const adbState = String(device.state || '').toLowerCase();
    if (adbState === 'unauthorized') {
      setWizardStep(2);
      setWizardCopy(
        'Approve USB debugging inside the headset',
        'Put on the headset, enable “Always allow from this computer,” and select Allow.',
      );
      action.disabled = true;
      return;
    }

    if (adbState !== 'device') {
      setWizardStep(1);
      setWizardCopy(
        'Waiting for the USB headset',
        `The headset currently reports “${adbState || 'unknown'}.” Reconnect the cable and keep it awake.`,
      );
      action.disabled = true;
      return;
    }

    setWizardStep(3);
    setWizardCopy(
      `${deviceModel(device)} is ready to join VRhotspot`,
      'Developer Hub will verify the dedicated hotspot network before enabling wireless ADB.',
    );
    action.disabled = state.requestRunning;
  }

  async function pollUsbDevices() {
    const modal = el('devhubWirelessWizard');
    if (state.requestRunning || !modal || modal.hidden) return;
    try {
      state.usbDevices = (await getDevices()).filter(isUsbDevice);
      if (!state.usbDevices.some(
        (device) => String(device.serial || '') === state.selectedUsbSerial,
      )) {
        const preferred = state.usbDevices.find((device) => device.state === 'device')
          || state.usbDevices[0];
        state.selectedUsbSerial = preferred ? String(preferred.serial || '') : '';
      }
      renderWizardState();
    } catch (error) {
      setWizardCopy('Unable to inspect USB devices', String(error.message || error));
    }
  }

  function startPolling() {
    stopPolling();
    void pollUsbDevices();
    state.pollTimer = window.setInterval(pollUsbDevices, POLL_MS);
  }

  function selectWirelessTarget(target) {
    const refresh = el('devhubRefresh');
    if (refresh && !refresh.disabled) refresh.click();

    let attempt = 0;
    const timer = window.setInterval(() => {
      attempt += 1;
      const rows = document.querySelectorAll('#devhubDeviceList .devhub-list-item');
      const row = Array.from(rows).find((candidate) => {
        if (candidate.dataset.devhubSerial === target) return true;
        const address = candidate.querySelector('.devhub-device-address');
        return address && String(address.textContent || '').includes(target);
      });
      if (row) {
        const choose = row.querySelector('button');
        if (choose) choose.click();
        window.clearInterval(timer);
      } else if (attempt >= 10) {
        window.clearInterval(timer);
      }
    }, 450);
  }

  function startManualJoinPolling() {
    stopManualPolling();
    state.manualPollTimer = window.setInterval(() => {
      const modal = el('devhubWirelessWizard');
      if (!modal || modal.hidden || !state.manualJoinPending || state.requestRunning) return;
      void runWirelessBootstrap(true);
    }, 2200);
  }

  async function runWirelessBootstrap(fromPoll) {
    if (state.setupComplete) {
      closeWizard();
      return;
    }

    const device = chosenUsbDevice();
    if (!device || device.state !== 'device' || state.requestRunning) return;

    state.requestRunning = true;
    renderWizardState();
    if (!fromPoll) {
      setWizardStep(4);
      setWizardCopy(
        'Verifying the VRhotspot network',
        'Wireless ADB will remain disabled until the headset address and route match VRhotspot.',
      );
    }

    try {
      const response = await request(WIRELESS_PATH, {
        method: 'POST',
        body: JSON.stringify({
          serial: String(device.serial || ''),
          port: 5555,
        }),
      });
      const result = operationData(response);
      const data = result && result.data && typeof result.data === 'object'
        ? result.data
        : {};
      const code = resultCode(response);

      if (response.ok && result.success) {
        state.setupComplete = true;
        state.manualJoinPending = false;
        stopManualPolling();
        setWizardStep(5);
        setWizardCopy(
          `${normalized(data.model || deviceModel(device))} is connected through VRhotspot`,
          `You can disconnect the USB cable. Wireless ADB is using ${data.ssid || 'the dedicated VRhotspot network'}.`,
        );
        feedback('Wireless headset setup completed through VRhotspot.', 'success');
        if (data.target) selectWirelessTarget(String(data.target));
        return;
      }

      if (code === 'hotspot_not_running') {
        closeWizard();
        showHotspotPrecondition();
        return;
      }

      if (
        code === 'wifi_control_unavailable'
        || data.requires_manual_join === true
        || code === 'headset_not_on_vrhotspot'
        || code === 'headset_address_outside_hotspot_subnet'
      ) {
        state.manualJoinPending = true;
        const ssid = String(data.ssid || 'VRhotspot');
        setWizardStep(3);
        setWizardCopy(
          `Join ${ssid} inside the headset`,
          responseMessage(
            response,
            `Select ${ssid} in the headset Wi-Fi settings. Developer Hub will continue automatically.`,
          ),
        );
        feedback(`Waiting for the headset to join ${ssid}.`, 'loading');
        startManualJoinPolling();
        return;
      }

      state.manualJoinPending = false;
      stopManualPolling();
      setWizardStep(3);
      setWizardCopy(
        'Wireless setup needs attention',
        responseMessage(response, `Wireless setup failed: ${code}`),
      );
      feedback(responseMessage(response, `Wireless setup failed: ${code}`), 'error');
    } catch (error) {
      state.manualJoinPending = false;
      stopManualPolling();
      setWizardStep(3);
      setWizardCopy('Wireless setup needs attention', String(error.message || error));
      feedback(String(error.message || error), 'error');
    } finally {
      state.requestRunning = false;
      renderWizardState();
    }
  }

  function buildWizard() {
    if (el('devhubWirelessWizard')) return;

    const overlay = document.createElement('div');
    overlay.id = 'devhubWirelessWizard';
    overlay.className = 'devhub-wizard-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'devhubWizardHeading');

    const dialog = document.createElement('div');
    dialog.className = 'devhub-wizard-dialog';

    const header = document.createElement('div');
    header.className = 'devhub-wizard-header';
    const heading = document.createElement('h2');
    heading.id = 'devhubWizardHeading';
    heading.textContent = 'Connect headset to VRhotspot';
    const close = document.createElement('button');
    close.id = 'devhubWizardClose';
    close.type = 'button';
    close.className = 'btn sm secondary';
    close.textContent = 'Close';
    close.addEventListener('click', closeWizard);
    header.append(heading, close);

    const steps = document.createElement('div');
    steps.className = 'devhub-wizard-steps';
    [
      'Connect USB',
      'Approve debugging',
      'Join VRhotspot',
      'Enable wireless',
      'Complete',
    ].forEach((label, index) => {
      const step = document.createElement('div');
      step.className = 'devhub-wizard-step';
      step.dataset.step = String(index + 1);
      const number = document.createElement('span');
      number.textContent = String(index + 1);
      const copy = document.createElement('strong');
      copy.textContent = label;
      step.append(number, copy);
      steps.appendChild(step);
    });

    const body = document.createElement('div');
    body.className = 'devhub-wizard-body';
    const title = document.createElement('h3');
    title.id = 'devhubWizardTitle';
    const detail = document.createElement('p');
    detail.id = 'devhubWizardDetail';
    const policy = document.createElement('p');
    policy.className = 'devhub-wizard-policy';
    policy.textContent = 'Developer Hub will not expose wireless ADB through another Wi-Fi network.';
    const device = document.createElement('div');
    device.id = 'devhubWizardDevice';
    device.className = 'devhub-wizard-device';
    device.hidden = true;
    body.append(title, detail, policy, device);

    const footer = document.createElement('div');
    footer.className = 'devhub-wizard-footer';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn secondary';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', closeWizard);
    const action = document.createElement('button');
    action.id = 'devhubWizardAction';
    action.type = 'button';
    action.className = 'btn primary';
    action.textContent = 'Join VRhotspot & enable wireless';
    action.disabled = true;
    action.addEventListener('click', () => void runWirelessBootstrap(false));
    footer.append(cancel, action);

    dialog.append(header, steps, body, footer);
    overlay.appendChild(dialog);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) closeWizard();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !overlay.hidden) closeWizard();
    });
    document.body.appendChild(overlay);
  }

  function openWizard() {
    const modal = el('devhubWirelessWizard');
    if (!modal) return;
    state.setupComplete = false;
    state.manualJoinPending = false;
    state.requestRunning = false;
    state.usbDevices = [];
    state.selectedUsbSerial = '';
    modal.hidden = false;
    document.body.classList.add('devhub-modal-open');
    renderWizardState();
    startPolling();
    const close = el('devhubWizardClose');
    if (close) close.focus();
  }

  function closeWizard() {
    const modal = el('devhubWirelessWizard');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('devhub-modal-open');
    stopPolling();
    stopManualPolling();
    const trigger = el('devhubWirelessSetup');
    if (trigger) trigger.focus();
  }

  function buildHotspotPrecondition() {
    if (el('devhubHotspotPrecondition')) return;

    const overlay = document.createElement('div');
    overlay.id = 'devhubHotspotPrecondition';
    overlay.className = 'devhub-wizard-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'devhubHotspotPreconditionTitle');

    const dialog = document.createElement('div');
    dialog.className = 'devhub-wizard-dialog devhub-precondition-dialog';

    const header = document.createElement('div');
    header.className = 'devhub-wizard-header';
    const title = document.createElement('h2');
    title.id = 'devhubHotspotPreconditionTitle';
    title.textContent = 'Start VRhotspot first';
    header.appendChild(title);

    const body = document.createElement('div');
    body.className = 'devhub-wizard-body';
    const copy = document.createElement('p');
    copy.id = 'devhubHotspotPreconditionCopy';
    copy.textContent = (
      'Wireless headset setup only works over the dedicated VRhotspot network. '
      + 'Start the hotspot before continuing. Developer Hub will not enable '
      + 'wireless ADB through another Wi-Fi network.'
    );
    const status = document.createElement('div');
    status.id = 'devhubHotspotPreconditionStatus';
    status.className = 'devhub-precondition-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    body.append(copy, status);

    const footer = document.createElement('div');
    footer.className = 'devhub-wizard-footer';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn secondary';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', closeHotspotPrecondition);
    const openSetup = document.createElement('button');
    openSetup.id = 'devhubOpenHotspotSetup';
    openSetup.type = 'button';
    openSetup.className = 'btn secondary';
    openSetup.textContent = 'Open Hotspot Setup';
    openSetup.hidden = true;
    openSetup.addEventListener('click', navigateToHotspotSetup);
    const start = document.createElement('button');
    start.id = 'devhubStartHotspot';
    start.type = 'button';
    start.className = 'btn primary';
    start.textContent = 'Start Hotspot';
    start.addEventListener('click', () => void startHotspotFromPrecondition());
    footer.append(cancel, openSetup, start);

    dialog.append(header, body, footer);
    overlay.appendChild(dialog);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) closeHotspotPrecondition();
    });
    document.body.appendChild(overlay);
  }

  function showHotspotPrecondition() {
    const modal = el('devhubHotspotPrecondition');
    if (!modal) return;
    const status = el('devhubHotspotPreconditionStatus');
    const start = el('devhubStartHotspot');
    const openSetup = el('devhubOpenHotspotSetup');
    if (status) status.textContent = '';
    if (start) {
      start.hidden = false;
      start.disabled = false;
      start.textContent = 'Start Hotspot';
    }
    if (openSetup) openSetup.hidden = true;
    modal.hidden = false;
    document.body.classList.add('devhub-modal-open');
    if (start) start.focus();
  }

  function closeHotspotPrecondition() {
    const modal = el('devhubHotspotPrecondition');
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('devhub-modal-open');
    const trigger = el('devhubWirelessSetup');
    if (trigger) trigger.focus();
  }

  async function waitForHotspot(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() <= deadline) {
      const checked = await hotspotRunning();
      last = checked;
      if (checked.running) return checked;
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
    return last || { running: false, runtime: {}, response: null };
  }

  async function startHotspotFromPrecondition() {
    if (state.startRunning) return;
    state.startRunning = true;

    const start = el('devhubStartHotspot');
    const openSetup = el('devhubOpenHotspotSetup');
    const status = el('devhubHotspotPreconditionStatus');
    if (start) {
      start.disabled = true;
      start.textContent = 'Starting…';
    }
    if (openSetup) openSetup.hidden = true;
    if (status) status.textContent = 'Starting VRhotspot through the existing hotspot workflow…';

    try {
      if (typeof startHotspot === 'function') {
        await startHotspot(null, 'Developer Hub');
      } else {
        const authoritative = el('btnStart') || el('btnStartBasic');
        if (!authoritative) throw new Error('Hotspot setup is not available.');
        authoritative.click();
      }

      const checked = await waitForHotspot(30000);
      if (!checked.running) {
        throw new Error('VRhotspot did not reach the running state.');
      }

      closeHotspotPrecondition();
      openWizard();
    } catch (error) {
      if (status) {
        status.textContent = String(error.message || error);
        status.dataset.state = 'error';
      }
      if (start) {
        start.hidden = true;
        start.disabled = false;
        start.textContent = 'Start Hotspot';
      }
      if (openSetup) openSetup.hidden = false;
    } finally {
      state.startRunning = false;
    }
  }

  function navigateToHotspotSetup() {
    closeHotspotPrecondition();
    const nav = Array.from(document.querySelectorAll('.nav-item')).find((item) => {
      const text = String(item.textContent || '').trim().toLowerCase();
      return text.includes('set up hotspot');
    }) || document.querySelector('.nav-item[data-tab="status"]');
    if (nav) nav.click();
    window.setTimeout(() => {
      const target = el('btnStartBasic') || el('btnStart') || el('ssid');
      if (target) target.focus();
    }, 0);
  }

  async function beginWirelessSetup() {
    if (state.requestRunning || state.startRunning) return;
    const checked = await hotspotRunning();
    if (checked.running) {
      openWizard();
    } else {
      showHotspotPrecondition();
    }
  }

  function addSetupButton(devicesCard) {
    if (el('devhubWirelessSetup')) return;
    const header = devicesCard && devicesCard.querySelector('.card-header');
    if (!header) return;
    header.classList.add('devhub-headsets-header');

    const actions = document.createElement('div');
    actions.className = 'devhub-headsets-actions';
    const setup = document.createElement('button');
    setup.id = 'devhubWirelessSetup';
    setup.type = 'button';
    setup.className = 'btn sm primary';
    setup.textContent = 'Set up wireless headset';
    setup.addEventListener('click', () => void beginWirelessSetup());

    const disconnect = el('devhubDisconnect');
    if (disconnect && disconnect.parentNode === header) {
      header.insertBefore(actions, disconnect);
      actions.append(setup, disconnect);
    } else {
      actions.appendChild(setup);
      header.appendChild(actions);
    }
  }

  function mergeDiscoveryIntoDevices(discoveryCard, devicesCard) {
    const candidateList = el('devhubCandidateList');
    const deviceBody = devicesCard && devicesCard.querySelector('.card-body');
    if (!candidateList || !deviceBody || el('devhubUnifiedDiscovery')) {
      if (discoveryCard) discoveryCard.remove();
      return;
    }

    const heading = devicesCard.querySelector('.card-header h2');
    if (heading) heading.textContent = 'Headsets';

    const section = document.createElement('section');
    section.id = 'devhubUnifiedDiscovery';
    section.className = 'devhub-unified-discovery';
    const titleRow = document.createElement('div');
    titleRow.className = 'devhub-unified-section-title';
    const title = document.createElement('h3');
    title.textContent = 'Detected on VRhotspot';
    titleRow.append(
      title,
      infoTip(
        'Detection is informational. Wireless ADB is enabled only through the guarded USB setup.',
      ),
    );
    section.append(titleRow, candidateList);
    deviceBody.appendChild(section);
    discoveryCard.remove();

    const normalizeCandidates = () => {
      const rows = candidateList.querySelectorAll('.devhub-list-item');
      section.hidden = rows.length === 0;
      rows.forEach((row) => {
        const button = row.querySelector('button');
        if (button) button.remove();
        const meta = row.querySelector('.devhub-list-meta');
        if (meta && !meta.textContent.includes('Use USB setup')) {
          meta.textContent = `${meta.textContent} · Use USB setup to authorize`;
        }
      });
    };
    const observer = new MutationObserver(normalizeCandidates);
    observer.observe(candidateList, { childList: true, subtree: true });
    normalizeCandidates();
  }

  function removeManualConnection(pairingCard, connectCard, connectionPanel) {
    if (pairingCard) pairingCard.remove();
    if (connectCard) connectCard.remove();
    const existing = el('devhubAdvancedConnection');
    if (existing) existing.remove();

    const workspace = el('devhubWorkspace');
    const connectionTab = workspace
      ? workspace.querySelector('.devhub-workspace-tab[data-view="connection"]')
      : null;
    const connectionWasActive = !!(connectionTab && connectionTab.classList.contains('active'));
    if (connectionTab) connectionTab.remove();
    if (connectionPanel) connectionPanel.remove();

    try {
      if (sessionStorage.getItem(WORKSPACE_VIEW_KEY) === 'connection') {
        sessionStorage.setItem(WORKSPACE_VIEW_KEY, 'device');
      }
    } catch { }

    if (connectionWasActive && workspace) {
      const deviceTab = workspace.querySelector('.devhub-workspace-tab[data-view="device"]');
      if (deviceTab) deviceTab.click();
    }
  }

  function cleanAppsInterface() {
    const pathInput = el('devhubApkPath');
    if (pathInput) {
      pathInput.required = false;
      const details = pathInput.closest('.devhub-host-path');
      if (details) {
        details.remove();
      } else {
        const field = pathInput.closest('div');
        if (field) field.remove();
      }
    }

    const serial = el('devhubPackageSerial');
    if (serial) {
      serial.required = false;
      serial.hidden = true;
      serial.setAttribute('aria-hidden', 'true');
      serial.tabIndex = -1;
      const label = document.querySelector('label[for="devhubPackageSerial"]');
      if (label) label.hidden = true;
    }

    document.querySelectorAll('#tab-devhub .small.faded').forEach((node) => {
      const copy = String(node.textContent || '').toLowerCase();
      if (copy.includes('path must exist on the linux machine')) node.remove();
    });

    const advanced = el('devhubAdvancedConnection');
    if (advanced) advanced.remove();

    if (
      typeof companionAuthBridgeAvailable === 'function'
      && companionAuthBridgeAvailable()
    ) {
      const choose = document.querySelector('#devhubApkPicker .btn');
      const drop = document.querySelector('#devhubApkPicker .devhub-file-drop');
      const meta = document.querySelector('#devhubApkPicker .devhub-file-meta');
      if (choose) choose.disabled = true;
      if (drop) {
        drop.removeAttribute('role');
        drop.tabIndex = -1;
        drop.setAttribute('aria-disabled', 'true');
      }
      if (meta) {
        meta.textContent = (
          'APK file upload is temporarily unavailable in the desktop companion. '
          + 'Open the browser Web Portal to deploy an APK.'
        );
      }
    }
  }

  function normalizeDeviceStates() {
    document.querySelectorAll('#devhubDeviceList .devhub-list-item').forEach((row) => {
      const stateNode = row.querySelector('.devhub-device-state');
      if (!stateNode) return;
      const raw = String(row.dataset.devhubState || stateNode.textContent || '')
        .trim()
        .toLowerCase();
      const map = {
        unauthorized: 'Approve USB debugging',
        offline: 'Offline',
        recovery: 'Recovery mode',
      };
      if (raw === 'device') {
        stateNode.hidden = true;
        stateNode.setAttribute('aria-hidden', 'true');
        stateNode.textContent = 'device';
        return;
      }
      stateNode.hidden = false;
      stateNode.removeAttribute('aria-hidden');
      stateNode.textContent = map[raw] || 'Unavailable';
    });
  }

  function toolsModel(response) {
    const outer = operationData(response);
    const status = outer && outer.adb
      ? outer
      : (outer && outer.data && outer.data.adb ? outer.data : {});
    const adb = status && status.adb && typeof status.adb === 'object'
      ? status.adb
      : {};
    const managed = adb.managed && typeof adb.managed === 'object'
      ? adb.managed
      : {};
    return {
      source: String(adb.source || 'missing'),
      path: adb.path || '',
      managed,
      system: adb.system && typeof adb.system === 'object' ? adb.system : {},
    };
  }

  async function refreshToolsManagement() {
    const section = el('devhubToolsManagement');
    if (!section) return;

    const response = await request(TOOLS_PATH);
    if (!response.ok) return;
    const model = toolsModel(response);
    const install = el('devhubInstallTools');
    const remove = el('devhubRemoveTools');
    const accept = el('devhubAcceptToolsLicense');
    const checks = accept && accept.closest('.devhub-checks');
    const statusCopy = document.querySelector(
      '#tab-devhub .devhub-card-status .small',
    );

    const managedBroken = (
      model.managed.present === true
      && model.managed.verified === false
    );
    const managedReady = (
      model.source === 'managed'
      && model.managed.installed === true
      && model.managed.verified !== false
    );

    section.hidden = false;
    if (statusCopy) statusCopy.textContent = 'ADB runtime';

    if (managedBroken) {
      if (install) {
        install.hidden = false;
        install.textContent = 'Repair Managed ADB';
      }
      if (remove) remove.hidden = false;
      if (checks) checks.hidden = false;
      if (statusCopy) statusCopy.textContent = 'Managed ADB needs repair';
      return;
    }

    if (managedReady) {
      if (install) {
        install.hidden = false;
        install.textContent = 'Reinstall Managed ADB';
      }
      if (remove) remove.hidden = false;
      if (checks) checks.hidden = false;
      if (statusCopy) statusCopy.textContent = 'Managed ADB ready';
      return;
    }

    if (model.source === 'system') {
      section.hidden = true;
      if (install) install.hidden = true;
      if (remove) remove.hidden = true;
      if (checks) checks.hidden = true;
      if (statusCopy) statusCopy.textContent = 'System ADB ready';
      return;
    }

    if (install) {
      install.hidden = false;
      install.textContent = 'Install Managed ADB';
    }
    if (remove) remove.hidden = true;
    if (checks) checks.hidden = false;
    if (statusCopy) statusCopy.textContent = 'ADB unavailable';
  }

  function normalizeEmptyDeviceCopy() {
    const list = el('devhubDeviceList');
    const empty = list && list.querySelector('.devhub-empty');
    const message = 'No headsets are connected. Use “Set up wireless headset” to add one.';
    if (empty && empty.textContent !== message) empty.textContent = message;
  }

  function reconcile() {
    cleanAppsInterface();
    normalizeDeviceStates();
    normalizeEmptyDeviceCopy();
    void refreshToolsManagement();
  }

  function inject() {
    if (state.initialized) return true;
    const workspace = el('devhubWorkspace');
    if (!workspace) return false;

    const devicePanel = workspace.querySelector(
      '.devhub-workspace-panel[data-view="device"]',
    );
    const connectionPanel = workspace.querySelector(
      '.devhub-workspace-panel[data-view="connection"]',
    );
    if (!devicePanel || !connectionPanel) return false;

    const devicesCard = cardByTitle(devicePanel, 'ADB Devices');
    const discoveryCard = cardByTitle(connectionPanel, 'Network Discovery');
    const pairingCard = cardByTitle(connectionPanel, 'Wireless Pairing');
    const connectCard = cardByTitle(connectionPanel, 'Connect Headset');
    if (!devicesCard || !discoveryCard || !pairingCard || !connectCard) return false;

    buildWizard();
    buildHotspotPrecondition();
    addSetupButton(devicesCard);
    mergeDiscoveryIntoDevices(discoveryCard, devicesCard);
    removeManualConnection(pairingCard, connectCard, connectionPanel);

    state.initialized = true;
    document.documentElement.dataset.devhubVrhotspotOnlyReady = '1';

    state.observer = new MutationObserver(reconcile);
    state.observer.observe(workspace, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    reconcile();
    state.toolsTimer = window.setInterval(() => {
      const pane = el('tab-devhub');
      if (pane && pane.classList.contains('active') && !document.hidden) {
        reconcile();
      }
    }, 5000);
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
