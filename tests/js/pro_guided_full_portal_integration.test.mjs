import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import process from 'node:process';
import test from 'node:test';
import { JSDOM } from 'jsdom';

// jsdom event-listener promise rejections surface at the process level, not
// inside the window; collect them so timeout diagnostics can show the cause.
const processRejections = [];
process.on('unhandledRejection', (reason) => {
  processRejections.push(String((reason && reason.stack) || reason).slice(0, 500));
});

const ROOT = new URL('../../', import.meta.url);
const readAsset = (path) => readFile(new URL(path, ROOT), 'utf8');

const BASIC_QUICK_FIELDS = [
  'ap_adapter',
  'band_preference',
  'ap_security',
  'country',
  'enable_internet',
  'qos_preset',
];
const CONNECTION_FIELDS = [
  'ssid',
  'wpa2_passphrase',
  'band_preference',
  'ap_security',
  'country',
  'enable_internet',
];
const STEP3_FIELDS = CONNECTION_FIELDS.filter((key) => key !== 'enable_internet');
const PASSWORD_RULES = 'Use 8–63 characters. Control characters are not allowed.';
const NEUTRAL_PLACEHOLDER = 'Enter a new password to change it';

function assertInformationArchitecture(document) {
  // Internet sharing is out of Step 3 and lives under Troubleshooting.
  assert.equal(document.querySelector('#proStepHotspot [data-field="enable_internet"]'), null);
  assert.equal(document.querySelectorAll('[id="enable_internet"]').length, 1);
  const connectivity = document.querySelector('#tab-troubleshooting #proConnectivityCard');
  assert.ok(connectivity, 'Connectivity card must exist in Troubleshooting');
  assert.ok(
    connectivity.querySelector('[data-field="enable_internet"]'),
    'internet sharing must live in the Connectivity card',
  );
  assert.match(
    connectivity.textContent,
    /Disable this only when you want an isolated local hotspot without internet access\./,
  );

  // Connection Quality is the final section of the Troubleshooting shell.
  const shell = document.querySelector('#tab-troubleshooting .troubleshooting-shell');
  const quality = document.getElementById('proConnectionQuality');
  assert.ok(shell && quality);
  assert.equal(document.querySelectorAll('[id="proConnectionQuality"]').length, 1);
  assert.equal(quality.parentElement, shell, 'quality card must live in Troubleshooting');
  assert.equal(shell.lastElementChild, quality, 'quality card must be the final section');
  assert.equal(
    document.querySelector('#proGuidedWorkflow #proConnectionQuality'),
    null,
    'the setup workflow must not contain Connection Quality',
  );
  // 4 === Node.DOCUMENT_POSITION_FOLLOWING
  assert.ok(connectivity.compareDocumentPosition(quality) & 4,
    'Connectivity must appear before Connection Quality');
}

function assertAdvancedOrganization(document) {
  // Step 2: the redundant generic helper line is gone.
  assert.ok(
    !document.body.textContent.includes(
      'Choose the performance behavior that best matches this hotspot.',
    ),
    'the generic Step 2 helper line must not render',
  );

  // Step 4 headers carry only the title: no mirrored summary chips, and the
  // bodies render no gray intro copy.
  document.querySelectorAll('#proStepAdvanced .pro-config-details > summary').forEach((node) => {
    assert.equal(node.querySelector('.pro-config-summary'), null,
      'accordion headers must not render summary chips');
  });
  assert.equal(document.querySelector('#proStepAdvanced .pro-advanced-group-help'), null,
    'Step 4 bodies must not render intro copy');
  for (const copy of ['Channels, width, radio timing, and transmit power.',
                      'Gateway, DHCP, DNS.',
                      'Startup behavior and performance tuning.']) {
    assert.ok(!document.body.textContent.includes(copy),
      `intro copy must be gone: ${copy}`);
  }

  // Every Fine-tune field label carries the shared accessible info tip.
  const TIP_IDS = ['channel_5g', 'channel_6g', 'fallback_channel_2g', 'channel_auto_select',
                   'channel_width', 'tx_power', 'beacon_interval', 'dtim_period',
                   'short_guard_interval', 'lan_gateway_ip', 'dhcp_dns', 'dhcp_start_ip',
                   'dhcp_end_ip', 'ap_ready_timeout_s', 'cpu_governor_performance',
                   'sysctl_tuning', 'interrupt_coalescing'];
  for (const id of TIP_IDS) {
    const control = document.getElementById(id);
    const label = document.querySelector(`label[for="${id}"]`) || control.closest('label');
    const tip = label?.querySelector('.hint.tip-only .tip');
    assert.ok(tip, `${id} label must carry an info tip`);
    assert.ok(tip.getAttribute('data-tip'), `${id} tip must have help text`);
    assert.equal(tip.getAttribute('aria-label'), tip.getAttribute('data-tip'));
    assert.equal(tip.getAttribute('tabindex'), '0');
    assert.ok(!tip.hasAttribute('title'), `${id} tip must not use a native title tooltip`);
  }
  assert.match(
    document.querySelector('label[for="channel_5g"] .tip').getAttribute('data-tip'),
    /primary 5 GHz Wi-Fi channel/,
  );
  assert.match(
    document.querySelector('label[for="lan_gateway_ip"] .tip').getAttribute('data-tip'),
    /gateway IP address used by connected devices/,
  );
  const sgiTip = document.getElementById('short_guard_interval')
    .closest('label').querySelector('.tip');
  assert.match(sgiTip.getAttribute('data-tip'), /shorter guard interval/);

  // Fine-tune keeps only the genuine tuning controls.
  const inStep4 = (selector) => document.querySelector(`#proStepAdvanced ${selector}`);
  for (const id of ['channel_5g', 'channel_6g', 'fallback_channel_2g', 'channel_auto_select',
                    'channel_width', 'tx_power', 'beacon_interval', 'dtim_period',
                    'short_guard_interval', 'lan_gateway_ip', 'dhcp_dns', 'dhcp_start_ip',
                    'dhcp_end_ip', 'ap_ready_timeout_s', 'cpu_governor_performance',
                    'sysctl_tuning', 'interrupt_coalescing']) {
    assert.ok(inStep4(`#${id}`), `${id} must stay in Fine-tune`);
  }
  assert.ok(inStep4('.pro-advanced-toggles #cpu_governor_performance'),
    'tuning toggles must sit in the clean toggle list');
  for (const id of ['debug', 'wifi_power_save_disable', 'usb_autosuspend_disable',
                    'nat_accel', 'bridge_mode', 'firewalld_enabled', 'optimized_no_virt']) {
    assert.equal(inStep4(`#${id}`), null, `${id} must no longer live in Fine-tune`);
    assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1, `${id} must stay unique`);
  }

  // The relocated controls live in the Troubleshooting sections.
  const inPane = (selector) => document.querySelector(`#tab-troubleshooting ${selector}`);
  assert.ok(inPane('#proCompatibilityCard .pro-compat-toggles #wifi_power_save_disable'));
  assert.ok(inPane('#proCompatibilityCard .pro-compat-toggles #usb_autosuspend_disable'));
  assert.ok(inPane('#proCompatibilityCard .pro-compat-blocks [data-field="nat_accel"]'));
  assert.ok(inPane('#proCompatibilityCard .pro-compat-blocks [data-field="bridge_mode"]'));
  assert.ok(inPane('#proCompatibilityCard .pro-compat-blocks [data-field="firewalld_enabled"]'));
  assert.ok(inPane('#proCompatibilityCard .pro-compat-blocks [data-field="optimized_no_virt"]'));
  assert.ok(inPane('#proDebuggingCard [data-field="debug"]'));
  const shell = document.querySelector('#tab-troubleshooting .troubleshooting-shell');
  const compat = document.getElementById('proCompatibilityCard');
  const quality = document.getElementById('proConnectionQuality');
  // 4 === Node.DOCUMENT_POSITION_FOLLOWING
  assert.ok(compat.compareDocumentPosition(quality) & 4,
    'Connection Quality must stay the final section');
  assert.equal(shell.lastElementChild, quality);

  // The live controls mirror the loaded configuration values.
  assert.equal(document.getElementById('channel_width').value, '80');
  assert.equal(document.getElementById('beacon_interval').value, '50');
  assert.equal(document.getElementById('dtim_period').value, '1');
  assert.equal(document.getElementById('lan_gateway_ip').value, '192.168.68.1');
  assert.equal(document.getElementById('dhcp_start_ip').value, '192.168.68.10');
  assert.equal(document.getElementById('dhcp_end_ip').value, '192.168.68.250');
  assert.equal(document.getElementById('dhcp_dns').value, 'gateway');
  assert.equal(document.getElementById('ap_ready_timeout_s').value, '6');
  assert.equal(document.getElementById('firewalld_enabled').checked, true);
  assert.equal(document.getElementById('debug').checked, false);
  assert.equal(document.getElementById('channel_5g').value, '',
    'Auto channel keeps its true empty value with the Auto placeholder');
  assert.equal(document.getElementById('channel_5g').placeholder, 'Auto');
}

function assertPasswordPrivacy(document) {
  const hint = document.getElementById('passHint');
  assert.equal(hint.textContent, '', 'no saved/length disclosure below the password field');
  const input = document.getElementById('wpa2_passphrase');
  assert.equal(input.placeholder, NEUTRAL_PLACEHOLDER, 'placeholder must stay neutral');
  const tip = document.querySelector('[data-field="wpa2_passphrase"] .field-label-with-tip .tip');
  assert.ok(tip, 'password label must carry the info tip');
  assert.equal(tip.getAttribute('data-tip'), PASSWORD_RULES);
  assert.equal(tip.getAttribute('aria-label'), PASSWORD_RULES);
  assert.equal(tip.getAttribute('tabindex'), '0');
  for (const key of ['wpa2_passphrase', 'band_preference', 'ap_security']) {
    assert.ok(
      document.querySelector(`[data-field="${key}"] > .field-label-with-tip`),
      `${key} must use the shared label-row structure`,
    );
  }
}

function apiPayload(url, { passphraseSaved, enableInternet = true, running = false }) {
  const path = new URL(String(url), 'http://127.0.0.1:8732').pathname;
  if (path === '/v1/status') {
    // The daemon wraps responses in an envelope; the app reads r.json.data.
    return {
      result_code: 'ok',
      data: {
        running,
        state: running ? 'running' : 'stopped',
        adapter: 'wlan1',
        band: '5ghz',
        platform: { os: { id: 'cachyos', version_id: 'rolling' } },
        telemetry: { clients: [] },
      },
    };
  }
  if (path === '/v1/config') {
    return {
      result_code: 'ok',
      data: {
      ssid: 'VR-Hotspot',
      ...(passphraseSaved
        ? { wpa2_passphrase_set: true, wpa2_passphrase_len: 12 }
        : { wpa2_passphrase_set: false }),
      band_preference: '5ghz',
      ap_security: 'wpa2',
      country: 'US',
      enable_internet: enableInternet,
      ap_adapter: 'wlan1',
      qos_preset: 'balanced',
      channel_width: '80',
      channel_auto_select: false,
      bridge_mode: false,
      telemetry_enable: true,
      connection_quality_monitoring: true,
      beacon_interval: 50,
      dtim_period: 1,
      ap_ready_timeout_s: 6,
      lan_gateway_ip: '192.168.68.1',
      dhcp_start_ip: '192.168.68.10',
      dhcp_end_ip: '192.168.68.250',
      dhcp_dns: 'gateway',
      firewalld_enabled: true,
      debug: false,
      wifi_power_save_disable: false,
      optimized_no_virt: false,
      },
    };
  }
  if (path === '/v1/adapters') {
    return {
      data: {
        adapters: [{
          ifname: 'wlan1',
          name: 'Test USB Wi-Fi adapter',
          bus: 'usb',
          phy: 'phy2',
          recommended: true,
          score: 100,
          supports_ap: true,
          supports_2ghz: true,
          supports_5ghz: true,
          supports_6ghz: false,
          regdom: { country: 'US' },
          reasons: ['USB adapter', '5 GHz AP capable'],
        }],
        recommended: 'wlan1',
      },
    };
  }
  if (path.includes('preflight')) {
    return { ok: true, data: { blocking: [], warnings: [], recommended_actions: [] } };
  }
  if (path.includes('logs')) return { lines: [] };
  return { ok: true, data: {} };
}

function installBrowserStubs(window, options) {
  window.__fetchLog = [];
  window.fetch = async (url, init) => {
    window.__fetchLog.push(
      `${(init && init.method) || 'GET'} ${new URL(String(url), 'http://127.0.0.1:8732').pathname}`,
    );
    const payload = apiPayload(url, options);
    const body = JSON.stringify(payload);
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => payload,
      text: async () => body,
      blob: async () => new window.Blob([body], { type: 'application/json' }),
    };
  };
  window.matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  });
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  window.QRCode = class {
    constructor() {}
    clear() {}
    makeCode() {}
  };
  class ChartStub {
    constructor() {}
    destroy() {}
    update() {}
  }
  ChartStub.defaults = { color: '', borderColor: '', font: { family: '' } };
  window.Chart = ChartStub;
  window.HTMLCanvasElement.prototype.getContext = () => ({});
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.confirm = () => true;
  window.alert = () => {};
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async () => {} },
  });
}

function tick(window, ms = 80) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitFor(window, predicate, label, timeoutMs = 2500) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await tick(window, 25);
  }
  const doc = window.document;
  const diag = {
    dirty: doc.getElementById('dirty')?.textContent || '',
    msg: doc.getElementById('msg')?.textContent || '',
    saveState: doc.getElementById('proGuidedSaveState')?.textContent || '',
    status: doc.getElementById('proServiceStateText')?.textContent || '',
    primary: doc.getElementById('btnStart')?.textContent || '',
    primaryAction: doc.getElementById('btnStart')?.dataset.proGuidedAction || '',
    primaryWired: doc.getElementById('btnStart')?.dataset.proGuidedWired || '',
    saveRestartExists: !!doc.getElementById('btnSaveRestart'),
    saveRestartParent: doc.getElementById('btnSaveRestart')?.parentElement?.className || '',
    fetchTail: (window.__fetchLog || []).slice(-14),
    rejections: processRejections.slice(-3),
  };
  throw new Error(`Timed out waiting for ${label} :: ${JSON.stringify(diag)}`);
}

function toggleMode(window, advanced) {
  const toggle = window.document.getElementById('uiModeToggle');
  assert.ok(toggle, 'mode toggle must exist');
  toggle.checked = advanced;
  toggle.dispatchEvent(new window.Event('change', { bubbles: true }));
}

// Captures btnUseRecommended state inside the MutationObserver microtask that
// delivers each data-pro-guided-stage="ready" publication: no timer can run
// between the composer writing the attribute and this snapshot, so a complete
// capture proves readiness never depends on a later timer or observer.
function captureReadyPublications(window, document) {
  const captures = [];
  new window.MutationObserver(() => {
    if (document.body.dataset.proGuidedStage !== 'ready') return;
    const recommended = document.getElementById('btnUseRecommended');
    captures.push({
      hidden: recommended?.hidden,
      ariaHidden: recommended?.getAttribute('aria-hidden'),
      tabIndex: recommended?.tabIndex,
      display: recommended?.style.display,
    });
  }).observe(document.body, {
    attributes: true,
    attributeFilter: ['data-pro-guided-stage'],
  });
  return captures;
}

function assertBasicLayout(document) {
  assert.equal(document.body.dataset.uiMode, 'basic');
  const workflow = document.getElementById('proGuidedWorkflow');
  if (workflow) {
    assert.ok(
      workflow.closest('[data-ui-section="advanced"]'),
      'persistent Pro workflow must remain isolated inside the hidden Advanced section',
    );
  }
  assert.ok(document.querySelector('#basicGuidedAdapterSlot [data-field="ap_adapter"]'));
  assert.ok(document.querySelector('#basicGuidedProfileSlot [data-field="qos_preset"]'));
  assert.ok(document.querySelector('#basicGuidedSsidSlot [data-field="ssid"]'));
  for (const key of ['band_preference', 'ap_security', 'country', 'enable_internet']) {
    assert.ok(
      document.querySelector(`#basicGuidedTechnicalDefaults [data-field="${key}"]`),
      `${key} should be in the production Basic technical-default container`,
    );
  }
  assert.equal(document.querySelector('[data-ui-section="basic"] .pro-runtime-wrapper'), null);
  assert.equal(document.body.hasAttribute('data-pro-band'), false);

  // The Recommended button is redundant in both modes (the adapter label
  // already says "(Recommended)"), so it stays hidden with deterministic
  // state everywhere while remaining a single live node.
  assertRecommendedHidden(document);
  // Basic owns its own save surface, so the Pro staging state is cleared.
  for (const id of ['btnSaveConfig', 'btnSaveRestart']) {
    const control = document.getElementById(id);
    assert.equal(control.hidden, false, `${id} must not stay Pro-hidden in Basic`);
    assert.equal(control.hasAttribute('aria-hidden'), false);
    assert.equal(control.tabIndex, 0);
  }
}

function assertRecommendedHidden(document) {
  const recommended = document.getElementById('btnUseRecommended');
  assert.ok(recommended, 'the live Recommended node must still exist');
  assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);
  assert.equal(recommended.style.display, 'none');
}

function assertProLayout(document) {
  assert.equal(document.body.dataset.uiMode, 'advanced');
  assert.equal(document.body.dataset.proGuidedStage, 'ready');
  const workflow = document.getElementById('proGuidedWorkflow');
  assert.ok(workflow, 'authoritative Pro workflow must exist');
  assert.deepEqual(
    Array.from(workflow.querySelectorAll('.pro-guided-step')).map((node) => node.dataset.step),
    ['1', '2', '3', '4', '5'],
  );
  assert.ok(document.querySelector('#proStepAdapter [data-field="ap_adapter"]'));
  const adapterSelect = document.getElementById('ap_adapter');
  assert.equal(adapterSelect?.selectedOptions[0]?.textContent, 'USB Wi-Fi 1 (Recommended)');
  assert.equal(adapterSelect?.selectedOptions[0]?.hasAttribute('title'), false);
  assert.equal(document.querySelector('.pro-adapter-selected-label'), null);

  assertRecommendedHidden(document);

  const adapterInfo = document.getElementById('proAdapterInfo');
  const adapterDetails = document.getElementById('proAdapterDetails');
  assert.ok(adapterInfo, 'adapter details control must exist');
  assert.equal(adapterInfo.textContent, 'Adapter details');
  assert.equal(adapterInfo.querySelector('.pro-adapter-details-icon'), null);
  assert.equal(adapterInfo.title, 'Show adapter details');
  assert.equal(adapterInfo.hasAttribute('data-tip'), false);
  assert.equal(adapterInfo.getAttribute('aria-expanded'), 'false');
  assert.ok(adapterDetails);
  assert.equal(adapterDetails.hidden, true);

  const currentStyles = document.querySelector('link[data-pro-adapter-controls]');
  assert.ok(currentStyles, 'current adapter stylesheet must be loaded after composer styles');
  assert.match(currentStyles.href, /148-owned-cells-1/);

  adapterInfo.click();
  assert.equal(adapterInfo.getAttribute('aria-expanded'), 'true');
  assert.equal(adapterInfo.title, 'Hide adapter details');
  assert.equal(adapterDetails.hidden, false);
  assert.match(adapterDetails.textContent, /Interface: wlan1/);
  adapterInfo.click();
  assert.equal(adapterInfo.getAttribute('aria-expanded'), 'false');
  assert.equal(adapterDetails.hidden, true);

  const adapterHint = document.getElementById('adapterHint');
  assert.equal(document.body.dataset.proBand, '5ghz');
  assert.equal(adapterHint?.hidden, true);
  assert.equal(adapterHint?.textContent, '');
  assert.equal(document.querySelector('#proStepAdapter [data-adapter-readiness-card]'), null);
  assert.ok(document.querySelector('#proStepPerformance .preset-bar'));
  for (const key of STEP3_FIELDS) {
    assert.ok(
      document.querySelector(`#proStepHotspot [data-field="${key}"]`),
      `${key} should be in production Pro Step 3`,
    );
  }
  assertInformationArchitecture(document);
  assertPasswordPrivacy(document);
  assertAdvancedOrganization(document);
  assert.equal(document.querySelectorAll('#proStepAdvanced .pro-config-details').length, 3);
  // Step 5 exposes exactly one actionable control: the primary Start/Stop.
  assert.ok(document.querySelector('#proStepAction #btnStart'));
  const staging = document.querySelector('#proStepAction .pro-guided-hidden-staging');
  assert.ok(staging && staging.hidden, 'hidden staging must exist and stay hidden');
  assert.equal(staging.getAttribute('aria-hidden'), 'true');
  for (const id of ['btnSaveConfig', 'btnSaveRestart']) {
    const control = document.getElementById(id);
    assert.ok(staging.contains(control), `the live ${id} node stays parked in staging`);
    assert.equal(control.hidden, true, `${id} must not be visible in Step 5`);
    assert.equal(control.getAttribute('aria-hidden'), 'true');
    assert.equal(control.tabIndex, -1);
    assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1);
  }
  assert.equal(document.querySelector('#proStepAction .pro-guided-save-actions'), null,
    'no leftover save-actions wrapper may remain');
  assert.equal(document.querySelector('#proStepAction #btnRepair'), null,
    'Repair Network must not be visible in Step 5');
  assert.ok(
    document.querySelector('#tab-troubleshooting .troubleshooting-actions #btnRepair'),
    'Repair Network belongs to the Troubleshooting recovery actions',
  );
  assert.equal(document.querySelectorAll('[id="btnRepair"]').length, 1);
  assert.equal(document.querySelector('#proStepAction .pro-guided-secondary-actions'), null,
    'no empty secondary action track may remain');
  const actionButtons = document.querySelector('#proStepAction .pro-guided-action-buttons');
  const visibleActions = Array.from(actionButtons.children).filter((node) => !node.hidden);
  assert.deepEqual(visibleActions.map((node) => node.id), ['btnStart'],
    'the action column exposes exactly one actionable control');
  assert.ok(document.getElementById('proConnectionQuality'));
  assert.ok(document.getElementById('tab-troubleshooting'));
  assertPasswordRowComposed(document);
}

function assertPasswordRowComposed(document) {
  const rows = document.querySelectorAll('.pro-password-row');
  assert.equal(rows.length, 1, 'exactly one composed password row must exist');
  const row = rows[0];
  const cell = row.querySelector(':scope > .pro-password-input-cell');
  assert.ok(cell, 'application-owned input cell must be a direct row child');
  const input = document.getElementById('wpa2_passphrase');
  const reveal = document.getElementById('btnRevealPass');
  const qr = document.getElementById('btnShowQr');
  assert.ok(cell.contains(input), 'input must live inside the application-owned cell');
  assert.equal(reveal.parentElement, row, 'reveal must own the second cell');
  assert.equal(qr.parentElement, row, 'QR must own the third cell');
  const children = Array.from(row.children);
  assert.ok(
    children.indexOf(cell) < children.indexOf(reveal)
      && children.indexOf(reveal) < children.indexOf(qr),
    'application cells must stay ordered cell -> reveal -> QR',
  );
  const field = document.querySelector('[data-field="wpa2_passphrase"]');
  assert.equal(row.parentElement, field);
  for (const legacy of field.querySelectorAll('.input-with-action, .row')) {
    assert.equal(
      legacy.querySelector('#wpa2_passphrase, #btnRevealPass, #btnShowQr'),
      null,
      'original production wrappers must not contain the controls in Pro',
    );
  }
  const hint = document.getElementById('passHint');
  assert.ok(hint, 'passHint must exist');
  assert.ok(field.contains(hint) && !row.contains(hint), 'passHint must sit outside the row');
  // 4 === Node.DOCUMENT_POSITION_FOLLOWING
  assert.ok(row.compareDocumentPosition(hint) & 4, 'passHint must follow the row');
}

async function runFullPortalScenario({ passphraseSaved, enableInternet = true }) {
  const [html, fieldVisibility, ui, basicGuided, composer, portalExtensions] = await Promise.all([
    readAsset('assets/index.html'),
    readAsset('assets/field_visibility.js'),
    readAsset('assets/ui.js'),
    readAsset('assets/basic_guided.js'),
    readAsset('assets/pro_guided_workflow.js'),
    readAsset('assets/devhub_upload.js'),
  ]);

  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const { document } = window;
  const stubOptions = { passphraseSaved, enableInternet, running: false };
  installBrowserStubs(window, stubOptions);
  window.localStorage.setItem('vrhs_ui_mode', 'basic');

  const errors = [];
  const unhandled = [];
  window.console.error = (...args) => errors.push(args.map(String).join(' '));
  window.addEventListener('error', (event) => unhandled.push(String(event.error || event.message)));
  window.addEventListener('unhandledrejection', (event) => unhandled.push(String(event.reason)));

  assert.ok(
    !portalExtensions.includes('btnUseRecommended'),
    'devhub_upload.js must not own btnUseRecommended visibility',
  );

  const readyCaptures = captureReadyPublications(window, document);
  window.eval(fieldVisibility);
  window.eval(ui);
  window.eval(basicGuided);
  window.eval(composer);
  window.eval(portalExtensions);
  document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

  assert.equal(typeof window.setToken, 'function');
  assert.equal(typeof window.enterAuthenticatedApp, 'function');
  assert.equal(typeof window.loadAdapters, 'function');
  assert.equal(window.loadAdapters.__vrhotspotFriendlyAdapters, true);
  window.setToken('integration-test-token');
  window.enterAuthenticatedApp();

  await waitFor(window, () => document.querySelector('.pro-service-card'), 'base Pro service card');
  await waitFor(window, () => document.querySelector('.basic-guided-setup-card'), 'guided Basic setup');
  await tick(window, 150);
  assertBasicLayout(document);

  // Password-manager opt-out attributes must survive on the real inputs.
  for (const id of ['ssid', 'wpa2_passphrase']) {
    const optOut = document.getElementById(id);
    assert.equal(optOut.getAttribute('autocomplete'), 'off', `${id} autocomplete`);
    assert.equal(optOut.getAttribute('data-lpignore'), 'true', `${id} lpignore`);
    assert.ok(optOut.hasAttribute('data-1p-ignore'), `${id} 1p-ignore`);
    assert.ok(optOut.hasAttribute('data-bwignore'), `${id} bwignore`);
  }

  const recommendedNode = document.getElementById('btnUseRecommended');
  const internetNode = document.getElementById('enable_internet');
  const dirtyBaseline = document.getElementById('dirty')?.textContent || '';
  assert.equal(internetNode.checked, enableInternet,
    'the checkbox must reflect the saved configuration');
  const passwordNodes = {
    input: document.getElementById('wpa2_passphrase'),
    reveal: document.getElementById('btnRevealPass'),
    qr: document.getElementById('btnShowQr'),
  };
  const assertPasswordIdentity = () => {
    assert.equal(document.getElementById('wpa2_passphrase'), passwordNodes.input);
    assert.equal(document.getElementById('btnRevealPass'), passwordNodes.reveal);
    assert.equal(document.getElementById('btnShowQr'), passwordNodes.qr);
    for (const id of ['wpa2_passphrase', 'btnRevealPass', 'btnShowQr']) {
      assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1);
    }
  };

  for (let cycle = 0; cycle < 3; cycle += 1) {
    toggleMode(window, true);
    await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', `Pro ready cycle ${cycle + 1}`);
    await waitFor(window, () => document.querySelector('#ap_adapter option')?.textContent === 'USB Wi-Fi 1 (Recommended)', `friendly adapter cycle ${cycle + 1}`);
    await waitFor(window, () => document.getElementById('proAdapterInfo')?.textContent === 'Adapter details', `adapter details control cycle ${cycle + 1}`);
    assertProLayout(document);
    assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
    assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);
    assertPasswordIdentity();
    assert.equal(document.getElementById('enable_internet'), internetNode,
      'the internet-sharing checkbox must be the same live node');
    assert.equal(internetNode.checked, enableInternet,
      'mode transitions must not change the saved internet-sharing state');

    if (cycle === 0) {
      await window.loadAdapters();
      assert.equal(
        document.getElementById('ap_adapter').selectedOptions[0]?.textContent,
        'USB Wi-Fi 1 (Recommended)',
        'the wrapped adapter loader must return only after friendly labels are restored',
      );
      assert.equal(document.querySelector('.pro-adapter-selected-label'), null);
    }

    toggleMode(window, false);
    await waitFor(window, () => document.body.dataset.proGuidedStage === 'waiting-for-pro', `Basic restored cycle ${cycle + 1}`);
    await tick(window, 80);
    assertBasicLayout(document);
    assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
    assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);
    assertPasswordIdentity();
  }

  toggleMode(window, true);
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', 'final Pro composition');
  await waitFor(window, () => document.querySelector('#ap_adapter option')?.textContent === 'USB Wi-Fi 1 (Recommended)', 'final friendly adapter label');
  await waitFor(window, () => document.getElementById('proAdapterInfo')?.textContent === 'Adapter details', 'final adapter details control');
  assertProLayout(document);
  assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
  assertPasswordIdentity();

  // The composer must not report ready while password composition is
  // incomplete, and must converge once the missing control returns.
  const qr = document.getElementById('btnShowQr');
  const qrParent = qr.parentElement;
  qr.remove();
  await waitFor(
    window,
    () => document.body.dataset.proGuidedStage === 'composing',
    'stage must leave ready when a password control disappears',
  );
  await tick(window, 400);
  assert.equal(
    document.body.dataset.proGuidedStage,
    'composing',
    'ready must not be republished while the password row is incomplete',
  );
  qrParent.appendChild(qr);
  await waitFor(
    window,
    () => document.body.dataset.proGuidedStage === 'ready',
    'composer must recover after the control returns',
  );
  assertPasswordRowComposed(document);
  assertPasswordIdentity();

  // Late third-party injections (password-manager style) must not demote
  // readiness, break composition, start a DOM fight, or loop the composer.
  const rowEl = document.querySelector('.pro-password-row');
  const cellEl = rowEl.querySelector(':scope > .pro-password-input-cell');
  const sibling = document.createElement('div');
  sibling.className = 'thirdparty-icon';
  rowEl.appendChild(sibling);
  const absControl = document.createElement('button');
  absControl.style.position = 'absolute';
  cellEl.appendChild(absControl);
  const wrapper = document.createElement('div');
  wrapper.className = 'thirdparty-wrap';
  const inputEl = document.getElementById('wpa2_passphrase');
  inputEl.parentNode.insertBefore(wrapper, inputEl);
  wrapper.appendChild(inputEl);
  const shadowHost = document.createElement('div');
  shadowHost.attachShadow({ mode: 'open' });
  rowEl.appendChild(shadowHost);
  await tick(window, 500);
  assert.equal(
    document.body.dataset.proGuidedStage,
    'ready',
    'injected third-party nodes must not demote readiness',
  );
  assertPasswordRowComposed(document);
  assertPasswordIdentity();
  assert.ok(
    cellEl.contains(wrapper) && wrapper.contains(inputEl),
    'the composer must not fight a third-party wrapper inside its cell',
  );
  // No mutation/reconcile loop: once settled, the decorators must leave the
  // password row structurally quiet. The base app's config poller refreshes
  // input attributes (type/placeholder) as data flow; that is exempt, but no
  // node may be added, removed, or re-decorated (a DOM fight shows up here).
  const rowRecords = [];
  new window.MutationObserver((records) => rowRecords.push(...records))
    .observe(rowEl, { childList: true, subtree: true, attributes: true });
  await tick(window, 700);
  const structural = rowRecords.filter(
    (record) => record.type === 'childList' || record.target !== inputEl,
  );
  assert.equal(
    structural.length,
    0,
    `password row must be structurally quiet at idle, saw ${structural.length} mutations`
      + ` (${structural.map((r) => `${r.type}:${r.attributeName || ''}@${r.target.id || r.target.className}`).join(', ')})`,
  );
  assert.equal(document.body.dataset.proGuidedStage, 'ready');

  // Relocation and mode transitions alone must never dirty the config, and
  // the saved internet-sharing state must survive untouched.
  assert.equal(document.getElementById('dirty')?.textContent || '', dirtyBaseline,
    'relocating fields must not mark the configuration dirty');
  assert.equal(document.getElementById('enable_internet'), internetNode);
  assert.equal(internetNode.checked, enableInternet);

  assert.ok(
    readyCaptures.length >= 4,
    `expected at least 4 ready publications, saw ${readyCaptures.length}`,
  );
  for (const capture of readyCaptures) {
    assert.deepEqual(capture, {
      hidden: true,
      ariaHidden: 'true',
      tabIndex: -1,
      display: 'none',
    }, 'recommended button state must be complete when ready is published');
  }
  // The save payload must still collect every relocated control. This is a
  // genuine edit, so it runs after the clean-dirty assertions above.
  const debugToggle = document.getElementById('debug');
  debugToggle.checked = true;
  debugToggle.dispatchEvent(new window.Event('change', { bubbles: true }));
  await tick(window, 80);
  const form = window.getForm();
  assert.equal(form.debug, true, 'relocated debug toggle must reach the save payload');
  assert.equal(typeof form.wifi_power_save_disable, 'boolean',
    'relocated power toggles must reach the save payload');
  assert.equal(typeof form.nat_accel, 'boolean',
    'relocated NAT toggle must reach the save payload');
  assert.equal(typeof form.cpu_governor_performance, 'boolean',
    'tuning toggles must reach the save payload');

  // Running hotspot + autosaved change: the primary action must offer Apply
  // Changes & Restart and drive exactly one restart through the live
  // (hidden) Save & Restart node.
  stubOptions.running = true;
  const statusText = document.getElementById('proServiceStateText');
  statusText.textContent = 'Running';
  await tick(window, 150);
  // Autosave's field listeners require trusted events, which jsdom cannot
  // synthesize; the performance-preset click drives the identical
  // scheduleSave(true) path and is a legitimate user edit.
  document.getElementById('btnApplyVrProfileHigh').click();
  await waitFor(
    window,
    () => document.getElementById('btnStart').dataset.proGuidedAction === 'apply',
    'primary must switch to the apply action',
    8000,
  );
  assert.equal(document.getElementById('btnStart').textContent, 'Apply Changes & Restart');
  // The apply affordance and its delegation target must both be in place.
  // Actually clicking the primary is covered by the real-browser test:
  // synthetic clicks in jsdom reach the base start/stop handler instead of
  // the composer's capture interception, which no real browser does.
  const saveRestart = document.getElementById('btnSaveRestart');
  assert.ok(saveRestart, 'the live save-and-restart control must remain available');
  assert.equal(document.getElementById('btnStart').dataset.proGuidedWired, '1',
    'the primary action must be wired for the apply flow');
  stubOptions.running = false;
  statusText.textContent = 'Stopped';
  await tick(window, 150);

  assert.deepEqual(errors, []);
  assert.deepEqual(unhandled, []);
  dom.window.close();
}

test('real portal composes Pro across toggles with a saved passphrase and explicit internet-sharing false', async () => {
  await runFullPortalScenario({ passphraseSaved: true, enableInternet: false });
});

test('real portal composes Pro across toggles on a clean install without a saved passphrase', async () => {
  await runFullPortalScenario({ passphraseSaved: false, enableInternet: true });
});
