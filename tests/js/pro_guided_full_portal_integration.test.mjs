import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

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

function apiPayload(url) {
  const path = new URL(String(url), 'http://127.0.0.1:8732').pathname;
  if (path === '/v1/status') {
    return {
      running: false,
      state: 'stopped',
      adapter: 'wlan1',
      band: '5ghz',
      platform: { os: { id: 'cachyos', version_id: 'rolling' } },
      telemetry: { clients: [] },
    };
  }
  if (path === '/v1/config') {
    return {
      ssid: 'VR-Hotspot',
      wpa2_passphrase_set: true,
      wpa2_passphrase_len: 12,
      band_preference: '5ghz',
      ap_security: 'wpa2',
      country: 'US',
      enable_internet: true,
      ap_adapter: 'wlan1',
      qos_preset: 'balanced',
      channel_width: '80',
      channel_auto_select: false,
      bridge_mode: false,
      telemetry_enable: true,
      connection_quality_monitoring: true,
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

function installBrowserStubs(window) {
  window.fetch = async (url) => {
    const payload = apiPayload(url);
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
  throw new Error(`Timed out waiting for ${label}`);
}

function toggleMode(window, advanced) {
  const toggle = window.document.getElementById('uiModeToggle');
  assert.ok(toggle, 'mode toggle must exist');
  toggle.checked = advanced;
  toggle.dispatchEvent(new window.Event('change', { bubbles: true }));
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

  const stableLabel = document.querySelector('#proStepAdapter .pro-adapter-selected-label');
  assert.ok(stableLabel, 'stable selected-adapter label must exist');
  assert.equal(stableLabel.textContent, 'USB Wi-Fi 1 (Recommended)');

  const recommended = document.getElementById('btnUseRecommended');
  assert.ok(recommended);
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);

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
  for (const key of CONNECTION_FIELDS) {
    assert.ok(
      document.querySelector(`#proStepHotspot [data-field="${key}"]`),
      `${key} should be in production Pro Step 3`,
    );
  }
  assert.equal(document.querySelectorAll('#proStepAdvanced .pro-config-details').length, 3);
  for (const id of ['btnStart', 'btnSaveConfig', 'btnSaveRestart', 'btnRepair']) {
    assert.ok(document.querySelector(`#proStepAction #${id}`), `${id} should be in production Pro Step 5`);
  }
  assert.ok(document.getElementById('proConnectionQuality'));
  assert.ok(document.getElementById('tab-troubleshooting'));
}

test('real portal scripts preserve Basic and compose Pro across repeated toggles', async () => {
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
  installBrowserStubs(window);
  window.localStorage.setItem('vrhs_ui_mode', 'basic');

  const errors = [];
  const unhandled = [];
  window.console.error = (...args) => errors.push(args.map(String).join(' '));
  window.addEventListener('error', (event) => unhandled.push(String(event.error || event.message)));
  window.addEventListener('unhandledrejection', (event) => unhandled.push(String(event.reason)));

  window.eval(fieldVisibility);
  window.eval(ui);
  window.eval(basicGuided);
  window.eval(composer);
  window.eval(portalExtensions);
  document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

  assert.equal(typeof window.setToken, 'function');
  assert.equal(typeof window.enterAuthenticatedApp, 'function');
  window.setToken('integration-test-token');
  window.enterAuthenticatedApp();

  await waitFor(window, () => document.querySelector('.pro-service-card'), 'base Pro service card');
  await waitFor(window, () => document.querySelector('.basic-guided-setup-card'), 'guided Basic setup');
  await tick(window, 150);
  assertBasicLayout(document);

  for (let cycle = 0; cycle < 3; cycle += 1) {
    toggleMode(window, true);
    await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', `Pro ready cycle ${cycle + 1}`);
    await waitFor(window, () => document.querySelector('#ap_adapter option')?.textContent === 'USB Wi-Fi 1 (Recommended)', `friendly adapter cycle ${cycle + 1}`);
    await waitFor(window, () => document.querySelector('.pro-adapter-selected-label')?.textContent === 'USB Wi-Fi 1 (Recommended)', `stable label cycle ${cycle + 1}`);
    assertProLayout(document);

    if (cycle === 0) {
      const adapterSelect = document.getElementById('ap_adapter');
      const stableLabel = document.querySelector('.pro-adapter-selected-label');
      const rawOption = document.createElement('option');
      rawOption.value = 'wlan1';
      rawOption.textContent = '* wlan1 (phy2, 5G/2G, reg=US, score=100, AP)';
      rawOption.selected = true;
      adapterSelect.replaceChildren(rawOption);
      assert.equal(
        stableLabel.textContent,
        'USB Wi-Fi 1 (Recommended)',
        'the painted label must remain stable while the native option list is rebuilt',
      );
      await tick(window, 0);
      assert.equal(
        adapterSelect.selectedOptions[0]?.textContent,
        'USB Wi-Fi 1 (Recommended)',
        'adapter inventory refresh must normalize the native option list',
      );
      assert.equal(stableLabel.textContent, 'USB Wi-Fi 1 (Recommended)');
    }

    toggleMode(window, false);
    await waitFor(window, () => document.body.dataset.proGuidedStage === 'waiting-for-pro', `Basic restored cycle ${cycle + 1}`);
    await tick(window, 80);
    assertBasicLayout(document);
  }

  toggleMode(window, true);
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', 'final Pro composition');
  await waitFor(window, () => document.querySelector('#ap_adapter option')?.textContent === 'USB Wi-Fi 1 (Recommended)', 'final friendly adapter label');
  await waitFor(window, () => document.querySelector('.pro-adapter-selected-label')?.textContent === 'USB Wi-Fi 1 (Recommended)', 'final stable adapter label');
  assertProLayout(document);
  assert.deepEqual(errors, []);
  assert.deepEqual(unhandled, []);
  dom.window.close();
});
