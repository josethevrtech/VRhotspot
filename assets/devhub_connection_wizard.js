(function addDeveloperHubConnectionWizard() {
  'use strict';

  const DEVICES_PATH = '/v1/devbridge/adb/devices';
  const CONNECT_PATH = '/v1/devbridge/adb/connect';
  const ENABLE_WIRELESS_PATH = '/v1/devbridge/adb/enable-wireless';
  const WORKSPACE_VIEW_KEY = 'vrhs_devhub_workspace_view';
  const POLL_MS = 1600;

  let pollTimer = null;
  let selectedUsbSerial = '';
  let usbDevices = [];
  let setupComplete = false;
  let requestRunning = false;
  let candidateObserver = null;

  function el(id) {
    return document.getElementById(id);
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

  function responseMessage(response, fallback) {
    const result = operationData(response);
    return String(
      (result && result.message)
      || (response && response.json && response.json.message)
      || fallback,
    );
  }

  function feedback(message, state) {
    const node = el('devhubFeedback');
    if (!node) return;
    node.textContent = message;
    node.dataset.state = state || 'idle';
  }

  async function getDevices() {
    if (typeof api !== 'function') return [];
    const response = await api(DEVICES_PATH);
    if (!response.ok) throw new Error(responseMessage(response, 'Unable to inspect ADB devices.'));
    const data = nestedData(response);
    return Array.isArray(data.devices) ? data.devices : [];
  }

  function isUsbDevice(device) {
    const serial = String((device && device.serial) || '').trim();
    if (!serial || serial.includes(':')) return false;
    const props = device && device.properties && typeof device.properties === 'object'
      ? device.properties
      : {};
    return !!props.usb || !serial.includes(':');
  }

  function deviceModel(device) {
    const props = device && device.properties && typeof device.properties === 'object'
      ? device.properties
      : {};
    return normalized(props.model || props.product || 'Android XR headset');
  }

  function cardByTitle(panel, title) {
    return Array.from(panel.querySelectorAll(':scope > .devhub-workspace-grid > .card')).find((card) => {
      const heading = card.querySelector('.card-header h2');
      return heading && heading.textContent.trim() === title;
    }) || null;
  }

  function setWizardStep(step) {
    document.querySelectorAll('.devhub-wizard-step').forEach((node) => {
      const value = Number(node.dataset.step);
      node.classList.toggle('current', value === step);
      node.classList.toggle('complete', value < step);
    });
  }

  function setWizardCopy(title, detail) {
    const titleNode = el('devhubWizardTitle');
    const detailNode = el('devhubWizardDetail');
    if (titleNode) titleNode.textContent = title;
    if (detailNode) detailNode.textContent = detail;
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

    if (usbDevices.length > 1) {
      const label = document.createElement('label');
      label.textContent = 'USB headset';
      const select = document.createElement('select');
      select.id = 'devhubWizardUsbSelect';
      for (const candidate of usbDevices) {
        const option = document.createElement('option');
        option.value = String(candidate.serial || '');
        option.textContent = `${deviceModel(candidate)} — ${candidate.state || 'unknown'}`;
        option.selected = option.value === selectedUsbSerial;
        select.appendChild(option);
      }
      select.addEventListener('change', () => {
        selectedUsbSerial = select.value;
        renderWizardState();
      });
      label.appendChild(select);
      box.appendChild(label);
    }
  }

  function chosenUsbDevice() {
    return usbDevices.find((device) => String(device.serial || '') === selectedUsbSerial)
      || usbDevices.find((device) => device.state === 'device')
      || usbDevices[0]
      || null;
  }

  function renderWizardState() {
    const action = el('devhubWizardAction');
    const device = chosenUsbDevice();
    if (!action) return;

    if (setupComplete) {
      setWizardStep(4);
      action.disabled = false;
      action.textContent = 'Done';
      renderUsbDevice(device);
      return;
    }

    renderUsbDevice(device);
    action.textContent = 'Enable Wireless ADB';

    if (!device) {
      setWizardStep(1);
      setWizardCopy(
        'Connect the headset by USB',
        'Use a data-capable USB cable. VRhotspot will detect the headset automatically.',
      );
      action.disabled = true;
      return;
    }

    if (String(device.state || '').toLowerCase() === 'unauthorized') {
      setWizardStep(2);
      setWizardCopy(
        'Approve USB debugging inside the headset',
        'Put on the headset, enable “Always allow from this computer,” and select Allow.',
      );
      action.disabled = true;
      return;
    }

    if (String(device.state || '').toLowerCase() !== 'device') {
      setWizardStep(1);
      setWizardCopy(
        'Waiting for the USB headset',
        `The headset currently reports “${device.state || 'unknown'}.” Reconnect the cable and keep it awake.`,
      );
      action.disabled = true;
      return;
    }

    setWizardStep(3);
    setWizardCopy(
      `${deviceModel(device)} is ready`,
      'VRhotspot will read its Wi-Fi address, enable wireless ADB, and connect it automatically.',
    );
    action.disabled = requestRunning;
  }

  async function pollUsbDevices() {
    if (requestRunning || !el('devhubWirelessWizard') || el('devhubWirelessWizard').hidden) return;
    try {
      usbDevices = (await getDevices()).filter(isUsbDevice);
      if (!usbDevices.some((device) => String(device.serial || '') === selectedUsbSerial)) {
        const preferred = usbDevices.find((device) => device.state === 'device') || usbDevices[0];
        selectedUsbSerial = preferred ? String(preferred.serial || '') : '';
      }
      renderWizardState();
    } catch (error) {
      setWizardCopy('Unable to inspect USB devices', String(error.message || error));
    }
  }

  async function selectWirelessTarget(target) {
    const refresh = el('devhubRefresh');
    if (refresh) refresh.click();

    for (let attempt = 0; attempt < 8; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 450));
      const rows = document.querySelectorAll('#devhubDeviceList .devhub-list-item');
      const row = Array.from(rows).find((candidate) => {
        if (candidate.dataset.devhubSerial === target) return true;
        const address = candidate.querySelector('.devhub-device-address');
        return address && String(address.textContent || '').includes(target);
      });
      if (!row) continue;
      const choose = row.querySelector('button');
      if (choose) choose.click();
      return;
    }
  }

  async function enableWireless() {
    if (setupComplete) {
      closeWizard();
      return;
    }

    const device = chosenUsbDevice();
    if (!device || device.state !== 'device' || requestRunning) return;

    requestRunning = true;
    renderWizardState();
    setWizardCopy(
      'Enabling wireless connection',
      'Keep the headset awake and connected to Wi-Fi. This usually takes only a few seconds.',
    );

    try {
      const response = await api(ENABLE_WIRELESS_PATH, {
        method: 'POST',
        body: JSON.stringify({ serial: String(device.serial || ''), port: 5555 }),
      });
      const result = operationData(response);
      if (!response.ok || !result.success) {
        throw new Error(responseMessage(response, 'Wireless setup failed.'));
      }

      const data = result.data && typeof result.data === 'object' ? result.data : {};
      const target = String(data.target || '');
      setupComplete = true;
      setWizardCopy(
        `${normalized(data.model || deviceModel(device))} is connected wirelessly`,
        'You can disconnect the USB cable. VRhotspot will keep using the wireless connection.',
      );
      feedback('Wireless headset setup completed.', 'success');
      renderWizardState();
      if (target) await selectWirelessTarget(target);
    } catch (error) {
      setWizardStep(3);
      setWizardCopy('Wireless setup needs attention', String(error.message || error));
      feedback(String(error.message || error), 'error');
    } finally {
      requestRunning = false;
      renderWizardState();
    }
  }

  function startPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    void pollUsbDevices();
    pollTimer = window.setInterval(pollUsbDevices, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function openWizard() {
    const modal = el('devhubWirelessWizard');
    if (!modal) return;
    setupComplete = false;
    requestRunning = false;
    usbDevices = [];
    selectedUsbSerial = '';
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
    const trigger = el('devhubWirelessSetup');
    if (trigger) trigger.focus();
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
    heading.textContent = 'Set up wireless headset';
    const close = document.createElement('button');
    close.id = 'devhubWizardClose';
    close.type = 'button';
    close.className = 'btn sm secondary';
    close.textContent = 'Close';
    close.addEventListener('click', closeWizard);
    header.append(heading, close);

    const steps = document.createElement('div');
    steps.className = 'devhub-wizard-steps';
    ['Connect USB', 'Approve', 'Enable wireless', 'Complete'].forEach((label, index) => {
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
    const device = document.createElement('div');
    device.id = 'devhubWizardDevice';
    device.className = 'devhub-wizard-device';
    device.hidden = true;
    body.append(title, detail, device);

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
    action.textContent = 'Enable Wireless ADB';
    action.disabled = true;
    action.addEventListener('click', () => void enableWireless());
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

  async function connectCandidate(address, button) {
    if (!address || requestRunning) return;
    requestRunning = true;
    button.disabled = true;
    feedback(`Connecting to discovered headset ${address}...`, 'loading');
    try {
      const response = await api(CONNECT_PATH, {
        method: 'POST',
        body: JSON.stringify({ ip: address, port: 5555 }),
      });
      if (!response.ok) throw new Error(responseMessage(response, 'Connection failed.'));
      feedback('Discovered headset connected.', 'success');
      const refresh = el('devhubRefresh');
      if (refresh) refresh.click();
    } catch (error) {
      feedback(String(error.message || error), 'error');
    } finally {
      requestRunning = false;
      button.disabled = false;
    }
  }

  function prepareCandidateRows() {
    const list = el('devhubCandidateList');
    if (!list) return;

    list.querySelectorAll('.devhub-list-item').forEach((row) => {
      const title = row.querySelector('.devhub-list-title');
      const button = row.querySelector('button');
      if (!title || !button || row.dataset.wizardCandidateReady === '1') return;
      const address = String(title.textContent || '').trim();
      row.dataset.wizardCandidateReady = '1';
      row.dataset.devhubCandidateAddress = address;
      title.classList.add('devhub-sensitive-identifier');
      button.textContent = 'Connect';
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        void connectCandidate(address, button);
      }, { capture: true });
    });

    const section = el('devhubUnifiedDiscovery');
    if (section) {
      section.hidden = !list.querySelector('.devhub-list-item');
    }
  }

  function mergeDiscoveryIntoDevices(discoveryCard, devicesCard) {
    const candidateList = el('devhubCandidateList');
    const deviceBody = devicesCard.querySelector('.card-body');
    if (!candidateList || !deviceBody) return;

    const heading = devicesCard.querySelector('.card-header h2');
    if (heading) heading.textContent = 'Headsets';

    const section = document.createElement('section');
    section.id = 'devhubUnifiedDiscovery';
    section.className = 'devhub-unified-discovery';
    section.hidden = true;
    const titleRow = document.createElement('div');
    titleRow.className = 'devhub-unified-section-title';
    const title = document.createElement('h3');
    title.textContent = 'Available on network';
    titleRow.append(
      title,
      infoTip('Headsets discovered on the VRhotspot network appear here when they are available.'),
    );
    section.append(titleRow, candidateList);
    deviceBody.appendChild(section);
    discoveryCard.remove();

    candidateObserver = new MutationObserver(prepareCandidateRows);
    candidateObserver.observe(candidateList, { childList: true, subtree: true });
    prepareCandidateRows();
  }

  function addSetupButton(devicesCard) {
    if (el('devhubWirelessSetup')) return;
    const header = devicesCard.querySelector('.card-header');
    if (!header) return;
    header.classList.add('devhub-headsets-header');

    const actions = document.createElement('div');
    actions.className = 'devhub-headsets-actions';
    const setup = document.createElement('button');
    setup.id = 'devhubWirelessSetup';
    setup.type = 'button';
    setup.className = 'btn sm primary';
    setup.textContent = 'Set up wireless headset';
    setup.addEventListener('click', openWizard);

    const disconnect = el('devhubDisconnect');
    if (disconnect && disconnect.parentNode === header) {
      header.insertBefore(actions, disconnect);
      actions.append(setup, disconnect);
    } else {
      actions.appendChild(setup);
      header.appendChild(actions);
    }
  }

  function moveManualConnectionToTools(pairingCard, connectCard, toolsPanel) {
    const toolsGrid = toolsPanel.querySelector('.devhub-workspace-grid');
    if (!toolsGrid || el('devhubAdvancedConnection')) return;

    const card = document.createElement('section');
    card.id = 'devhubAdvancedConnection';
    card.className = 'card devhub-advanced-connection';
    const header = document.createElement('div');
    header.className = 'card-header';
    const title = document.createElement('h2');
    title.textContent = 'Advanced ADB';
    header.append(title, infoTip('Manual pairing and IP connection are retained for non-standard Android devices and troubleshooting.'));

    const body = document.createElement('div');
    body.className = 'card-body';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Manual pairing and IP connection';
    const grid = document.createElement('div');
    grid.className = 'devhub-advanced-connection-grid';

    const pairingTitle = pairingCard.querySelector('.card-header h2');
    const connectTitle = connectCard.querySelector('.card-header h2');
    if (pairingTitle) pairingTitle.textContent = 'Pair with code';
    if (connectTitle) connectTitle.textContent = 'Connect by IP';
    grid.append(pairingCard, connectCard);
    details.append(summary, grid);
    body.appendChild(details);
    card.append(header, body);
    toolsGrid.appendChild(card);
  }

  function normalizeEmptyDeviceCopy() {
    const list = el('devhubDeviceList');
    const empty = list && list.querySelector('.devhub-empty');
    const message = 'No headsets are connected. Use “Set up wireless headset” to add one.';
    if (empty && empty.textContent !== message) {
      empty.textContent = message;
    }
  }

  function inject() {
    if (document.documentElement.dataset.devhubConnectionWizardReady === '1') return true;
    const workspace = el('devhubWorkspace');
    if (!workspace) return false;

    const devicePanel = workspace.querySelector('.devhub-workspace-panel[data-view="device"]');
    const connectionPanel = workspace.querySelector('.devhub-workspace-panel[data-view="connection"]');
    const toolsPanel = workspace.querySelector('.devhub-workspace-panel[data-view="tools"]');
    if (!devicePanel || !connectionPanel || !toolsPanel) return false;

    const devicesCard = cardByTitle(devicePanel, 'ADB Devices');
    const discoveryCard = cardByTitle(connectionPanel, 'Network Discovery');
    const pairingCard = cardByTitle(connectionPanel, 'Wireless Pairing');
    const connectCard = cardByTitle(connectionPanel, 'Connect Headset');
    if (!devicesCard || !discoveryCard || !pairingCard || !connectCard) return false;

    document.documentElement.dataset.devhubConnectionWizardReady = '1';
    buildWizard();
    addSetupButton(devicesCard);
    mergeDiscoveryIntoDevices(discoveryCard, devicesCard);
    moveManualConnectionToTools(pairingCard, connectCard, toolsPanel);

    const connectionTab = workspace.querySelector('.devhub-workspace-tab[data-view="connection"]');
    const connectionWasActive = connectionTab && connectionTab.classList.contains('active');
    if (connectionTab) connectionTab.remove();
    connectionPanel.remove();

    try {
      if (sessionStorage.getItem(WORKSPACE_VIEW_KEY) === 'connection') {
        sessionStorage.setItem(WORKSPACE_VIEW_KEY, 'device');
      }
    } catch { }
    if (connectionWasActive) {
      const deviceTab = workspace.querySelector('.devhub-workspace-tab[data-view="device"]');
      if (deviceTab) deviceTab.click();
    }

    const deviceList = el('devhubDeviceList');
    if (deviceList) {
      const observer = new MutationObserver(normalizeEmptyDeviceCopy);
      observer.observe(deviceList, { childList: true });
    }
    normalizeEmptyDeviceCopy();
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
