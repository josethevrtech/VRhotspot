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
  window.fetch = async (url, init) => {
    const method = (init && init.method) || 'GET';
    const path = new URL(String(url), 'http://127.0.0.1:8732').pathname;
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
  const [html, fieldVisibility, ui, basicGuided] = await Promise.all([
    readAsset('assets/index.html'),
    readAsset('assets/field_visibility.js'),
    readAsset('assets/ui.js'),
    readAsset('assets/basic_guided.js'),
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
  window.eval(basicGuided);
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

test('canonical lifecycle phases drive Basic and Pro without contradictory strings', async () => {
  const stub = { status: { running: false, phase: 'stopped' }, gates: new Map() };
  const { dom, window, document, errors } = await bootPortal(stub);
  // Stop timer-based polling so each publishStatus below is the only
  // authoritative writer and assertions are deterministic.
  window.stopActivePolling();

  const pillText = () => document.getElementById('pillTxt')?.textContent || '';
  const basicPillText = () => document.getElementById('basicPillTxt')?.textContent || '';

  // stopped
  assert.equal(basicView(document).state, 'stopped');
  assert.equal(basicView(document).label, 'Stopped');
  assert.equal(basicView(document).heading, 'Start hotspot');
  assert.equal(basicView(document).summary, 'The hotspot is stopped.');
  assert.equal(basicView(document).button, 'Start hotspot');
  assert.equal(proView(document).state, 'stopped');
  assert.equal(proView(document).label, 'Stopped');
  assert.equal(proView(document).button, 'Start Hotspot');

  // Regression 1: phase=starting, running=false -> Starting…, no "Stopped".
  await publishStatus(window, stub, { running: false, phase: 'starting' });
  assert.ok(pillText().startsWith('Starting…'), `pill was: ${pillText()}`);
  assert.ok(!pillText().includes('Stopped'), `pill must not contradict: ${pillText()}`);
  assert.ok(!basicPillText().includes('Stopped'), `basic pill must not contradict: ${basicPillText()}`);
  assert.deepEqual(basicView(document), {
    state: 'starting',
    label: 'Starting…',
    heading: 'Starting hotspot',
    summary: 'VRhotspot is applying the connection settings.',
    button: 'Starting…',
    buttonDisabled: true,
  });
  assert.equal(proView(document).state, 'starting');
  assert.equal(proView(document).label, 'Starting…');
  assert.equal(proView(document).button, 'Starting…');
  assert.equal(proView(document).buttonDisabled, true);

  // Regression 2: phase=stopping -> Stopping…, no "Stopped"/"Running".
  await publishStatus(window, stub, { running: true, phase: 'stopping' });
  assert.ok(pillText().startsWith('Stopping…'), `pill was: ${pillText()}`);
  assert.ok(!pillText().includes('Stopped'), `pill must not contradict: ${pillText()}`);
  assert.ok(!pillText().includes('Running'), `pill must not contradict: ${pillText()}`);
  assert.deepEqual(basicView(document), {
    state: 'stopping',
    label: 'Stopping…',
    heading: 'Stopping hotspot',
    summary: 'VRhotspot is shutting down the hotspot safely.',
    button: 'Stopping…',
    buttonDisabled: true,
  });
  assert.equal(proView(document).state, 'stopping');
  assert.equal(proView(document).label, 'Stopping…');
  assert.equal(proView(document).button, 'Stopping…');
  assert.equal(proView(document).buttonDisabled, true);

  // Regression 3: running.
  await publishStatus(window, stub, { running: true, phase: 'running' });
  assert.ok(pillText().startsWith('Running'));
  assert.equal(basicView(document).state, 'running');
  assert.equal(basicView(document).label, 'Running');
  assert.equal(basicView(document).heading, 'Hotspot running');
  assert.equal(basicView(document).summary, 'Your hotspot is active and ready for your headset.');
  assert.equal(basicView(document).button, 'Stop hotspot');
  assert.equal(proView(document).state, 'running');
  assert.equal(proView(document).button, 'Stop Hotspot');

  // Regression 4: stopped again.
  await publishStatus(window, stub, { running: false, phase: 'stopped' });
  assert.equal(basicView(document).state, 'stopped');
  assert.equal(basicView(document).button, 'Start hotspot');

  // Regression 5: restarting and repairing transitional labels.
  await publishStatus(window, stub, { running: false, phase: 'restarting' });
  assert.ok(pillText().startsWith('Restarting…'));
  assert.equal(basicView(document).state, 'restarting');
  assert.equal(basicView(document).label, 'Restarting…');
  assert.equal(basicView(document).button, 'Restarting…');
  assert.equal(proView(document).label, 'Restarting…');
  await publishStatus(window, stub, { running: false, phase: 'repairing' });
  assert.ok(pillText().startsWith('Repairing…'));
  assert.equal(basicView(document).state, 'repairing');
  assert.equal(basicView(document).label, 'Repairing…');
  assert.equal(basicView(document).button, 'Repairing…');
  assert.equal(proView(document).label, 'Repairing…');

  // Regression 6: error state.
  await publishStatus(window, stub, { running: false, phase: 'error' });
  assert.ok(pillText().startsWith('Needs attention'));
  assert.equal(basicView(document).state, 'error');
  assert.equal(basicView(document).label, 'Needs attention');
  assert.equal(basicView(document).heading, 'Hotspot needs attention');
  assert.equal(basicView(document).button, 'View problem');
  assert.equal(proView(document).state, 'error');
  assert.equal(proView(document).label, 'Needs attention');
  assert.equal(document.getElementById('pill').classList.contains('err'), true);

  // Regression 12: the Wi-Fi glyph is present on every surface, decorative,
  // and keyed by the same data-hotspot-state attribute the text uses.
  for (const selector of [
    '.basic-guided-status-state svg.hotspot-wifi-icon',
    '.pro-service-state-row svg.hotspot-wifi-icon',
    '#pill svg.hotspot-wifi-icon',
    '#basicPill svg.hotspot-wifi-icon',
  ]) {
    const icon = document.querySelector(selector);
    assert.ok(icon, `${selector} must exist`);
    assert.equal(icon.getAttribute('aria-hidden'), 'true', `${selector} must be decorative`);
  }
  assert.equal(document.querySelector('#pill .dot'), null, 'legacy pill dot must be replaced');
  assert.equal(document.querySelector('#basicPill .dot'), null, 'legacy basic dot must be replaced');
  assert.equal(
    document.querySelector('.basic-guided-setup-card').dataset.hotspotState,
    'error',
    'icon styling attribute must match the lifecycle state',
  );

  assert.deepEqual(errors, []);
  dom.window.close();
});

test('lifecycle actions render optimistic states that authoritative refresh replaces', async () => {
  const stub = { status: { running: false, phase: 'stopped' }, gates: new Map() };
  const { dom, window, document, errors } = await bootPortal(stub);
  window.stopActivePolling();

  const pillText = () => document.getElementById('pillTxt')?.textContent || '';

  // Regression 9: Start click shows Starting… before the POST resolves.
  const releaseStart = gateRequest(stub, 'POST /v1/start');
  const startPromise = window.startHotspot();
  assert.ok(pillText().startsWith('Starting…'),
    `optimistic Starting… must render synchronously, saw: ${pillText()}`);
  await tick(window, 30);
  assert.equal(basicView(document).state, 'starting');
  assert.equal(basicView(document).button, 'Starting…');
  assert.equal(basicView(document).heading, 'Starting hotspot');
  assert.equal(proView(document).label, 'Starting…');
  // Regression 11: the authoritative refresh replaces the optimistic state.
  stub.status = { running: true, phase: 'running' };
  releaseStart();
  await startPromise;
  await tick(window, 30);
  assert.ok(pillText().startsWith('Running'));
  assert.equal(basicView(document).state, 'running');
  assert.equal(basicView(document).button, 'Stop hotspot');
  assert.equal(proView(document).state, 'running');

  // Regression 10: Stop click shows Stopping… before the POST resolves.
  const releaseStop = gateRequest(stub, 'POST /v1/stop');
  const stopPromise = window.stopHotspot();
  assert.ok(pillText().startsWith('Stopping…'),
    `optimistic Stopping… must render synchronously, saw: ${pillText()}`);
  await tick(window, 30);
  assert.equal(basicView(document).state, 'stopping');
  assert.equal(basicView(document).button, 'Stopping…');
  assert.equal(basicView(document).heading, 'Stopping hotspot');
  assert.equal(proView(document).label, 'Stopping…');
  stub.status = { running: false, phase: 'stopped' };
  releaseStop();
  await stopPromise;
  await tick(window, 30);
  assert.equal(basicView(document).state, 'stopped');
  assert.equal(basicView(document).button, 'Start hotspot');

  // Restart Service and Repair Network transient states.
  const releaseRestart = gateRequest(stub, 'POST /v1/restart');
  const restartPromise = window.restartHotspot();
  assert.ok(pillText().startsWith('Restarting…'));
  stub.status = { running: true, phase: 'running' };
  releaseRestart();
  await restartPromise;
  await tick(window, 30);
  assert.equal(basicView(document).state, 'running');

  const releaseRepair = gateRequest(stub, 'POST /v1/repair');
  const repairPromise = window.repairHotspot();
  assert.ok(pillText().startsWith('Repairing…'));
  stub.status = { running: false, phase: 'stopped' };
  releaseRepair();
  await repairPromise;
  await tick(window, 30);
  assert.equal(basicView(document).state, 'stopped');

  // An optimistic state must never override an authoritative error: the
  // next refresh after a failed action reports the backend truth.
  const releaseFailingStart = gateRequest(stub, 'POST /v1/start');
  stub.status = { running: false, phase: 'error' };
  const failingStart = window.startHotspot();
  assert.ok(pillText().startsWith('Starting…'));
  releaseFailingStart();
  await failingStart;
  await tick(window, 30);
  assert.equal(basicView(document).state, 'error');
  assert.equal(basicView(document).label, 'Needs attention');

  assert.deepEqual(errors, []);
  dom.window.close();
});
