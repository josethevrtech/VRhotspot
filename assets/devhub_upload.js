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

(function loadPortalExtensionAssets() {
  'use strict';

  const assets = [
    '/assets/browser_session.js?v=139-session-hotfix',
    '/assets/pro_guided_workflow.js?v=148-adapter-controls-3',
  ];
  for (const src of assets) {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    document.head.appendChild(script);
  }
})();

(function stabilizeProAdapterControl() {
  'use strict';

  let observedSelect = null;
  let optionsObserver = null;
  let normalizing = false;
  let reconcileQueued = false;

  function isProMode() {
    return document.body?.dataset.uiMode === 'advanced';
  }

  function adapterRecord(ifname) {
    try {
      if (typeof getAdapterByIfname === 'function') return getAdapterByIfname(ifname);
    } catch {
      // The Pro adapter surface can initialize before the inventory helper is ready.
    }
    return null;
  }

  function adapterKind(adapter, rawLabel) {
    const bus = String(adapter?.bus || '').trim().toLowerCase();
    const identity = `${bus} ${adapter?.name || ''} ${rawLabel || ''}`.toLowerCase();
    if (bus === 'usb' || identity.includes('usb')) return 'usb';
    if (['pci', 'pcie', 'platform', 'sdio', 'internal'].includes(bus)) return 'internal';
    return adapter ? 'internal' : 'other';
  }

  function ensureSelectedLabel(row) {
    let label = row.querySelector(':scope > .pro-adapter-selected-label');
    if (!label) {
      label = document.createElement('span');
      label.className = 'pro-adapter-selected-label pro-runtime-wrapper';
      label.setAttribute('aria-hidden', 'true');
      row.prepend(label);
    }
    return label;
  }

  function normalizeOptions(select) {
    if (!isProMode()) return '';
    const row = select.closest('.pro-adapter-row');
    if (!row) return '';

    const counters = { usb: 0, internal: 0, other: 0 };
    const recommended = String(select.dataset.recommended || '');
    let selectedLabel = '';
    normalizing = true;
    try {
      for (const option of Array.from(select.options)) {
        const rawLabel = option.dataset.rawAdapterLabel || String(option.textContent || '').trim();
        if (!option.dataset.rawAdapterLabel) option.dataset.rawAdapterLabel = rawLabel;
        const adapter = adapterRecord(option.value);
        const kind = adapterKind(adapter, rawLabel);
        let label;
        if (kind === 'usb') {
          counters.usb += 1;
          label = `USB Wi-Fi ${counters.usb}`;
        } else if (kind === 'internal') {
          label = `Internal Wi-Fi ${counters.internal}`;
          counters.internal += 1;
        } else {
          counters.other += 1;
          label = `Wi-Fi Adapter ${counters.other}`;
        }
        if (option.value === recommended) label += ' (Recommended)';
        if (option.textContent !== label) option.textContent = label;
        option.removeAttribute('title');
        if (option.selected) selectedLabel = label;
      }
    } finally {
      normalizing = false;
    }

    const display = ensureSelectedLabel(row);
    if (selectedLabel) display.textContent = selectedLabel;
    return selectedLabel;
  }

  function decorateDetailsButton(info) {
    if (!info) return;
    const expanded = info.getAttribute('aria-expanded') === 'true';
    info.replaceChildren(document.createTextNode('Adapter details'));
    info.classList.add('pro-adapter-details-button');
    info.classList.remove('tip');
    info.removeAttribute('data-tip');
    info.title = expanded ? 'Hide adapter details' : 'Show adapter details';
    info.setAttribute('aria-label', info.title);
  }

  function decorate() {
    if (!isProMode()) return false;
    const select = document.getElementById('ap_adapter');
    const row = select?.closest('.pro-adapter-row');
    const info = document.getElementById('proAdapterInfo');
    const recommended = document.getElementById('btnUseRecommended');
    if (!select || !row || !info) return false;

    const stylesheet = document.querySelector('link[data-pro-guided-styles="authoritative"]');
    if (stylesheet && !stylesheet.href.includes('148-adapter-controls-3')) {
      stylesheet.href = '/assets/pro_guided_authoritative.css?v=148-adapter-controls-3';
    }

    if (recommended) {
      recommended.hidden = true;
      recommended.setAttribute('aria-hidden', 'true');
      recommended.tabIndex = -1;
    }

    decorateDetailsButton(info);
    normalizeOptions(select);

    if (observedSelect !== select) {
      if (optionsObserver) optionsObserver.disconnect();
      observedSelect = select;
      optionsObserver = new MutationObserver(() => {
        if (normalizing || !isProMode()) return;
        normalizeOptions(select);
      });
      optionsObserver.observe(select, { childList: true, subtree: true });
    }

    if (select.dataset.proStableLabelWired !== '1') {
      select.dataset.proStableLabelWired = '1';
      select.addEventListener('change', () => normalizeOptions(select));
    }
    return true;
  }

  function reconcile() {
    reconcileQueued = false;
    decorate();
  }

  function schedule() {
    if (reconcileQueued) return;
    reconcileQueued = true;
    window.setTimeout(reconcile, 0);
  }

  function start() {
    const observer = new MutationObserver(schedule);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-ui-mode', 'aria-expanded'],
    });
    window.addEventListener('pageshow', schedule);
    schedule();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
