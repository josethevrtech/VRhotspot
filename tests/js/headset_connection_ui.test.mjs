import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

// Lifecycle presentation regression suite for the canonical hotspot state
// chain: /v1/status phase -> ui.js setPill (data-hotspot-state) -> Basic
// guided card + Pro service card, including optimistic transient states
// rendered before lifecycle POSTs resolve.

const ROOT = new URL('../../', import.meta.url);
const readAsset = (path) => readFile(new URL(path, ROOT), 'utf8');

function apiPayload(path, method, stub) {
  if (path === '/v1/frame-direct') return {data: stub.direct || {active: false, paired: true, adapters: ['wlan1']}};
  if (path === '/v1/frame-direct/disconnect') {
    stub.direct = {...stub.direct, active: false, connected: false};
    return {result_code: 'ok', data: stub.direct};
  }
  if (path === '/v1/frame-direct/connect') {
    stub.direct = {...stub.direct, active: true, connected: true, paired: true, adapters: ['wlan1'], adapter: 'wlan1', width_mhz: 160};
    return {result_code: 'ok', data: stub.direct};
  }
  if (path === '/v1/status') {
    return {
      result_code: 'ok',
      data: {
        running: stub.status.running,
        phase: stub.status.phase,
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
  if (path === '/v1/start') return { result_code: 'started', data: {} };
  if (path === '/v1/stop') return { result_code: 'stopped', data: {} };
  if (path === '/v1/restart') return { result_code: 'restarted', data: {} };
  if (path === '/v1/repair') return { result_code: 'repaired', data: {} };
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

function installBrowserStubs(window, stub) {
  const Observer = window.MutationObserver;
  window.reviewObservers = [];
  window.MutationObserver = class extends Observer {
    constructor(callback) { super(callback); window.reviewObservers.push(this); }
  };
  window.fetch = async (url, init) => {
    const method = (init && init.method) || 'GET';
    const path = new URL(String(url), 'http://127.0.0.1:8732').pathname;
    (stub.requests ||= []).push({path, method});
    const gate = stub.gates.get(`${method} ${path}`);
    if (gate) await gate;
    const payload = apiPayload(path, method, stub);
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
    configurable: true,
    value: { writeText: async () => {} },
  });
}

function tick(window, ms = 40) {
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

function gateRequest(stub, key) {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  stub.gates.set(key, promise);
  return () => {
    stub.gates.delete(key);
    release();
  };
}

function basicView(document) {
  const card = document.querySelector('.basic-guided-setup-card');
  const button = document.getElementById('btnStartBasic');
  return {
    state: card?.dataset.hotspotState || '',
    label: document.getElementById('basicGuidedStateText')?.textContent || '',
    heading: document.getElementById('basicGuidedActionSlotTitle')?.textContent || '',
    summary: document.getElementById('basicGuidedStatusSummary')?.textContent || '',
    button: button?.textContent || '',
    buttonDisabled: !!button?.disabled,
  };
}

function proView(document) {
  const card = document.querySelector('.pro-service-card');
  const button = document.getElementById('btnStart');
  return {
    state: card?.dataset.hotspotState || '',
    label: document.getElementById('proServiceStateText')?.textContent || '',
    button: button?.textContent || '',
    buttonDisabled: !!button?.disabled,
  };
}

async function bootPortal(stub) {
  const [html, fieldVisibility, ui, basicGuided, connection, proGuided] = await Promise.all([
    readAsset('assets/index.html'),
    readAsset('assets/field_visibility.js'),
    readAsset('assets/ui.js'),
    readAsset('assets/basic_guided.js'),
    readAsset('assets/frame_direct.js'),
    readAsset('assets/pro_guided_workflow.js'),
  ]);
  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const { document } = window;
  installBrowserStubs(window, stub);
  window.localStorage.setItem('vrhs_ui_mode', 'basic');

  const errors = [];
  window.console.error = (...args) => errors.push(args.map(String).join(' '));

  window.eval(fieldVisibility);
  window.eval(ui);
  window.eval(connection);
  window.eval(basicGuided);
  window.eval(proGuided);
  document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

  window.setToken('lifecycle-test-token');
  window.enterAuthenticatedApp();

  await waitFor(window, () => document.querySelector('.pro-service-card'), 'Pro service card');
  await waitFor(window, () => document.querySelector('.basic-guided-setup-card'), 'Basic guided card');
  await waitFor(
    window,
    () => document.querySelector('.basic-guided-setup-card')?.dataset.hotspotState === 'stopped',
    'initial authoritative Stopped state',
  );
  return { dom, window, document, errors };
}

async function publishStatus(window, stub, status) {
  stub.status = status;
  await window.refresh();
  await tick(window, 30);
}


test('paired headset reuses setup and existing Basic/Pro controls without a separate panel', async () => {
  const stub = {status: {running: false, phase: 'stopped'}, gates: new Map()};
  const {dom, window, document} = await bootPortal(stub);
  window.stopActivePolling();
  stub.direct = {active: true, connected: true, paired: true, adapters: ['wlan1'], adapter: 'wlan1', width_mhz: 160};
  await window.refresh(); await tick(window);
  assert.equal(document.getElementById('frameDirectCard'), null);
  assert.equal(basicView(document).label, 'Connected');
  assert.equal(basicView(document).button, 'Disconnect');
  assert.equal(proView(document).label, 'Connected');
  assert.equal(proView(document).button, 'Disconnect');
  assert.equal(document.getElementById('connectionPurpose').value, 'headset');
  assert.equal(document.getElementById('connectionPurpose').disabled, true);
  assert.equal(document.getElementById('headsetPairing').open, false);
  assert.equal(document.getElementById('headsetPairing').hidden, true);
  for (const id of ['basicGuidedProfileSlot', 'basicGuidedSsidSlot', 'basicGuidedPassSlot']) {
    assert.equal(document.getElementById(id).closest('.basic-guided-step').hidden, true);
  }
  const toggle = document.getElementById('uiModeToggle');
  toggle.checked = true; toggle.dispatchEvent(new window.Event('change', {bubbles: true}));
  await waitFor(window, () => document.body.dataset.proGuidedStage === 'ready', 'Pro connection setup');
  assert.equal(document.querySelector('.pro-guided-header-copy h2').textContent, 'Set Up Connection');
  assert.equal(document.getElementById('headsetPairing').hidden, false);
  document.getElementById('headsetPairing').open = true;
  for (const id of ['proStepPerformance', 'proStepHotspot', 'proStepAdvanced']) {
    assert.equal(document.getElementById(id).closest('.pro-guided-step').hidden, true);
  }
  toggle.checked = false; toggle.dispatchEvent(new window.Event('change', {bubbles: true}));
  await tick(window, 80);
  assert.equal(document.getElementById('headsetPairing').hidden, true);
  assert.equal(document.getElementById('headsetPairing').open, false);
  // Existing single action is the real control, not a second set of buttons.
  document.getElementById('btnStartBasic').click();
  await waitFor(window, () => basicView(document).button === 'Connect', 'disconnected action');
  assert.ok(stub.requests.some(r => r.path === '/v1/frame-direct/disconnect'));
  assert.ok(!stub.requests.some(r => r.path === '/v1/stop'));
  await tick(window, 80);
  document.getElementById('btnStartBasic').click();
  await waitFor(window, () => basicView(document).button === 'Disconnect', 'connected action');
  assert.ok(stub.requests.some(r => r.path === '/v1/frame-direct/connect'));
  assert.ok(!stub.requests.some(r => r.path === '/v1/start'));
  for (const observer of window.reviewObservers) observer.disconnect();
  dom.window.close();
});

test('disconnected user can select ordinary sharing and recover its existing setup', async () => {
  const stub = {status: {running: false, phase: 'stopped'}, gates: new Map()};
  const {dom, window, document} = await bootPortal(stub);
  window.stopActivePolling();
  const selector = document.getElementById('connectionPurpose');
  selector.value = 'headset'; selector.dispatchEvent(new window.Event('change'));
  await tick(window, 100);
  selector.value = 'hotspot'; selector.dispatchEvent(new window.Event('change'));
  await tick(window, 100);
  assert.equal(basicView(document).button, 'Start hotspot');
  assert.equal(proView(document).button, 'Start Hotspot');
  assert.equal(document.getElementById('basicGuidedSsidSlot').closest('.basic-guided-step').hidden, false);
  assert.equal(document.getElementById('headsetPairing').hidden, true);
  assert.ok(!stub.requests.some(r => r.method === 'POST' && r.path.startsWith('/v1/frame-direct/')));
  for (const observer of window.reviewObservers) observer.disconnect();
  dom.window.close();
});


test('unpaired Basic shows a setup state without opening technical fields or connecting', async () => {
  const stub = {status: {running: false, phase: 'stopped'}, gates: new Map()};
  const {dom, window, document} = await bootPortal(stub);
  window.stopActivePolling();
  stub.direct = {active: false, connected: false, paired: false, adapters: ['wlan1']};
  const selector = document.getElementById('connectionPurpose');
  selector.value = 'headset'; selector.dispatchEvent(new window.Event('change'));
  await tick(window, 100);
  assert.equal(document.getElementById('headsetPairing').hidden, true);
  assert.equal(basicView(document).label, 'Setup needed');
  assert.equal(basicView(document).buttonDisabled, true);
  await window.startHotspot();
  assert.equal(document.getElementById('headsetPairing').open, false);
  assert.ok(!stub.requests.some(r => r.method === 'POST' && r.path.startsWith('/v1/frame-direct/')));
  for (const observer of window.reviewObservers) observer.disconnect();
  dom.window.close();
});
