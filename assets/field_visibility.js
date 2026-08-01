window.UI_FIELD_VISIBILITY = {
  ssid: "basic",
  wpa2_passphrase: "basic",
  band_preference: "basic",
  ap_security: "basic",
  country: "basic",
  ap_adapter: "basic",
  enable_internet: "basic",
  qos_preset: "basic",

  channel_6g: "advanced",
  channel_width: "advanced",
  beacon_interval: "advanced",
  dtim_period: "advanced",
  short_guard_interval: "advanced",
  tx_power: "advanced",
  channel_auto_select: "advanced",
  ap_ready_timeout_s: "advanced",
  fallback_channel_2g: "advanced",
  optimized_no_virt: "advanced",
  lan_gateway_ip: "advanced",
  dhcp_start_ip: "advanced",
  dhcp_end_ip: "advanced",
  dhcp_dns: "advanced",
  wifi_power_save_disable: "advanced",
  usb_autosuspend_disable: "advanced",
  cpu_governor_performance: "advanced",
  sysctl_tuning: "advanced",
  cpu_affinity: "advanced",
  irq_affinity: "advanced",
  interrupt_coalescing: "advanced",
  tcp_low_latency: "advanced",
  memory_tuning: "advanced",
  io_scheduler_optimize: "advanced",
  telemetry_enable: "advanced",
  telemetry_interval_s: "advanced",
  watchdog_enable: "advanced",
  watchdog_interval_s: "advanced",
  connection_quality_monitoring: "advanced",
  auto_channel_switch: "advanced",
  nat_accel: "advanced",
  bridge_mode: "advanced",
  bridge_name: "advanced",
  bridge_uplink: "advanced",
  firewalld_enabled: "advanced",
  firewalld_enable_masquerade: "advanced",
  firewalld_enable_forward: "advanced",
  firewalld_cleanup_on_stop: "advanced",
  firewalld_zone: "advanced",
  debug: "advanced"
};

(function loadDeveloperHubAssets() {
  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/assets/devhub.css';
  document.head.appendChild(stylesheet);

  const script = document.createElement('script');
  script.src = '/assets/devhub.js';
  script.async = false;
  document.head.appendChild(script);
})();

(function addManagedPlatformToolsControls() {
  const TERMS_URL = 'https://developer.android.com/studio/terms';

  function feedback(message, state) {
    const node = document.getElementById('devhubFeedback');
    if (!node) return;
    node.textContent = message;
    node.dataset.state = state || 'idle';
  }

  async function toolsRequest(path, body, button) {
    if (button) button.disabled = true;
    feedback(path.endsWith('/install')
      ? 'Downloading and installing Android Platform-Tools...'
      : 'Removing VRhotspot-managed Android Platform-Tools...', 'loading');
    try {
      const response = await api(path, {
        method: 'POST',
        body: JSON.stringify(body || {}),
      });
      if (!response.ok) {
        const code = response.json && response.json.result_code
          ? response.json.result_code
          : `HTTP ${response.status}`;
        feedback(`Managed tools operation failed: ${code}`, 'error');
        return;
      }
      const result = response.json && response.json.data ? response.json.data : {};
      feedback(result.message || 'Managed tools operation completed.', 'success');
      const refresh = document.getElementById('devhubRefresh');
      if (refresh) refresh.click();
    } catch {
      feedback('Managed tools operation could not reach the VRhotspot daemon.', 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function inject() {
    if (document.getElementById('devhubToolsManagement')) return true;
    const toolsGrid = document.querySelector('#tab-devhub .devhub-tools-grid');
    if (!toolsGrid) return false;

    const section = document.createElement('div');
    section.id = 'devhubToolsManagement';
    section.className = 'devhub-span';

    const checks = document.createElement('div');
    checks.className = 'devhub-checks mt-12';
    const label = document.createElement('label');
    const accept = document.createElement('input');
    accept.id = 'devhubAcceptToolsLicense';
    accept.type = 'checkbox';
    const copy = document.createElement('span');
    copy.append('I accept the ');
    const terms = document.createElement('a');
    terms.href = TERMS_URL;
    terms.target = '_blank';
    terms.rel = 'noopener noreferrer';
    terms.textContent = 'Android SDK License Agreement';
    copy.appendChild(terms);
    label.append(accept, copy);
    checks.appendChild(label);

    const actions = document.createElement('div');
    actions.className = 'devhub-actions mt-12';
    const install = document.createElement('button');
    install.id = 'devhubInstallTools';
    install.type = 'button';
    install.className = 'btn primary';
    install.textContent = 'Install Managed ADB';
    const remove = document.createElement('button');
    remove.id = 'devhubRemoveTools';
    remove.type = 'button';
    remove.className = 'btn secondary';
    remove.textContent = 'Remove Managed ADB';
    actions.append(install, remove);

    install.addEventListener('click', () => {
      if (!accept.checked) {
        feedback('Review and accept the Android SDK License Agreement first.', 'error');
        return;
      }
      void toolsRequest(
        '/v1/devbridge/tools/install',
        { license_accepted: true },
        install,
      );
    });
    remove.addEventListener('click', () => {
      void toolsRequest('/v1/devbridge/tools/remove', {}, remove);
    });

    section.append(checks, actions);
    toolsGrid.appendChild(section);
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
