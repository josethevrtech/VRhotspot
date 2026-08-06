import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const ROOT = new URL('../../', import.meta.url);
const readAsset = (path) => readFile(new URL(path, ROOT), 'utf8');

const STATUS = '/v1/status';
const TOOLS = '/v1/devbridge/tools/status';
const VERSION = '/v1/devbridge/adb/version';
const DEVICES = '/v1/devbridge/adb/devices';
const NETWORK = '/v1/devbridge/devices';
const WIRELESS = '/v1/devbridge/adb/enable-wireless';

const USB_SERIAL = '2G0YC1ZF8G0T1B';
const SSID = 'VR-Hotspot';

const runningStatus = () => ({
  ok: true,
  status: 200,
  json: { data: { running: true, phase: 'running' } },
  raw: '',
});

const systemTools = () => ({
  ok: true,
  status: 200,
  json: {
    data: {
      adb: {
        source: 'system',
        path: '/usr/bin/adb',
        managed: { present: false, installed: false, verified: null },
        system: { present: true, path: '/usr/bin/adb' },
      },
    },
  },
  raw: '',
});

const versionOk = () => ({
  ok: true,
  status: 200,
  json: { data: { stdout: 'Android Debug Bridge version 1.0.41' } },
  raw: '',
});

const devicesOk = (devices) => ({
  ok: true,
  status: 200,
  json: { data: { result_code: 'ok', data: { devices } } },
  raw: '',
});

const usbQuest = () => ({
  serial: USB_SERIAL,
  state: 'device',
  properties: { model: 'Quest_3S' },
});

const networkOk = () => ({
  ok: true,
  status: 200,
  json: { data: { devices: [] } },
  raw: '',
});

// The exact response observed on real Quest 3S hardware: Horizon OS exposes
// no `cmd wifi connect-network`, so the daemon refuses to enable wireless
// ADB and asks for a manual VRhotspot join. The Wi-Fi secret never appears.
const manualJoinRequired = () => ({
  ok: false,
  status: 500,
  json: {
    correlation_id: 'test-cid',
    result_code: 'wifi_control_unavailable',
    data: {
      schema_version: 2,
      operation: 'enable_wireless',
      success: false,
      result_code: 'wifi_control_unavailable',
      stage: 'join_vrhotspot',
      message:
        `Select ${SSID} in the headset Wi-Fi settings. Developer Hub will `
        + 'continue automatically after the headset joins the dedicated '
        + 'VRhotspot network.',
      returncode: 1,
      stdout: '',
      stderr: '',
      data: {
        usb_serial: USB_SERIAL,
        port: 5555,
        ssid: SSID,
        security: 'wpa2',
        ap_interface: 'wlan0',
        gateway: '192.168.68.1',
        subnet: '192.168.68.0/24',
        model: 'Quest 3S',
        requires_manual_join: true,
      },
    },
  },
  raw: '',
});

const wirelessSuccess = () => ({
  ok: true,
  status: 200,
  json: {
    correlation_id: 'test-cid',
    result_code: 'ok',
    data: {
      schema_version: 2,
      operation: 'enable_wireless',
      success: true,
      result_code: 'ok',
      stage: 'complete',
      message: 'Wireless ADB is enabled through VRhotspot.',
      returncode: 0,
      stdout: '',
      stderr: '',
      data: {
        usb_serial: USB_SERIAL,
        port: 5555,
        ssid: SSID,
        security: 'wpa2',
        ap_interface: 'wlan0',
        gateway: '192.168.68.1',
        subnet: '192.168.68.0/24',
        model: 'Quest 3S',
      },
    },
  },
  raw: '',
});

function fixture() {
  return `<!doctype html>
<html><head></head><body>
  <nav><ul class="nav-list">
    <li class="nav-item" data-tab="overview">Overview</li>
    <li class="nav-item" data-tab="logs">Logs</li>
  </ul></nav>
  <main class="content-area"><div id="tab-logs" class="tab-pane"></div></main>
</body></html>`;
}

const tick = (window, ms = 25) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function settle(window, times = 4) {
  for (let i = 0; i < times; i += 1) await tick(window);
}

async function makeWizardConsole(routes) {
  const dom = new JSDOM(fixture(), {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const calls = [];
  window.api = async (path) => {
    const route = String(path).split('?')[0];
    calls.push(route);
    const handler = routes[route];
    if (!handler) throw new Error(`Unexpected Developer Hub request: ${route}`);
    return handler();
  };

  window.eval(await readAsset('assets/devhub.js'));
  assert.ok(window.document.getElementById('devhubRefresh'), 'Developer Hub pane injected');
  window.document.getElementById('tab-devhub').classList.add('active');
  window.eval(await readAsset('assets/devhub_workspace.js'));
  assert.ok(window.document.getElementById('devhubWorkspace'), 'workspace injected');
  window.eval(await readAsset('assets/devhub_connection_wizard.js'));
  assert.equal(
    window.document.documentElement.dataset.devhubVrhotspotOnlyReady,
    '1',
    'VRhotspot-only wizard layer injected',
  );
  return { window, document: window.document, calls };
}

function baseRoutes() {
  return {
    [STATUS]: runningStatus,
    [TOOLS]: systemTools,
    [VERSION]: versionOk,
    [DEVICES]: () => devicesOk([usbQuest()]),
    [NETWORK]: networkOk,
    [WIRELESS]: manualJoinRequired,
  };
}

function currentStep(document) {
  const current = document.querySelectorAll('#devhubWirelessWizard .devhub-wizard-step.current');
  assert.equal(current.length, 1, 'exactly one wizard step is current');
  return Number(current[0].dataset.step);
}

const wirelessCalls = (calls) => calls.filter((route) => route === WIRELESS).length;

async function openManualJoinWizard(routes) {
  const console = await makeWizardConsole(routes);
  const { window, document } = console;
  document.getElementById('devhubWirelessSetup').click();
  await settle(window, 6);
  assert.equal(document.getElementById('devhubWirelessWizard').hidden, false, 'wizard opened');
  const action = document.getElementById('devhubWizardAction');
  assert.equal(action.disabled, false, 'action enabled for an authorized USB headset');
  action.click();
  await settle(window, 6);
  return console;
}

test('wifi_control_unavailable renders the Step 3 manual-join waiting state', async () => {
  const { document } = await openManualJoinWizard(baseRoutes());

  assert.equal(currentStep(document), 3, 'wizard stays on Step 3: Join VRhotspot');
  assert.equal(
    document.getElementById('devhubWizardTitle').textContent,
    `Put on your headset and join ${SSID}`,
  );
  const detail = document.getElementById('devhubWizardDetail').textContent;
  assert.ok(detail.includes('Horizon OS does not permit automatic Wi-Fi enrollment'), detail);
  assert.ok(detail.includes(`Settings → Wi-Fi and select ${SSID}`), detail);
  assert.ok(detail.includes('Keep the USB cable connected'), detail);

  const panel = document.getElementById('devhubManualJoin');
  assert.equal(panel.hidden, false, 'manual-join panel is visible');
  assert.ok(panel.querySelector('.devhub-manual-join-spinner'), 'spinner is rendered');
  assert.equal(document.getElementById('devhubManualJoinSsid').textContent, SSID);
  assert.equal(
    document.getElementById('devhubManualJoinStatus').textContent,
    `Waiting for Quest 3S to join ${SSID}…`,
  );

  const action = document.getElementById('devhubWizardAction');
  assert.equal(action.textContent, 'I’ve joined — check again');
  assert.equal(action.disabled, false, 'manual recheck stays available');

  const feedback = document.getElementById('devhubFeedback');
  assert.equal(feedback.dataset.state, 'loading', 'waiting state, not an error banner');
  assert.ok(feedback.textContent.includes(SSID), feedback.textContent);
});

test('automatic polling repeats without overlap and without repeated error banners', async () => {
  const routes = baseRoutes();
  let inFlight = 0;
  let maxInFlight = 0;
  routes[WIRELESS] = async () => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 150));
    inFlight -= 1;
    return manualJoinRequired();
  };
  const { window, document, calls } = await openManualJoinWizard(routes);

  const before = wirelessCalls(calls);
  await tick(window, 5200);
  await settle(window, 4);
  const after = wirelessCalls(calls);

  assert.ok(after >= before + 2, `automatic polling kept rechecking (${before} -> ${after})`);
  assert.equal(maxInFlight, 1, 'wireless bootstrap requests never overlap');
  assert.equal(currentStep(document), 3, 'still on Step 3 while on the wrong network');
  const feedback = document.getElementById('devhubFeedback');
  assert.notEqual(feedback.dataset.state, 'error', feedback.textContent);
  assert.ok(!feedback.textContent.includes('wifi_control_unavailable'), feedback.textContent);
});

test('manual recheck button issues an immediate request and stays on Step 3', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes());

  const before = wirelessCalls(calls);
  document.getElementById('devhubWizardAction').click();
  await settle(window, 6);

  assert.ok(wirelessCalls(calls) >= before + 1, 'recheck triggered a wireless bootstrap call');
  assert.equal(currentStep(document), 3);
  assert.equal(document.getElementById('devhubManualJoin').hidden, false);
  assert.notEqual(document.getElementById('devhubFeedback').dataset.state, 'error');
});

test('joining VRhotspot advances visibly to Step 4 and then Step 5', async () => {
  const routes = baseRoutes();
  const { window, document, calls } = await openManualJoinWizard(routes);

  routes[WIRELESS] = wirelessSuccess;
  document.getElementById('devhubWizardAction').click();
  await settle(window, 6);

  assert.equal(currentStep(document), 4, 'Step 4: Enable wireless is shown first');
  assert.equal(document.getElementById('devhubManualJoin').hidden, true);
  assert.ok(
    document.getElementById('devhubWizardTitle').textContent.includes(`joined ${SSID}`),
    document.getElementById('devhubWizardTitle').textContent,
  );
  assert.equal(document.getElementById('devhubWizardAction').disabled, true);

  await tick(window, 1100);
  assert.equal(currentStep(document), 5, 'Step 5: Complete follows');
  assert.equal(
    document.getElementById('devhubWizardTitle').textContent,
    'Quest 3S is connected through VRhotspot',
  );
  assert.equal(document.getElementById('devhubFeedback').dataset.state, 'success');
  assert.equal(document.getElementById('devhubWizardAction').textContent, 'Done');

  const settled = wirelessCalls(calls);
  await tick(window, 2600);
  assert.equal(wirelessCalls(calls), settled, 'join polling stopped after success');
});

test('USB disconnect during manual join stops polling and asks to reconnect', async () => {
  const routes = baseRoutes();
  const { window, document, calls } = await openManualJoinWizard(routes);

  routes[DEVICES] = () => devicesOk([]);
  await tick(window, 1800);
  await settle(window, 4);

  assert.equal(currentStep(document), 3);
  assert.equal(
    document.getElementById('devhubWizardTitle').textContent,
    'USB cable disconnected',
  );
  const detail = document.getElementById('devhubWizardDetail').textContent;
  assert.ok(detail.includes('Reconnect the USB cable'), detail);
  assert.equal(document.getElementById('devhubManualJoin').hidden, true);
  assert.equal(document.getElementById('devhubWizardAction').disabled, true);

  const paused = wirelessCalls(calls);
  await tick(window, 2600);
  assert.equal(wirelessCalls(calls), paused, 'no wireless bootstrap requests while USB is gone');

  // Reconnecting USB resumes the manual-join waiting state and the polling.
  routes[DEVICES] = () => devicesOk([usbQuest()]);
  await tick(window, 1800);
  await settle(window, 4);
  assert.equal(document.getElementById('devhubManualJoin').hidden, false);
  assert.equal(
    document.getElementById('devhubWizardTitle').textContent,
    `Put on your headset and join ${SSID}`,
  );
  await tick(window, 2600);
  assert.ok(wirelessCalls(calls) > paused, 'polling resumed after USB reconnect');
});

test('cancelling the wizard stops every polling timer', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes());

  document.getElementById('devhubWizardCancel').click();
  await settle(window, 2);
  assert.equal(document.getElementById('devhubWirelessWizard').hidden, true);

  const wirelessBefore = wirelessCalls(calls);
  const devicesBefore = calls.filter((route) => route === DEVICES).length;
  await tick(window, 3000);
  assert.equal(wirelessCalls(calls), wirelessBefore, 'wireless polling stopped on cancel');
  assert.equal(
    calls.filter((route) => route === DEVICES).length,
    devicesBefore,
    'USB polling stopped on cancel',
  );
});
