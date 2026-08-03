(function addDeveloperHubApkPicker() {
  'use strict';

  const UPLOAD_PATH = '/v1/devbridge/adb/install-upload';
  const MAX_APK_BYTES = 8 * 1024 * 1024 * 1024;

  function el(id) { return document.getElementById(id); }

  function feedback(message, state) {
    const node = el('devhubFeedback');
    if (!node) return;
    node.textContent = message;
    node.dataset.state = state || 'idle';
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let amount = bytes;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount.toFixed(amount >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function responseFailure(response) {
    const envelope = response && response.json ? response.json : {};
    const result = envelope && envelope.data && typeof envelope.data === 'object'
      ? envelope.data
      : {};
    const detail = String(result.stderr || '').trim();
    const code = String(result.result_code || envelope.result_code || `HTTP ${response.status}`);
    return detail ? `${code}: ${detail}` : code;
  }

  function deploymentData(response) {
    const publicResult = response && response.json && response.json.data;
    return publicResult && publicResult.data && typeof publicResult.data === 'object'
      ? publicResult.data
      : {};
  }

  function deploymentVerb(action) {
    if (action === 'installed') return 'Installed';
    if (action === 'updated') return 'Updated';
    return 'Installed or updated';
  }

  function selectedHeadsetLabel() {
    const name = el('devhubTargetDeviceName');
    const value = String((name && name.textContent) || '').trim();
    return value && value !== 'No headset selected' ? value : 'selected headset';
  }

  function companionBridgeActive() {
    return typeof companionAuthBridgeAvailable === 'function'
      && companionAuthBridgeAvailable();
  }

  function inject() {
    if (el('devhubApkPicker')) return true;
    const form = el('devhubInstallForm');
    const pathInput = el('devhubApkPath');
    const serialInput = el('devhubPackageSerial');
    if (!form || !pathInput || !serialInput) return false;
    const pathField = pathInput.closest('div');
    const checks = form.querySelector('.devhub-checks');
    const submit = form.querySelector('button[type="submit"]');
    if (!pathField || !checks || !submit) return false;

    pathInput.required = false;
    pathInput.placeholder = '/var/lib/builds/application.apk';

    const uploadField = document.createElement('div');
    uploadField.id = 'devhubApkPicker';
    uploadField.className = 'devhub-wide devhub-upload-field';
    const label = document.createElement('label');
    label.textContent = 'APK file';
    const fileInput = document.createElement('input');
    fileInput.id = 'devhubApkFile';
    fileInput.type = 'file';
    fileInput.accept = '.apk,application/vnd.android.package-archive';
    fileInput.hidden = true;
    const drop = document.createElement('div');
    drop.className = 'devhub-file-drop';
    drop.tabIndex = 0;
    drop.setAttribute('role', 'button');
    drop.setAttribute('aria-controls', fileInput.id);
    drop.setAttribute('aria-label', 'Choose an Android APK file');
    const choose = document.createElement('button');
    choose.type = 'button';
    choose.className = 'btn primary';
    choose.textContent = 'Choose APK';
    const copy = document.createElement('div');
    copy.className = 'devhub-file-copy';
    const name = document.createElement('div');
    name.className = 'devhub-file-name';
    name.textContent = 'No APK selected';
    const meta = document.createElement('div');
    meta.className = 'devhub-file-meta';
    meta.textContent = 'Choose a file or drop an APK here.';
    copy.append(name, meta);
    drop.append(choose, copy);
    uploadField.append(label, fileInput, drop);

    const advanced = document.createElement('details');
    advanced.className = 'devhub-wide devhub-host-path';
    const summary = document.createElement('summary');
    summary.textContent = 'Advanced: install from a path on the daemon host';
    const help = document.createElement('div');
    help.className = 'devhub-host-path-help';
    help.textContent = 'Use this only when the APK already exists on the Linux machine running VRhotspot.';

    form.insertBefore(uploadField, pathField);
    advanced.append(summary, pathField, help);
    form.insertBefore(advanced, checks);
    submit.textContent = 'Install or update app';

    function selectedFile() {
      return fileInput.files && fileInput.files.length ? fileInput.files[0] : null;
    }

    function updateSelection() {
      const file = selectedFile();
      name.textContent = file ? file.name : 'No APK selected';
      meta.textContent = file
        ? `${formatBytes(file.size)} · Ready to install or update`
        : 'Choose a file or drop an APK here.';
    }

    function useFile(file) {
      if (!file) return;
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
      updateSelection();
    }

    choose.addEventListener('click', () => fileInput.click());
    drop.addEventListener('click', (event) => {
      if (event.target !== choose) fileInput.click();
    });
    drop.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        fileInput.click();
      }
    });
    fileInput.addEventListener('change', updateSelection);
    for (const eventName of ['dragenter', 'dragover']) {
      drop.addEventListener(eventName, (event) => {
        event.preventDefault();
        drop.classList.add('is-dragging');
      });
    }
    for (const eventName of ['dragleave', 'drop']) {
      drop.addEventListener(eventName, (event) => {
        event.preventDefault();
        drop.classList.remove('is-dragging');
      });
    }
    drop.addEventListener('drop', (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) useFile(files[0]);
    });

    form.addEventListener('submit', async (event) => {
      const file = selectedFile();
      const hostPath = pathInput.value.trim();
      if (!file) {
        if (!hostPath) {
          event.preventDefault();
          event.stopImmediatePropagation();
          feedback('Choose an APK file to install.', 'error');
        }
        return;
      }

      event.preventDefault();
      event.stopImmediatePropagation();
      const serial = serialInput.value.trim();
      if (!serial) {
        feedback('Select a connected headset before installing an APK.', 'error');
        return;
      }
      if (!file.name.toLowerCase().endsWith('.apk')) {
        feedback('Choose a file with the .apk extension.', 'error');
        return;
      }
      if (file.size <= 0 || file.size > MAX_APK_BYTES) {
        feedback('The selected APK is empty or exceeds the supported size limit.', 'error');
        return;
      }
      if (companionBridgeActive()) {
        feedback(
          'Direct APK selection is available in the browser Web Portal. '
          + 'Use the advanced daemon path in the desktop companion for now.',
          'error',
        );
        return;
      }

      const grant = el('devhubGrantPermissions');
      const previousLabel = submit.textContent;
      const headset = selectedHeadsetLabel();
      submit.disabled = true;
      choose.disabled = true;
      fileInput.disabled = true;
      submit.textContent = 'Installing...';
      feedback(
        `Uploading ${file.name} (${formatBytes(file.size)}) and installing it on ${headset}...`,
        'loading',
      );
      try {
        const response = await api(UPLOAD_PATH, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/vnd.android.package-archive',
            'X-VRhotspot-Serial': serial,
            'X-VRhotspot-Apk-Name': encodeURIComponent(file.name),
            'X-VRhotspot-Reinstall': '1',
            'X-VRhotspot-Grant-Permissions': grant && grant.checked ? '1' : '0',
          },
          body: file,
        });
        if (!response.ok) {
          feedback(`APK install failed: ${responseFailure(response)}`, 'error');
          return;
        }
        const details = deploymentData(response);
        feedback(
          `${deploymentVerb(details.deployment_action)} ${file.name} on ${headset}.`,
          'success',
        );
        fileInput.value = '';
        updateSelection();
        el('devhubLoadPackages')?.click();
      } catch {
        feedback('APK upload could not reach the VRhotspot daemon.', 'error');
      } finally {
        submit.disabled = false;
        choose.disabled = false;
        fileInput.disabled = false;
        submit.textContent = previousLabel;
      }
    }, true);
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

(function buildProGuidedWorkflow() {
  'use strict';

  const AUTOSAVE_DELAY_MS = 650;
  const SAVE_TIMEOUT_MS = 12000;
  let initialized = false;
  let saveTimer = null;
  let restartRequired = false;

  function el(id) { return document.getElementById(id); }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  function icon(kind) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.classList.add('pro-nav-svg');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', kind === 'wifi'
      ? 'M12 18.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm0-5a6.45 6.45 0 0 0-4.58 1.9l1.42 1.42a4.48 4.48 0 0 1 6.32 0l1.42-1.42A6.45 6.45 0 0 0 12 13.5Zm0-5a11.42 11.42 0 0 0-8.08 3.35l1.42 1.42a9.42 9.42 0 0 1 13.32 0l1.42-1.42A11.42 11.42 0 0 0 12 8.5Zm0-5A16.4 16.4 0 0 0 .42 8.3l1.42 1.42a14.4 14.4 0 0 1 20.32 0l1.42-1.42A16.4 16.4 0 0 0 12 3.5Z'
      : 'M11 2h2v3h-2V2Zm0 17h2v3h-2v-3ZM2 11h3v2H2v-2Zm17 0h3v2h-3v-2ZM4.22 5.64l1.42-1.42 2.12 2.12-1.42 1.42-2.12-2.12Zm12.02 12.02 1.42-1.42 2.12 2.12-1.42 1.42-2.12-2.12ZM4.22 18.36l2.12-2.12 1.42 1.42-2.12 2.12-1.42-1.42ZM16.24 6.34l2.12-2.12 1.42 1.42-2.12 2.12-1.42-1.42ZM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z');
    svg.appendChild(path);
    return svg;
  }

  function replaceNav(item, kind, label) {
    if (!item) return;
    item.replaceChildren(icon(kind), document.createTextNode(label));
  }

  function addStyles() {
    if (el('proGuidedWorkflowStyles')) return;
    const style = make('style');
    style.id = 'proGuidedWorkflowStyles';
    style.textContent = `
      body[data-ui-mode="advanced"] .pro-nav-svg {
        width: 20px; height: 20px; flex: 0 0 20px; margin-right: 12px;
        color: var(--accent-primary);
      }
      body[data-ui-mode="advanced"] .pro-guided-shell {
        width: min(1540px, 100%); margin: 0 auto; display: grid; gap: 22px;
      }
      body[data-ui-mode="advanced"] .pro-guided-card {
        overflow: hidden; border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg); background: var(--bg-panel); box-shadow: var(--shadow-sm);
      }
      body[data-ui-mode="advanced"] .pro-guided-header {
        padding: 22px 26px; border-bottom: 1px solid var(--border-subtle); background: var(--bg-subtle);
      }
      body[data-ui-mode="advanced"] .pro-guided-header h2 { margin: 0; font-size: 20px; }
      body[data-ui-mode="advanced"] .pro-guided-header p {
        margin: 6px 0 0; color: var(--text-muted); font-size: 14px;
      }
      body[data-ui-mode="advanced"] .pro-guided-steps { padding: 26px; }
      body[data-ui-mode="advanced"] .pro-guided-step {
        display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 8px;
      }
      body[data-ui-mode="advanced"] .pro-guided-number {
        display: grid; place-items: center; width: 32px; height: 32px;
        border: 1px solid var(--border-active); border-radius: 50%;
        color: var(--accent-primary); background: var(--bg-subtle); font-weight: 700;
      }
      body[data-ui-mode="advanced"] .pro-guided-content {
        min-width: 0; padding: 0 0 26px 8px; margin-bottom: 24px;
        border-bottom: 1px solid var(--border-subtle);
      }
      body[data-ui-mode="advanced"] .pro-guided-step:last-child .pro-guided-content {
        padding-bottom: 0; margin-bottom: 0; border-bottom: 0;
      }
      body[data-ui-mode="advanced"] .pro-guided-title { margin: 2px 0 6px; font-size: 17px; }
      body[data-ui-mode="advanced"] .pro-guided-help {
        margin: 0 0 16px; color: var(--text-muted); font-size: 13px; line-height: 1.5;
      }
      body[data-ui-mode="advanced"] .pro-guided-slot { display: grid; gap: 16px; min-width: 0; }
      body[data-ui-mode="advanced"] .pro-guided-slot .form-group,
      body[data-ui-mode="advanced"] .pro-guided-slot .preset-bar { margin: 0; }
      body[data-ui-mode="advanced"] .pro-performance-picker .label { display: none; }
      body[data-ui-mode="advanced"] .pro-performance-picker .btn-group {
        display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; width: 100%;
      }
      body[data-ui-mode="advanced"] .pro-performance-picker .btn {
        min-height: 58px; width: 100%; justify-content: center; white-space: normal;
      }
      body[data-ui-mode="advanced"] .pro-hotspot-fields {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px;
      }
      body[data-ui-mode="advanced"] .pro-hotspot-fields [data-field="enable_internet"] { grid-column: 1 / -1; }
      body[data-ui-mode="advanced"] .pro-advanced-settings {
        border: 1px solid var(--border-subtle); border-radius: var(--radius-md); overflow: hidden;
      }
      body[data-ui-mode="advanced"] .pro-advanced-settings > summary {
        padding: 18px 20px; cursor: pointer; background: var(--bg-subtle);
        color: var(--text-main); font-weight: 700; list-style: none;
      }
      body[data-ui-mode="advanced"] .pro-advanced-settings > summary::after {
        content: '+'; float: right; color: var(--accent-primary);
      }
      body[data-ui-mode="advanced"] .pro-advanced-settings[open] > summary::after { content: '−'; }
      body[data-ui-mode="advanced"] .pro-advanced-body { display: grid; gap: 14px; padding: 18px; }
      body[data-ui-mode="advanced"] .pro-advanced-body .pro-config-details { margin: 0; }
      body[data-ui-mode="advanced"] .pro-guided-action {
        display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
        gap: 24px; align-items: center; padding: 22px;
        border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: var(--bg-subtle);
      }
      body[data-ui-mode="advanced"] .pro-guided-action .pro-service-state-copy h3 { font-size: 24px; }
      body[data-ui-mode="advanced"] .pro-guided-action #btnStart {
        width: 100%; min-height: 56px; justify-content: center;
      }
      body[data-ui-mode="advanced"] .pro-guided-hidden,
      body[data-ui-mode="advanced"] .pro-configuration > .settings-header,
      body[data-ui-mode="advanced"] .pro-service-secondary { display: none !important; }
      body[data-ui-mode="advanced"] .pro-save-state {
        min-height: 20px; color: var(--text-muted); font-size: 13px;
      }
      body[data-ui-mode="advanced"] .pro-save-state[data-state="saving"] { color: var(--accent-primary); }
      body[data-ui-mode="advanced"] .pro-save-state[data-state="restart"] { color: var(--warning); }
      body[data-ui-mode="advanced"] .pro-quality-card {
        overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-lg);
        background: var(--bg-panel);
      }
      body[data-ui-mode="advanced"] .pro-quality-header {
        display: flex; align-items: center; justify-content: space-between; gap: 16px;
        padding: 18px 22px; border-bottom: 1px solid var(--border-subtle); background: var(--bg-subtle);
      }
      body[data-ui-mode="advanced"] .pro-quality-header h2 { margin: 0; font-size: 18px; }
      body[data-ui-mode="advanced"] .pro-quality-body { padding: 20px 22px; }
      body[data-ui-mode="advanced"] .pro-quality-summary { color: var(--text-main); font-size: 16px; }
      body[data-ui-mode="advanced"] .pro-quality-details { margin-top: 16px; }
      body[data-ui-mode="advanced"] .pro-quality-details > summary {
        cursor: pointer; color: var(--accent-primary); font-weight: 700;
      }
      body[data-ui-mode="advanced"] .pro-quality-details .chart-wrapper { min-height: 220px; }
      body[data-ui-mode="advanced"] .pro-quality-settings { margin-top: 18px; }
      body[data-ui-mode="advanced"] #tab-telemetry { display: none !important; }
      body[data-ui-mode="advanced"] .troubleshooting-shell {
        width: min(1540px, 100%); margin: 0 auto; display: grid; gap: 20px;
      }
      body[data-ui-mode="advanced"] .troubleshooting-header {
        display: flex; justify-content: space-between; gap: 18px; align-items: center;
        padding: 22px 24px; border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg); background: var(--bg-panel);
      }
      body[data-ui-mode="advanced"] .troubleshooting-header h2 { margin: 0; font-size: 24px; }
      body[data-ui-mode="advanced"] .troubleshooting-header p { margin: 6px 0 0; color: var(--text-muted); }
      body[data-ui-mode="advanced"] .troubleshooting-actions { display: flex; gap: 10px; flex-wrap: wrap; }
      body[data-ui-mode="advanced"] .troubleshooting-section-label {
        margin: 0; padding: 16px 20px; border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md); background: var(--bg-subtle); font-size: 17px;
      }
      body[data-ui-mode="advanced"] #tab-logs { display: none !important; }
      body[data-ui-mode="advanced"] .troubleshooting-shell .preflight-page,
      body[data-ui-mode="advanced"] .troubleshooting-shell .pro-runtime-details,
      body[data-ui-mode="advanced"] .troubleshooting-shell .pro-support-bundle-card,
      body[data-ui-mode="advanced"] .troubleshooting-shell .card { margin: 0; }
      @media (max-width: 900px) {
        body[data-ui-mode="advanced"] .pro-performance-picker .btn-group,
        body[data-ui-mode="advanced"] .pro-hotspot-fields,
        body[data-ui-mode="advanced"] .pro-guided-action { grid-template-columns: minmax(0, 1fr); }
        body[data-ui-mode="advanced"] .troubleshooting-header { align-items: flex-start; flex-direction: column; }
      }
    `;
    document.head.appendChild(style);
  }

  function step(number, title, help, id) {
    const section = make('section', 'pro-guided-step');
    const badge = make('span', 'pro-guided-number', String(number));
    const content = make('div', 'pro-guided-content');
    content.append(make('h3', 'pro-guided-title', title), make('p', 'pro-guided-help', help));
    const slot = make('div', 'pro-guided-slot');
    slot.id = id;
    content.appendChild(slot);
    section.append(badge, content);
    return section;
  }

  function serviceIsRunning() {
    const text = String(el('pillTxt')?.textContent || '').toLowerCase();
    return text.includes('running') && !text.includes('not running');
  }

  function savingState(state, text) {
    const node = el('proSaveState');
    if (!node) return;
    node.dataset.state = state;
    setText(node, text);
  }

  function waitUntilSaved() {
    const started = Date.now();
    return new Promise((resolve) => {
      function check() {
        const dirty = String(el('dirty')?.textContent || '').trim();
        if (!dirty) return resolve(true);
        if (Date.now() - started >= SAVE_TIMEOUT_MS) return resolve(false);
        window.setTimeout(check, 120);
      }
      check();
    });
  }

  async function saveConfiguration() {
    const save = el('btnSaveConfig');
    if (!save) return false;
    savingState('saving', 'Saving changes…');
    save.click();
    const saved = await waitUntilSaved();
    if (!saved) {
      savingState('error', 'Changes could not be saved. Open Troubleshooting for details.');
      return false;
    }
    savingState(restartRequired ? 'restart' : 'saved', restartRequired
      ? 'Changes saved. Restart the hotspot to apply them.'
      : 'All changes saved.');
    syncPrimaryAction();
    return true;
  }

  function scheduleSave(markRestart = true) {
    if (markRestart && serviceIsRunning()) restartRequired = true;
    savingState('saving', 'Saving changes…');
    if (saveTimer) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => void saveConfiguration(), AUTOSAVE_DELAY_MS);
  }

  function syncPrimaryAction() {
    const primary = el('btnStart');
    if (!primary) return;
    if (serviceIsRunning() && restartRequired) {
      primary.dataset.proServiceAction = 'start';
      primary.dataset.proGuidedAction = 'apply';
      primary.textContent = 'Apply Changes & Restart';
      primary.classList.remove('danger', 'secondary');
      primary.classList.add('primary');
      primary.disabled = false;
      return;
    }
    delete primary.dataset.proGuidedAction;
  }

  function wireAutosave(root) {
    root.addEventListener('change', (event) => {
      if (!(event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement)) return;
      if (!event.isTrusted) return;
      scheduleSave(true);
      updateDependencies();
    });
    root.addEventListener('input', (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      if (!event.isTrusted) return;
      scheduleSave(true);
    });
    root.querySelectorAll('.preset-bar .btn').forEach((button) => {
      button.addEventListener('click', () => window.setTimeout(() => scheduleSave(true), 0));
    });
  }

  function updateDependencies() {
    const autoChannel = el('channel_auto_select');
    const channel5 = el('channel_5g');
    const channel6 = el('channel_6g');
    if (channel5) channel5.disabled = !!autoChannel?.checked;
    if (channel6) channel6.disabled = !!autoChannel?.checked;
    const bridge = el('bridge_mode');
    for (const id of ['bridge_name', 'bridge_uplink']) {
      const input = el(id);
      if (input) input.disabled = !bridge?.checked;
    }
  }

  function buildGuidedSetup() {
    const overview = el('tab-overview');
    const oldShell = overview?.querySelector('.pro-setup-shell');
    const configuration = el('proHotspotConfiguration');
    const serviceCard = overview?.querySelector('.pro-service-card');
    if (!overview || !oldShell || !configuration || !serviceCard) return false;

    const shell = make('div', 'pro-guided-shell');
    const card = make('section', 'pro-guided-card');
    const header = make('div', 'pro-guided-header');
    header.append(
      make('h2', '', 'Set Up Hotspot'),
      make('p', '', 'Configure the hotspot in order, then start it or apply changes safely.'),
    );
    const steps = make('div', 'pro-guided-steps');
    steps.append(
      step(1, 'Choose Wi-Fi adapter', 'Select the adapter VRhotspot should use. Recommended choices are labeled automatically.', 'proStepAdapter'),
      step(2, 'Choose performance mode', 'Choose the behavior that best matches your VR workflow.', 'proStepPerformance'),
      step(3, 'Configure hotspot', 'Set the network name, password, and internet-sharing preference.', 'proStepHotspot'),
      step(4, 'Fine-tune hotspot', 'Optional expert settings are grouped by purpose and can be left at their recommended defaults.', 'proStepAdvanced'),
      step(5, 'Start hotspot', 'Start, stop, or safely apply saved changes to a running hotspot.', 'proStepAction'),
    );
    card.append(header, steps);
    shell.appendChild(card);

    const adapter = document.querySelector('[data-field="ap_adapter"]');
    if (adapter) {
      const label = adapter.querySelector('label');
      if (label) label.textContent = 'Wi-Fi adapter';
      el('proStepAdapter').appendChild(adapter);
    }

    const preset = configuration.querySelector('.preset-bar');
    if (preset) {
      preset.classList.add('pro-performance-picker');
      const order = [
        el('btnApplyVrProfileUltra'),
        el('btnApplyVrProfile'),
        el('btnApplyVrProfileHigh'),
        el('btnApplyVrProfileStable'),
      ].filter(Boolean);
      const group = preset.querySelector('.btn-group');
      if (group) order.forEach((button) => group.appendChild(button));
      el('proStepPerformance').appendChild(preset);
    }
    const qos = document.querySelector('[data-field="qos_preset"]');
    if (qos) {
      qos.classList.add('pro-guided-hidden');
      el('proStepPerformance').appendChild(qos);
    }

    const friendlyLabels = {
      ssid: 'Hotspot name',
      wpa2_passphrase: 'Password',
    };
    const hotspotFields = make('div', 'pro-hotspot-fields');
    for (const key of ['ssid', 'wpa2_passphrase', 'enable_internet']) {
      const field = document.querySelector(`[data-field="${key}"]`);
      if (field && friendlyLabels[key]) {
        const label = field.querySelector('label');
        if (label) label.textContent = friendlyLabels[key];
      }
      if (field) hotspotFields.appendChild(field);
    }
    el('proStepHotspot').appendChild(hotspotFields);

    const advanced = make('details', 'pro-advanced-settings');
    const advancedSummary = make('summary', '', 'Advanced wireless, network, and system settings');
    const advancedBody = make('div', 'pro-advanced-body');
    configuration.querySelectorAll('.pro-config-details').forEach((details) => advancedBody.appendChild(details));
    advanced.append(advancedSummary, advancedBody);
    el('proStepAdvanced').appendChild(advanced);

    const action = make('div', 'pro-guided-action');
    const stateCopy = serviceCard.querySelector('.pro-service-state-copy');
    const primary = el('btnStart');
    if (stateCopy) action.appendChild(stateCopy);
    if (primary) action.appendChild(primary);
    el('proStepAction').append(action, make('div', 'pro-save-state', 'All changes saved.'));
    el('proStepAction').lastElementChild.id = 'proSaveState';

    const hidden = make('div', 'pro-guided-hidden');
    hidden.id = 'proGuidedHiddenControls';
    const headerBar = configuration.querySelector('.settings-header');
    const serviceSecondary = serviceCard.querySelector('.pro-service-secondary');
    const serviceMeta = serviceCard.querySelector('.hero-meta');
    const feedback = serviceCard.querySelectorAll('.hero-feedback');
    for (const node of [headerBar, serviceSecondary, serviceMeta, ...feedback]) {
      if (node) hidden.appendChild(node);
    }
    card.appendChild(hidden);

    overview.replaceChildren(shell);
    oldShell.remove();
    serviceCard.remove();
    configuration.remove();
    wireAutosave(card);
    updateDependencies();

    if (primary) {
      primary.addEventListener('click', async (event) => {
        if (primary.dataset.proGuidedAction !== 'apply') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const saved = await saveConfiguration();
        if (!saved) return;
        restartRequired = false;
        savingState('saving', 'Applying changes and restarting…');
        el('btnSaveRestart')?.click();
      }, true);
    }

    const statusSource = el('pillTxt');
    if (statusSource) {
      const observer = new MutationObserver(() => {
        if (!serviceIsRunning()) restartRequired = false;
        syncPrimaryAction();
      });
      observer.observe(statusSource, { childList: true, subtree: true, characterData: true });
    }
    return true;
  }

  function qualityMessage() {
    if (!serviceIsRunning()) return 'Start the hotspot to measure connection performance.';
    const summary = String(el('telemetrySummary')?.textContent || '').trim();
    return summary || 'Connection measurements will appear as clients begin using the hotspot.';
  }

  function buildConnectionQuality() {
    const overview = el('tab-overview');
    const shell = overview?.querySelector('.pro-guided-shell');
    const telemetryPane = el('tab-telemetry');
    const telemetryCard = el('cardTelemetry');
    if (!shell || !telemetryPane || !telemetryCard) return false;

    document.querySelector('.nav-item[data-tab="telemetry"]')?.remove();
    const card = make('section', 'pro-quality-card');
    const header = make('div', 'pro-quality-header');
    header.append(make('h2', '', 'Connection Quality'));
    const status = make('span', 'pill', serviceIsRunning() ? 'Measuring' : 'Waiting');
    status.id = 'proQualityStatus';
    header.appendChild(status);
    const body = make('div', 'pro-quality-body');
    const summary = make('div', 'pro-quality-summary', qualityMessage());
    summary.id = 'proQualitySummary';
    const warning = el('telemetryWarnings');
    const details = make('details', 'pro-quality-details');
    details.appendChild(make('summary', '', 'View detailed charts and client measurements'));

    const telemetryHeader = telemetryCard.querySelector('.card-header');
    const telemetryBody = telemetryCard.querySelector('.card-body');
    if (telemetryHeader) telemetryHeader.remove();
    if (telemetryBody) details.appendChild(telemetryBody);
    const settings = details.querySelector('[data-field="telemetry_enable"]');
    if (settings) {
      settings.classList.add('pro-quality-settings');
      const label = settings.querySelector(':scope > label');
      if (label) label.textContent = 'Measurement options';
      const toggles = settings.querySelectorAll('.tog');
      if (toggles[0]) toggles[0].lastChild.textContent = ' Measure connection quality';
      if (toggles[1]) toggles[1].lastChild.textContent = ' Watch for connection problems';
    }
    const interval = document.querySelector('[data-field="telemetry_interval_s"]');
    if (interval) interval.classList.add('pro-guided-hidden');
    body.append(summary);
    if (warning) body.appendChild(warning);
    body.appendChild(details);
    card.append(header, body);
    shell.appendChild(card);
    telemetryPane.hidden = true;

    const sources = [el('telemetrySummary'), el('telemetryWarnings'), el('pillTxt')].filter(Boolean);
    const observer = new MutationObserver(() => {
      setText(summary, qualityMessage());
      setText(status, serviceIsRunning() ? 'Live' : 'Waiting');
    });
    sources.forEach((node) => observer.observe(node, { childList: true, subtree: true, characterData: true }));
    return true;
  }

  function buildTroubleshooting() {
    const diagnosticsPane = el('tab-diagnostics');
    const logsPane = el('tab-logs');
    const nav = document.querySelector('.nav-item[data-tab="diagnostics"]');
    const logsNav = document.querySelector('.nav-item[data-tab="logs"]');
    if (!diagnosticsPane || !logsPane || !nav) return false;

    nav.dataset.tab = 'troubleshooting';
    diagnosticsPane.id = 'tab-troubleshooting';
    replaceNav(nav, 'trouble', 'Troubleshooting');
    logsNav?.remove();

    const shell = make('div', 'troubleshooting-shell');
    const header = make('section', 'troubleshooting-header');
    const copy = make('div');
    copy.append(
      make('h2', '', 'Troubleshooting'),
      make('p', '', 'Check system health, repair problems, inspect runtime details, and collect support information.'),
    );
    const actions = make('div', 'troubleshooting-actions');
    const repair = el('btnRepair');
    const restart = el('btnRestart');
    const refresh = el('btnRefreshPreflight');
    if (repair) actions.appendChild(repair);
    if (restart) actions.appendChild(restart);
    if (refresh) actions.appendChild(refresh);
    header.append(copy, actions);
    shell.append(header, make('h3', 'troubleshooting-section-label', 'System Health & Diagnostic Checks'));

    const preflight = diagnosticsPane.querySelector('.preflight-page');
    if (preflight) {
      preflight.querySelector('.preflight-header .action-group')?.remove();
      shell.appendChild(preflight);
    }

    shell.appendChild(make('h3', 'troubleshooting-section-label', 'Runtime Details, Logs & Support'));
    const supportHeading = logsPane.querySelector('.pro-support-heading');
    if (supportHeading) supportHeading.remove();
    Array.from(logsPane.children).forEach((node) => shell.appendChild(node));
    diagnosticsPane.replaceChildren(shell);
    logsPane.hidden = true;
    return true;
  }

  function initialize() {
    if (initialized) return true;
    const overviewNav = document.querySelector('.nav-item[data-tab="overview"]');
    const shell = el('tab-overview')?.querySelector('.pro-setup-shell');
    if (!overviewNav || !shell || !el('tab-telemetry') || !el('tab-diagnostics') || !el('tab-logs')) {
      return false;
    }
    addStyles();
    replaceNav(overviewNav, 'wifi', 'Set Up Hotspot');
    if (!buildGuidedSetup()) return false;
    buildConnectionQuality();
    buildTroubleshooting();
    initialized = true;
    return true;
  }

  function start() {
    if (initialize()) return;
    const observer = new MutationObserver(() => {
      if (initialize()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
