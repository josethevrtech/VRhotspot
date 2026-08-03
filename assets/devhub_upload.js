(function addDeveloperHubApkPicker() {
  'use strict';

  const UPLOAD_PATH = '/v1/devbridge/adb/install-upload';
  const MAX_APK_BYTES = 8 * 1024 * 1024 * 1024;
  const el = (id) => document.getElementById(id);

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

(function loadPortalHotfixAssets() {
  'use strict';

  const assets = [
    '/assets/browser_session.js?v=139-session-hotfix',
    '/assets/pro_guided_workflow.js?v=141-pro-guided-recovery',
  ];
  for (const src of assets) {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    document.head.appendChild(script);
  }
})();

(function polishProSetupDensity() {
  'use strict';

  const RETRY_LIMIT = 200;
  const PROFILE_COPY = {
    btnApplyVrProfileUltra: {
      value: 'ultra_low_latency',
      description: 'Prioritizes the lowest possible response time for demanding VR streaming.',
    },
    btnApplyVrProfile: {
      value: 'balanced',
      description: 'Recommended default for a strong balance of responsiveness and stability.',
    },
    btnApplyVrProfileHigh: {
      value: 'high_throughput',
      description: 'Prioritizes sustained transfer speed for large or bandwidth-heavy workloads.',
    },
    btnApplyVrProfileStable: {
      value: 'vr',
      description: 'Favors connection consistency when the wireless environment is unpredictable.',
    },
  };

  let attempts = 0;
  let retryTimer = null;
  let observer = null;

  function el(id) {
    return document.getElementById(id);
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function serviceState() {
    const raw = String(el('proServiceStateText')?.textContent || el('pillTxt')?.textContent || 'Checking…').trim();
    const value = raw.toLowerCase();
    if (value.includes('error') || value.includes('failed') || value.includes('attention')) {
      return { name: 'error', label: 'Needs attention' };
    }
    if (value.includes('starting') || value.includes('stopping') || value.includes('working') || value.includes('repair')) {
      return { name: 'working', label: raw || 'Working…' };
    }
    if (value.includes('running') && !value.includes('not running')) {
      return { name: 'running', label: 'Running' };
    }
    if (value.includes('stopped') || value.includes('inactive') || value.includes('not running')) {
      return { name: 'stopped', label: 'Stopped' };
    }
    return { name: 'loading', label: raw || 'Checking…' };
  }

  function syncHeaderStatus() {
    const status = el('proHeaderStatus');
    if (!status) return;
    const state = serviceState();
    status.dataset.state = state.name;
    status.textContent = state.label;
  }

  function compactHeader() {
    const header = document.querySelector('#proGuidedWorkflow .pro-guided-header');
    const saveState = el('proSaveState');
    if (!header || !saveState) return false;
    if (header.dataset.proDensityReady === '1') {
      syncHeaderStatus();
      return true;
    }

    const heading = header.querySelector(':scope > h2');
    const summary = header.querySelector(':scope > p');
    if (!heading || !summary) return false;

    const copy = make('div', 'pro-guided-header-copy');
    copy.append(heading, summary);
    const meta = make('div', 'pro-guided-header-meta');
    const status = make('div', 'pro-header-status', 'Checking…');
    status.id = 'proHeaderStatus';
    status.setAttribute('aria-live', 'polite');
    meta.append(status, saveState);
    header.replaceChildren(copy, meta);
    header.dataset.proDensityReady = '1';

    const source = el('proServiceStateText') || el('pillTxt');
    if (source && source.dataset.proDensityObserved !== '1') {
      source.dataset.proDensityObserved = '1';
      const statusObserver = new MutationObserver(syncHeaderStatus);
      statusObserver.observe(source, { childList: true, subtree: true, characterData: true });
    }
    syncHeaderStatus();
    return true;
  }

  function compactAdapter() {
    const field = document.querySelector('#proStepAdapter [data-field="ap_adapter"]');
    const select = el('ap_adapter');
    const rescan = el('btnReloadAdapters');
    if (!field || !select || !rescan) return false;
    if (field.dataset.proDensityReady === '1') return true;

    field.classList.add('pro-adapter-field');
    const recommended = el('btnUseRecommended');
    if (recommended) {
      recommended.hidden = true;
      recommended.setAttribute('aria-hidden', 'true');
    }

    const row = make('div', 'pro-adapter-row');
    const badge = make('span', 'pro-adapter-badge', 'Recommended');
    badge.id = 'proAdapterRecommendedBadge';
    const syncBadge = () => {
      const preferred = String(select.dataset.recommended || '');
      badge.hidden = !preferred || select.value !== preferred;
    };

    rescan.textContent = 'Rescan adapters';
    rescan.classList.add('pro-adapter-rescan');
    row.append(select, badge, rescan);

    const hint = el('adapterHint');
    const label = field.querySelector(':scope > label, :scope > .field-label-with-tip');
    field.replaceChildren();
    if (label) field.appendChild(label);
    field.appendChild(row);
    if (hint) field.appendChild(hint);

    select.addEventListener('change', syncBadge);
    const adapterObserver = new MutationObserver(syncBadge);
    adapterObserver.observe(select, { childList: true, subtree: true, attributes: true });
    field.dataset.proDensityReady = '1';
    syncBadge();
    return true;
  }

  function compactPerformance() {
    const preset = document.querySelector('#proStepPerformance .pro-performance-picker');
    const qosField = document.querySelector('[data-field="qos_preset"]');
    const qos = el('qos_preset');
    if (!preset || !qosField || !qos) return false;

    qosField.hidden = true;
    qosField.setAttribute('aria-hidden', 'true');
    qosField.classList.add('pro-guided-hidden');
    const hiddenControls = el('proGuidedHiddenControls');
    if (hiddenControls && qosField.parentElement !== hiddenControls) hiddenControls.appendChild(qosField);

    let description = el('proPerformanceDescription');
    if (!description) {
      description = make('p', 'pro-performance-description');
      description.id = 'proPerformanceDescription';
      preset.appendChild(description);
    }

    const buttons = Object.entries(PROFILE_COPY)
      .map(([id, profile]) => [el(id), profile])
      .filter(([button]) => !!button);

    const syncSelection = () => {
      const selected = String(qos.value || 'off');
      let selectedCopy = 'Choose the performance behavior that best matches this hotspot.';
      for (const [button, profile] of buttons) {
        const active = selected === profile.value;
        button.classList.toggle('is-selected', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
        if (active) selectedCopy = profile.description;
      }
      description.textContent = selectedCopy;
    };

    if (preset.dataset.proDensityReady !== '1') {
      preset.dataset.proDensityReady = '1';
      qos.addEventListener('change', syncSelection);
      for (const [button] of buttons) {
        button.addEventListener('click', () => window.setTimeout(syncSelection, 0));
      }
    }
    syncSelection();
    return true;
  }

  function compactPassword() {
    const field = document.querySelector('#proStepHotspot [data-field="wpa2_passphrase"]');
    const input = el('wpa2_passphrase');
    const reveal = el('btnRevealPass');
    const qr = el('btnShowQr');
    if (!field || !input || !reveal || !qr) return false;
    if (field.dataset.proDensityReady === '1') return true;

    const label = field.querySelector('label');
    const hint = el('passHint');
    if (label) label.textContent = 'Password';

    reveal.type = 'button';
    reveal.classList.add('icon-only');
    reveal.title = 'Show or hide password';
    reveal.setAttribute('aria-label', 'Show or hide password');

    qr.type = 'button';
    qr.textContent = 'QR';
    qr.className = 'btn icon-only';
    qr.title = 'Show QR code';
    qr.setAttribute('aria-label', 'Show QR code');

    const row = make('div', 'pro-password-row');
    row.append(input, reveal, qr);
    field.replaceChildren();
    if (label) field.appendChild(label);
    field.appendChild(row);
    if (hint) {
      hint.classList.add('pro-password-hint');
      field.appendChild(hint);
    }
    field.classList.add('pro-password-field');
    field.dataset.proDensityReady = '1';
    return true;
  }

  function compactHotspotStep() {
    const slot = el('proStepHotspot');
    if (!slot) return false;
    const content = slot.closest('.pro-guided-content');
    const title = content?.querySelector('.pro-guided-title');
    const help = content?.querySelector('.pro-guided-help');
    if (title) title.textContent = 'Hotspot name and password';
    if (help) help.textContent = 'Choose the network name, password, and whether connected devices can use this computer’s internet connection.';

    const ssid = slot.querySelector('[data-field="ssid"]');
    if (ssid) ssid.classList.add('pro-hotspot-name-field');
    const internet = slot.querySelector('[data-field="enable_internet"] .tog');
    if (internet) {
      const checkbox = internet.querySelector('input');
      if (checkbox) internet.replaceChildren(checkbox, document.createTextNode(' Share internet with connected devices'));
    }
    return compactPassword();
  }

  function removeLegacyEssentials() {
    document.querySelectorAll('.pro-config-essentials').forEach((node) => node.remove());
    document.querySelectorAll('[data-field="qos_preset"]').forEach((node) => {
      node.hidden = true;
      node.setAttribute('aria-hidden', 'true');
    });
  }

  function applyDensityPass() {
    const workflow = el('proGuidedWorkflow');
    if (!workflow) return false;
    removeLegacyEssentials();
    const ready = [
      compactHeader(),
      compactAdapter(),
      compactPerformance(),
      compactHotspotStep(),
    ].every(Boolean);
    if (ready) workflow.dataset.proDensityReady = '1';
    return ready;
  }

  function retry() {
    if (applyDensityPass()) {
      if (observer) observer.disconnect();
      if (retryTimer) window.clearTimeout(retryTimer);
      return;
    }
    attempts += 1;
    if (attempts >= RETRY_LIMIT) return;
    retryTimer = window.setTimeout(retry, 75);
  }

  function start() {
    if (applyDensityPass()) return;
    observer = new MutationObserver(() => {
      if (applyDensityPass() && observer) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    retry();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();