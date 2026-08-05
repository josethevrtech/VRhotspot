(function () {
  'use strict';

  const PATHS = {
    tools: '/v1/devbridge/tools/status',
    networkDevices: '/v1/devbridge/devices',
    version: '/v1/devbridge/adb/version',
    devices: '/v1/devbridge/adb/devices',
    pair: '/v1/devbridge/adb/pair',
    connect: '/v1/devbridge/adb/connect',
    disconnect: '/v1/devbridge/adb/disconnect',
    packages: '/v1/devbridge/adb/packages',
    install: '/v1/devbridge/adb/install',
    launch: '/v1/devbridge/adb/launch',
    stop: '/v1/devbridge/adb/stop',
    clearData: '/v1/devbridge/adb/clear-data',
    uninstall: '/v1/devbridge/adb/uninstall',
  };

  const state = {
    initialized: false,
    loading: false,
    devices: [],
    candidates: [],
    packages: [],
    selectedSerial: '',
    selectedPackage: '',
  };

  function el(id) {
    return document.getElementById(id);
  }

  function text(id, value, fallback = '--') {
    const node = el(id);
    if (!node) return;
    const rendered = (value === null || value === undefined || value === '')
      ? fallback
      : String(value);
    node.textContent = rendered;
  }

  function setFeedback(message, status = 'idle') {
    const node = el('devhubFeedback');
    if (!node) return;
    node.textContent = message || '';
    node.dataset.state = status;
  }

  function operationData(response) {
    if (!response || !response.json || !response.json.data) return null;
    return response.json.data;
  }

  function nestedData(response) {
    const result = operationData(response);
    return result && result.data && typeof result.data === 'object' ? result.data : {};
  }

  function responseCode(response) {
    if (response && response.json) {
      const result = response.json.data;
      if (result && result.result_code) return String(result.result_code);
      if (response.json.result_code) return String(response.json.result_code);
    }
    return response ? `HTTP ${response.status}` : 'request_failed';
  }

  async function request(path, options = {}) {
    if (typeof api !== 'function') {
      return { ok: false, status: 503, json: null, raw: '' };
    }
    return api(path, options);
  }

  async function post(path, body) {
    return request(path, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    });
  }

  function button(label, className, onClick) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = className || 'btn sm secondary';
    node.textContent = label;
    node.addEventListener('click', onClick);
    return node;
  }

  function emptyList(container, message) {
    container.replaceChildren();
    const node = document.createElement('div');
    node.className = 'devhub-empty';
    node.textContent = message;
    container.appendChild(node);
  }

  function deviceMeta(device) {
    const props = device && device.properties && typeof device.properties === 'object'
      ? device.properties
      : {};
    const parts = [];
    if (device && device.state) parts.push(device.state);
    if (props.model) parts.push(`model ${props.model}`);
    if (props.product) parts.push(`product ${props.product}`);
    if (props.device) parts.push(`device ${props.device}`);
    return parts.join(' · ') || 'ADB device';
  }

  function selectDevice(serial) {
    state.selectedSerial = serial || '';
    text('devhubSelectedDevice', state.selectedSerial, 'No device selected');
    const packageSerial = el('devhubPackageSerial');
    if (packageSerial) packageSerial.value = state.selectedSerial;
    renderDevices();
    state.packages = [];
    state.selectedPackage = '';
    text('devhubSelectedPackage', '', 'No package selected');
    renderPackages();
  }

  function renderDevices() {
    const container = el('devhubDeviceList');
    if (!container) return;
    if (!state.devices.length) {
      emptyList(container, 'No ADB devices are connected yet. Pair or connect a headset, then refresh.');
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const device of state.devices) {
      const serial = String(device.serial || '');
      const row = document.createElement('div');
      row.className = 'devhub-list-item';
      if (serial && serial === state.selectedSerial) row.classList.add('selected');

      const main = document.createElement('div');
      main.className = 'devhub-list-main';
      const title = document.createElement('div');
      title.className = 'devhub-list-title';
      title.textContent = serial || 'Unknown serial';
      const meta = document.createElement('div');
      meta.className = 'devhub-list-meta';
      meta.textContent = deviceMeta(device);
      main.append(title, meta);

      const choose = button(
        serial === state.selectedSerial ? 'Selected' : 'Select',
        serial === state.selectedSerial ? 'btn sm primary' : 'btn sm secondary',
        () => selectDevice(serial),
      );
      choose.disabled = !serial;
      row.append(main, choose);
      fragment.appendChild(row);
    }
    container.replaceChildren(fragment);
  }

  function candidateAddress(candidate) {
    if (!candidate || typeof candidate !== 'object') return '';
    return String(candidate.ip || candidate.ipv4 || candidate.address || '').trim();
  }

  function renderCandidates() {
    const container = el('devhubCandidateList');
    if (!container) return;
    if (!state.candidates.length) {
      emptyList(container, 'No headset candidates were discovered on the VRhotspot network.');
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const candidate of state.candidates) {
      const address = candidateAddress(candidate);
      const row = document.createElement('div');
      row.className = 'devhub-list-item';
      const main = document.createElement('div');
      main.className = 'devhub-list-main';
      const title = document.createElement('div');
      title.className = 'devhub-list-title';
      title.textContent = address || 'Unknown address';
      const meta = document.createElement('div');
      meta.className = 'devhub-list-meta';
      const name = candidate.hostname || candidate.name || candidate.classification || 'Network device';
      const reachable = candidate.adb_reachable === true || candidate.adb_port_reachable === true
        ? 'ADB reachable'
        : 'ADB not connected';
      meta.textContent = `${name} · ${reachable}`;
      main.append(title, meta);
      const use = button('Use address', 'btn sm secondary', () => {
        const pairIp = el('devhubPairIp');
        const connectIp = el('devhubConnectIp');
        if (pairIp) pairIp.value = address;
        if (connectIp) connectIp.value = address;
        setFeedback(`Loaded ${address} into the pairing and connection forms.`, 'success');
      });
      use.disabled = !address;
      row.append(main, use);
      fragment.appendChild(row);
    }
    container.replaceChildren(fragment);
  }

  function selectPackage(packageName) {
    state.selectedPackage = packageName || '';
    const input = el('devhubPackageName');
    if (input) input.value = state.selectedPackage;
    text('devhubSelectedPackage', state.selectedPackage, 'No package selected');
    renderPackages();
  }

  function renderPackages() {
    const container = el('devhubPackageList');
    if (!container) return;
    if (!state.packages.length) {
      emptyList(container, state.selectedSerial
        ? 'No packages loaded for the selected headset.'
        : 'Select a connected headset to inspect applications.');
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const packageName of state.packages) {
      const row = document.createElement('div');
      row.className = 'devhub-list-item';
      if (packageName === state.selectedPackage) row.classList.add('selected');
      const main = document.createElement('div');
      main.className = 'devhub-list-main';
      const title = document.createElement('div');
      title.className = 'devhub-list-title';
      title.textContent = packageName;
      main.appendChild(title);
      row.append(
        main,
        button(
          packageName === state.selectedPackage ? 'Selected' : 'Select',
          packageName === state.selectedPackage ? 'btn sm primary' : 'btn sm secondary',
          () => selectPackage(packageName),
        ),
      );
      fragment.appendChild(row);
    }
    container.replaceChildren(fragment);
  }

  function setToolsStatus(tools, versionResult) {
    const adb = tools && tools.adb && typeof tools.adb === 'object' ? tools.adb : {};
    const version = versionResult && versionResult.stdout ? versionResult.stdout.split('\n')[0] : '';
    text('devhubToolSource', adb.source, 'missing');
    text('devhubToolPath', adb.path, 'Not installed');
    text('devhubToolVersion', version, 'Unavailable');
    const dot = el('devhubToolDot');
    if (dot) {
      dot.classList.remove('ready', 'error');
      dot.classList.add(adb.path ? 'ready' : 'error');
    }
  }

  async function refreshConsole() {
    if (state.loading) return;
    state.loading = true;
    const refreshButton = el('devhubRefresh');
    if (refreshButton) refreshButton.disabled = true;
    setFeedback('Refreshing Developer Hub state...', 'loading');

    try {
      const [toolsResponse, versionResponse, devicesResponse, candidatesResponse] = await Promise.all([
        request(PATHS.tools),
        request(PATHS.version),
        request(PATHS.devices),
        request(PATHS.networkDevices),
      ]);

      const tools = toolsResponse.ok ? operationData(toolsResponse) : {};
      const versionResult = versionResponse.ok ? operationData(versionResponse) : {};
      setToolsStatus(tools, versionResult);

      const deviceData = devicesResponse.ok ? nestedData(devicesResponse) : {};
      state.devices = Array.isArray(deviceData.devices) ? deviceData.devices : [];
      if (state.selectedSerial && !state.devices.some((device) => device.serial === state.selectedSerial)) {
        state.selectedSerial = '';
      }
      if (!state.selectedSerial) {
        const connected = state.devices.find((device) => device.state === 'device') || state.devices[0];
        if (connected) state.selectedSerial = String(connected.serial || '');
      }
      text('devhubSelectedDevice', state.selectedSerial, 'No device selected');
      const packageSerial = el('devhubPackageSerial');
      if (packageSerial) packageSerial.value = state.selectedSerial;
      renderDevices();

      const candidateEnvelope = operationData(candidatesResponse) || {};
      state.candidates = Array.isArray(candidateEnvelope.devices)
        ? candidateEnvelope.devices
        : (candidateEnvelope.data && Array.isArray(candidateEnvelope.data.devices)
          ? candidateEnvelope.data.devices
          : []);
      renderCandidates();

      if (!toolsResponse.ok) {
        setFeedback(`Developer tools status failed: ${responseCode(toolsResponse)}`, 'error');
      } else if (!devicesResponse.ok) {
        setFeedback(`ADB device refresh failed: ${responseCode(devicesResponse)}`, 'error');
      } else {
        setFeedback(
          `Ready. ${state.devices.length} ADB device(s), ${state.candidates.length} network candidate(s).`,
          'success',
        );
      }
    } catch {
      setFeedback('Unable to refresh Developer Hub. Check the daemon connection.', 'error');
    } finally {
      state.loading = false;
      if (refreshButton) refreshButton.disabled = false;
    }
  }

  async function runOperation(label, path, body, options = {}) {
    setFeedback(`${label}...`, 'loading');
    const response = await post(path, body);
    if (!response.ok) {
      setFeedback(`${label} failed: ${responseCode(response)}`, 'error');
      return null;
    }
    const result = operationData(response) || {};
    setFeedback(`${label}: ${result.result_code || 'ok'}`, 'success');
    if (options.refreshDevices) await refreshConsole();
    if (options.refreshPackages) await loadPackages();
    return result;
  }

  async function loadPackages() {
    const serial = state.selectedSerial || (el('devhubPackageSerial') || {}).value || '';
    if (!serial) {
      setFeedback('Select a connected headset before loading packages.', 'error');
      return;
    }
    const includeAll = !!(el('devhubIncludeSystem') && el('devhubIncludeSystem').checked);
    setFeedback(`Loading packages from ${serial}...`, 'loading');
    const query = new URLSearchParams({
      serial,
      third_party_only: includeAll ? '0' : '1',
    });
    const response = await request(`${PATHS.packages}?${query.toString()}`);
    if (!response.ok) {
      setFeedback(`Package inventory failed: ${responseCode(response)}`, 'error');
      return;
    }
    const data = nestedData(response);
    state.packages = Array.isArray(data.packages) ? data.packages : [];
    if (state.selectedPackage && !state.packages.includes(state.selectedPackage)) {
      state.selectedPackage = '';
    }
    renderPackages();
    setFeedback(`Loaded ${state.packages.length} package(s) from ${serial}.`, 'success');
  }

  function currentSerial() {
    const field = el('devhubPackageSerial');
    return String((field && field.value) || state.selectedSerial || '').trim();
  }

  function currentPackage() {
    const field = el('devhubPackageName');
    return String((field && field.value) || state.selectedPackage || '').trim();
  }

  function wireForms() {
    el('devhubPairForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const code = el('devhubPairCode');
      const body = {
        ip: el('devhubPairIp').value.trim(),
        port: Number(el('devhubPairPort').value),
        pairing_code: code.value.trim(),
      };
      try {
        await runOperation('Pairing headset', PATHS.pair, body, { refreshDevices: true });
      } finally {
        code.value = '';
      }
    });

    el('devhubConnectForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      await runOperation('Connecting headset', PATHS.connect, {
        ip: el('devhubConnectIp').value.trim(),
        port: Number(el('devhubConnectPort').value),
      }, { refreshDevices: true });
    });

    el('devhubDisconnect').addEventListener('click', async () => {
      if (!state.selectedSerial) {
        setFeedback('Select a connected headset before disconnecting.', 'error');
        return;
      }
      await runOperation('Disconnecting headset', PATHS.disconnect, {
        serial: state.selectedSerial,
      }, { refreshDevices: true });
    });

    el('devhubLoadPackages').addEventListener('click', loadPackages);

    el('devhubInstallForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const serial = currentSerial();
      if (!serial) {
        setFeedback('Select a connected headset before installing an APK.', 'error');
        return;
      }
      await runOperation('Installing APK', PATHS.install, {
        serial,
        apk_path: el('devhubApkPath').value.trim(),
        reinstall: el('devhubReinstall').checked,
        grant_permissions: el('devhubGrantPermissions').checked,
      }, { refreshPackages: true });
    });

    const packageAction = async (label, path, extra = {}) => {
      const serial = currentSerial();
      const packageName = currentPackage();
      if (!serial || !packageName) {
        setFeedback('Select a headset and application package first.', 'error');
        return;
      }
      await runOperation(label, path, Object.assign({
        serial,
        package: packageName,
      }, extra), { refreshPackages: path === PATHS.uninstall });
    };

    el('devhubLaunch').addEventListener('click', () => packageAction('Launching application', PATHS.launch));
    el('devhubStop').addEventListener('click', () => packageAction('Stopping application', PATHS.stop));
    el('devhubClearData').addEventListener('click', () => packageAction('Clearing application data', PATHS.clearData));
    el('devhubUninstall').addEventListener('click', () => packageAction(
      'Uninstalling application',
      PATHS.uninstall,
      { keep_data: el('devhubKeepData').checked },
    ));
    el('devhubRefresh').addEventListener('click', refreshConsole);
  }

  function paneMarkup() {
    return `
      <div class="devhub-page">
        <div class="devhub-heading">
          <div class="devhub-heading-copy">
            <h2>Developer Hub</h2>
            <div class="small faded mt-6">Pair, connect, deploy, launch, inspect, and manage standalone XR headsets from Linux.</div>
          </div>
          <button class="btn primary" id="devhubRefresh" type="button">Refresh Hub</button>
        </div>

        <div id="devhubFeedback" class="devhub-feedback" data-state="idle" role="status" aria-live="polite">Open the Hub or refresh to inspect the current development environment.</div>

        <div class="devhub-grid">
          <section class="card devhub-span">
            <div class="card-header">
              <h2>Development Tools</h2>
              <div class="devhub-card-status"><span id="devhubToolDot" class="devhub-status-dot"></span><span class="small">ADB runtime</span></div>
            </div>
            <div class="card-body devhub-tools-grid">
              <div><div class="small muted">Source</div><div id="devhubToolSource" class="devhub-tool-value">--</div></div>
              <div><div class="small muted">Executable</div><div id="devhubToolPath" class="devhub-tool-value">--</div></div>
              <div><div class="small muted">Version</div><div id="devhubToolVersion" class="devhub-tool-value">--</div></div>
            </div>
          </section>

          <section class="card">
            <div class="card-header"><h2>Network Discovery</h2></div>
            <div class="card-body"><div id="devhubCandidateList" class="devhub-list"></div></div>
          </section>

          <section class="card">
            <div class="card-header"><h2>ADB Devices</h2><button class="btn sm secondary" id="devhubDisconnect" type="button">Disconnect selected</button></div>
            <div class="card-body">
              <div class="small muted">Selected headset</div>
              <div id="devhubSelectedDevice" class="devhub-selected mt-6">No device selected</div>
              <div id="devhubDeviceList" class="devhub-list mt-12"></div>
            </div>
          </section>

          <section class="card">
            <div class="card-header"><h2>Wireless Pairing</h2></div>
            <div class="card-body">
              <form id="devhubPairForm" class="devhub-form-grid" autocomplete="off">
                <div><label for="devhubPairIp">Headset IP</label><input id="devhubPairIp" inputmode="decimal" placeholder="192.168.68.24" required /></div>
                <div><label for="devhubPairPort">Pairing port</label><input id="devhubPairPort" type="number" min="1" max="65535" placeholder="37143" required /></div>
                <div class="devhub-wide"><label for="devhubPairCode">Six-digit code</label><input id="devhubPairCode" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="Shown inside the headset" required /></div>
                <div class="devhub-wide devhub-actions"><button class="btn primary" type="submit">Pair Headset</button></div>
              </form>
            </div>
          </section>

          <section class="card">
            <div class="card-header"><h2>Connect Headset</h2></div>
            <div class="card-body">
              <form id="devhubConnectForm" class="devhub-form-grid" autocomplete="off">
                <div><label for="devhubConnectIp">Headset IP</label><input id="devhubConnectIp" inputmode="decimal" placeholder="192.168.68.24" required /></div>
                <div><label for="devhubConnectPort">ADB port</label><input id="devhubConnectPort" type="number" min="1" max="65535" value="5555" required /></div>
                <div class="devhub-wide devhub-actions"><button class="btn primary" type="submit">Connect</button></div>
              </form>
            </div>
          </section>

          <section class="card devhub-span">
            <div class="card-header"><h2>APK Deployment</h2></div>
            <div class="card-body">
              <form id="devhubInstallForm" class="devhub-form-grid" autocomplete="off">
                <div><label for="devhubPackageSerial">Target serial</label><input id="devhubPackageSerial" placeholder="Select a connected device" required /></div>
                <div><label for="devhubApkPath">APK path on Linux host</label><input id="devhubApkPath" placeholder="/home/deck/Builds/application.apk" required /></div>
                <div class="devhub-wide devhub-checks">
                  <label><input id="devhubReinstall" type="checkbox" checked /> Update existing install</label>
                  <label><input id="devhubGrantPermissions" type="checkbox" /> Grant runtime permissions</label>
                </div>
                <div class="devhub-wide devhub-actions"><button class="btn primary" type="submit">Install APK</button></div>
              </form>
              <div class="small faded mt-8">The path must exist on the Linux machine running VRhotspot. Native file selection is the next desktop-companion integration.</div>
            </div>
          </section>

          <section class="card">
            <div class="card-header"><h2>Installed Applications</h2><button class="btn sm secondary" id="devhubLoadPackages" type="button">Load packages</button></div>
            <div class="card-body">
              <div class="devhub-checks"><label><input id="devhubIncludeSystem" type="checkbox" /> Include system packages</label></div>
              <div id="devhubPackageList" class="devhub-list mt-12"></div>
            </div>
          </section>

          <section class="card">
            <div class="card-header"><h2>Application Control</h2></div>
            <div class="card-body">
              <div class="devhub-field"><label for="devhubPackageName">Package name</label><input id="devhubPackageName" placeholder="com.example.application" /></div>
              <div class="small muted mt-12">Selected package</div>
              <div id="devhubSelectedPackage" class="devhub-selected mt-6">No package selected</div>
              <div class="devhub-actions mt-12">
                <button class="btn primary" id="devhubLaunch" type="button">Launch</button>
                <button class="btn secondary" id="devhubStop" type="button">Force Stop</button>
                <button class="btn secondary" id="devhubClearData" type="button">Clear Data</button>
                <button class="btn danger" id="devhubUninstall" type="button">Uninstall</button>
              </div>
              <div class="devhub-checks mt-12"><label><input id="devhubKeepData" type="checkbox" /> Keep app data when uninstalling</label></div>
            </div>
          </section>
        </div>
      </div>`;
  }

  function activateTab() {
    // Navigation state is owned by ui.js applyNavSelection (delegated on the
    // nav list); this handler only refreshes the console data on entry.
    void refreshConsole();
  }

  function injectConsole() {
    if (state.initialized || el('tab-devhub')) return;
    const navList = document.querySelector('.nav-list');
    const contentArea = document.querySelector('.content-area');
    if (!navList || !contentArea) return;

    const nav = document.createElement('li');
    nav.className = 'nav-item';
    nav.dataset.tab = 'devhub';
    const icon = document.createElement('span');
    icon.className = 'icon devhub-nav-icon';
    icon.textContent = 'XR';
    nav.append(icon, document.createTextNode(' Developer Hub'));
    const logsNav = navList.querySelector('[data-tab="logs"]');
    navList.insertBefore(nav, logsNav || null);

    const pane = document.createElement('div');
    pane.id = 'tab-devhub';
    pane.className = 'tab-pane';
    pane.innerHTML = paneMarkup();
    const logsPane = el('tab-logs');
    contentArea.insertBefore(pane, logsPane || null);

    nav.addEventListener('click', activateTab);
    wireForms();
    renderDevices();
    renderCandidates();
    renderPackages();
    state.initialized = true;
  }

  function init() {
    injectConsole();
    if (!state.initialized) {
      const observer = new MutationObserver(() => {
        injectConsole();
        if (state.initialized) observer.disconnect();
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
