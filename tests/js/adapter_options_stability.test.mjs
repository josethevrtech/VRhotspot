import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const ROOT = new URL('../../', import.meta.url);
const readAsset = (path) => readFile(new URL(path, ROOT), 'utf8');

function makeInventory() {
  return {
    recommended: 'wlan1',
    adapters: [
      {
        ifname: 'wlan1', name: 'Test USB Wi-Fi adapter', bus: 'usb',
        phy: 'phy2', recommended: true, score: 100, supports_ap: true,
        supports_2ghz: true, supports_5ghz: true, supports_6ghz: false,
        regdom: { country: 'US' }, reasons: ['USB adapter'],
      },
      {
        ifname: 'wlan0', name: 'Internal Wi-Fi', bus: 'pci',
        phy: 'phy0', recommended: false, score: 40, supports_ap: true,
        supports_2ghz: true, supports_5ghz: true, supports_6ghz: false,
        regdom: { country: 'US' }, reasons: [],
      },
    ],
  };
}

function apiPayload(url, inventory) {
  const path = new URL(String(url), 'http://127.0.0.1:8732').pathname;
  if (path === '/v1/status') {
    // The daemon wraps responses in an envelope; the app reads r.json.data.
    return {
      result_code: 'ok',
      data: {
        running: false, state: 'stopped', adapter: 'wlan1', band: '5ghz',
        platform: { os: { id: 'cachyos', version_id: 'rolling' } },
        telemetry: { clients: [] },
      },
    };
  }
  if (path === '/v1/config') {
    return {
      result_code: 'ok',
      data: {
        ssid: 'VR-Hotspot', wpa2_passphrase_set: true, wpa2_passphrase_len: 12,
        band_preference: '5ghz', ap_security: 'wpa2', country: 'US',
        enable_internet: true, ap_adapter: 'wlan1', qos_preset: 'balanced',
        channel_width: '80', channel_auto_select: false, bridge_mode: false,
        telemetry_enable: true, connection_quality_monitoring: true,
      },
    };
  }
  if (path === '/v1/adapters') {
    return { data: JSON.parse(JSON.stringify(inventory)) };
  }
  if (path.includes('preflight')) {
    return { ok: true, data: { blocking: [], warnings: [], recommended_actions: [] } };
  }
  if (path.includes('logs')) return { lines: [] };
  return { ok: true, data: {} };
}

function installBrowserStubs(window, inventoryRef) {
  window.fetch = async (url) => {
    const payload = apiPayload(url, inventoryRef.value);
    const body = JSON.stringify(payload);
    return {
      ok: true, status: 200, headers: { get: () => 'application/json' },
      json: async () => payload, text: async () => body,
      blob: async () => new window.Blob([body], { type: 'application/json' }),
    };
  };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.QRCode = class { constructor() {} clear() {} makeCode() {} };
  class ChartStub { constructor() {} destroy() {} update() {} }
  ChartStub.defaults = { color: '', borderColor: '', font: { family: '' } };
  window.Chart = ChartStub;
  window.HTMLCanvasElement.prototype.getContext = () => ({});
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.confirm = () => true;
  window.alert = () => {};
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true, value: { writeText: async () => {} },
  });
}

const tick = (window, ms = 80) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function waitFor(window, predicate, label, timeoutMs = 4000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await tick(window, 25);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function toggleMode(window, advanced) {
  const toggle = window.document.getElementById('uiModeToggle');
  toggle.checked = advanced;
  toggle.dispatchEvent(new window.Event('change', { bubbles: true }));
}

function observeSelect(window, select) {
  const counts = { childList: 0, attrs: 0 };
  const observer = new window.MutationObserver((records) => {
    for (const record of records) {
      if (record.type === 'childList') counts.childList += 1;
      else counts.attrs += 1;
    }
  });
  observer.observe(select, { childList: true, subtree: true, attributes: true });
  return { counts, observer };
}

test('adapter options are stable across identical inventory refreshes', async () => {
  const html = await readAsset('assets/index.html');
  const dom = new JSDOM(html, {
    runScripts: 'outside-only', pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const { document } = window;
  const inventoryRef = { value: makeInventory() };
  installBrowserStubs(window, inventoryRef);
  window.localStorage.setItem('vrhs_ui_mode', 'basic');

  const errors = [];
  window.console.error = (...args) => errors.push(args.map(String).join(' '));

  for (const asset of ['field_visibility.js', 'ui.js', 'basic_guided.js',
                       'pro_guided_workflow.js', 'devhub_upload.js']) {
    window.eval(await readAsset(`assets/${asset}`));
  }
  document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
  window.setToken('t');
  window.enterAuthenticatedApp();
  await waitFor(window, () => document.querySelector('.basic-guided-setup-card'), 'basic setup');
  await tick(window, 200);

  const select = document.getElementById('ap_adapter');

  // --- Basic mode: USB filtering, identity across identical refreshes ------
  assert.equal(select.options.length, 1, 'Basic mode lists USB adapters only');
  assert.equal(select.options[0].textContent, 'USB Wi-Fi 1 (Recommended)');
  assert.equal(select.value, 'wlan1');

  const basicNodes = Array.from(select.options);
  const dirtyBefore = document.getElementById('dirty')?.textContent || '';
  const basicWatch = observeSelect(window, select);
  for (let i = 0; i < 5; i += 1) {
    await window.loadAdapters();
    await tick(window, 60);
  }
  assert.equal(basicWatch.counts.childList, 0,
    'identical inventory must not add or remove option nodes');
  assert.ok(
    Array.from(select.options).every((option, index) => option === basicNodes[index]),
    'option nodes must retain strict identity across identical refreshes',
  );
  assert.equal(select.options[0].textContent, 'USB Wi-Fi 1 (Recommended)',
    'visible label must not change');
  assert.equal(select.value, 'wlan1', 'selection must be preserved');
  assert.equal(document.getElementById('dirty')?.textContent || '', dirtyBefore,
    'a background inventory refresh must not mark the configuration dirty');
  basicWatch.observer.disconnect();

  // --- Pro mode: decorated options stay stable, no decorator loop ----------
  toggleMode(window, true);
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', 'pro ready');
  await waitFor(window, () => select.options[0]?.textContent === 'USB Wi-Fi 1 (Recommended)', 'friendly labels');
  await tick(window, 300);
  assert.equal(select.options.length, 2, 'Pro mode lists every adapter');

  const proNodes = Array.from(select.options);
  const proWatch = observeSelect(window, select);
  for (let i = 0; i < 5; i += 1) {
    await window.loadAdapters();
    await tick(window, 60);
  }
  assert.equal(proWatch.counts.childList, 0,
    'decorated options must survive identical refreshes without rebuilds');
  assert.ok(
    Array.from(select.options).every((option, index) => option === proNodes[index]),
    'decorated option nodes must retain strict identity',
  );
  assert.equal(select.options[0].textContent, 'USB Wi-Fi 1 (Recommended)');
  assert.equal(select.value, 'wlan1');

  // No second mutation loop between ui.js and the Pro decorators: with no
  // stimulus at all, the select subtree must stay completely quiet.
  proWatch.counts.childList = 0;
  proWatch.counts.attrs = 0;
  await tick(window, 700);
  assert.equal(proWatch.counts.childList + proWatch.counts.attrs, 0,
    'adapter select must be mutation-quiet at idle');
  proWatch.observer.disconnect();

  // --- Genuine inventory change updates exactly once ------------------------
  const changeWatch = observeSelect(window, select);
  inventoryRef.value.adapters.push({
    ifname: 'wlan2', name: 'Second USB Wi-Fi adapter', bus: 'usb',
    phy: 'phy3', recommended: false, score: 60, supports_ap: true,
    supports_2ghz: true, supports_5ghz: true, supports_6ghz: false,
    regdom: { country: 'US' }, reasons: [],
  });
  await window.loadAdapters();
  await tick(window, 120);
  assert.equal(select.options.length, 3, 'a real inventory change must be applied');
  const mutationsAfterChange = changeWatch.counts.childList;
  assert.ok(mutationsAfterChange > 0, 'a real inventory change must rebuild');
  await window.loadAdapters();
  await tick(window, 120);
  assert.equal(changeWatch.counts.childList, mutationsAfterChange,
    'a repeated identical inventory must not rebuild again');
  changeWatch.observer.disconnect();

  // --- Removed selected adapter falls back to the configured adapter -------
  select.value = 'wlan2';
  inventoryRef.value.adapters = inventoryRef.value.adapters.filter(
    (adapter) => adapter.ifname !== 'wlan2',
  );
  await window.loadAdapters();
  await tick(window, 120);
  assert.equal(select.value, 'wlan1',
    'a removed selected adapter must fall back to the configured adapter');

  // --- Basic -> Pro -> Basic retains filtering and friendly names -----------
  toggleMode(window, false);
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'waiting-for-pro', 'basic restored');
  await tick(window, 200);
  assert.equal(select.options.length, 1, 'Basic filtering must return');
  assert.equal(select.options[0].textContent, 'USB Wi-Fi 1 (Recommended)');
  toggleMode(window, true);
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', 'pro again');
  await waitFor(window, () => select.options[0]?.textContent === 'USB Wi-Fi 1 (Recommended)', 'friendly labels again');
  assert.equal(select.options.length, 2);

  assert.deepEqual(errors, []);
  dom.window.close();
});
