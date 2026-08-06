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
const CREDENTIALS = '/v1/config/hotspot-credentials';

const USB_SERIAL = '2G0YC1ZF8G0T1B';
const SSID = 'VR-Hotspot';
const PASSWORD = 'correct-horse-battery-staple';
const MASK = '••••••••••';

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
      message: `Select ${SSID} in the headset Wi-Fi settings.`,
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

const credentialsOk = (secret = PASSWORD) => ({
  ok: true,
  status: 200,
  json: {
    correlation_id: 'test-cid',
    result_code: 'ok',
    warnings: [],
    data: { ssid: SSID, wpa2_passphrase: secret },
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
  <label><input type="checkbox" id="privacyModeBasic"> Privacy Mode</label>
  <label><input type="checkbox" id="privacyMode"> Privacy Mode</label>
  <input type="password" id="wpa2_passphrase">
</body></html>`;
}

const tick = (window, ms = 25) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function settle(window, times = 4) {
  for (let i = 0; i < times; i += 1) await tick(window);
}

async function makeWizardConsole(routes, { privacy = false } = {}) {
  const dom = new JSDOM(fixture(), {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const calls = [];
  const copied = [];
  window.api = async (path) => {
    const route = String(path).split('?')[0];
    calls.push(route);
    const handler = routes[route];
    if (!handler) throw new Error(`Unexpected Developer Hub request: ${route}`);
    return handler();
  };
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (value) => { copied.push(value); } },
  });
  window.document.getElementById('privacyMode').checked = privacy;
  window.document.getElementById('privacyModeBasic').checked = privacy;
  window.localStorage.setItem('vr_hotspot_privacy', privacy ? '1' : '0');

  window.eval(await readAsset('assets/devhub.js'));
  assert.ok(window.document.getElementById('devhubRefresh'), 'Developer Hub pane injected');
  window.document.getElementById('tab-devhub').classList.add('active');
  window.eval(await readAsset('assets/devhub_workspace.js'));
  window.eval(await readAsset('assets/devhub_connection_wizard.js'));
  assert.equal(
    window.document.documentElement.dataset.devhubVrhotspotOnlyReady,
    '1',
    'VRhotspot-only wizard layer injected',
  );
  return { window, document: window.document, calls, copied };
}

function baseRoutes() {
  return {
    [STATUS]: runningStatus,
    [TOOLS]: systemTools,
    [VERSION]: versionOk,
    [DEVICES]: () => devicesOk([usbQuest()]),
    [NETWORK]: networkOk,
    [WIRELESS]: manualJoinRequired,
    [CREDENTIALS]: () => credentialsOk(),
  };
}

const credCalls = (calls) => calls.filter((route) => route === CREDENTIALS).length;
const wirelessCalls = (calls) => calls.filter((route) => route === WIRELESS).length;

async function openManualJoinWizard(routes, options = {}) {
  const console = await makeWizardConsole(routes, options);
  const { window, document } = console;
  document.getElementById('devhubWirelessSetup').click();
  await settle(window, 6);
  assert.equal(document.getElementById('devhubWirelessWizard').hidden, false, 'wizard opened');
  document.getElementById('devhubWizardAction').click();
  await settle(window, 6);
  assert.equal(document.getElementById('devhubManualJoin').hidden, false, 'manual join shown');
  return console;
}

const domIncludesSecret = (document, secret = PASSWORD) =>
  document.documentElement.outerHTML.includes(secret);

test('privacy off renders credentials with working reveal, hide, and copy controls', async () => {
  const { window, document, calls, copied } = await openManualJoinWizard(baseRoutes());

  assert.equal(credCalls(calls), 1, 'credentials endpoint requested once');
  assert.equal(document.getElementById('devhubManualCredSsid').textContent, SSID);
  const secret = document.getElementById('devhubManualCredSecret');
  const toggle = document.getElementById('devhubManualCredToggle');
  assert.equal(secret.textContent, PASSWORD, 'password rendered when Privacy Mode is off');
  assert.equal(toggle.textContent, 'Hide');
  assert.equal(
    document.getElementById('devhubManualCredPrivacy').hidden,
    true,
    'privacy explanation absent while Privacy Mode is off',
  );

  toggle.click();
  await settle(window, 2);
  assert.equal(secret.textContent, MASK, 'hide masks the password');
  assert.ok(!domIncludesSecret(document), 'hidden password leaves the DOM');

  toggle.click();
  await settle(window, 2);
  assert.equal(secret.textContent, PASSWORD, 'reveal shows the password again');
  assert.equal(credCalls(calls), 1, 'cached reveal does not re-request the secret');

  assert.deepEqual(copied, [], 'nothing copied automatically');
  document.getElementById('devhubManualCredCopy').click();
  await settle(window, 2);
  assert.deepEqual(copied, [PASSWORD], 'copy button copies the password');

  await tick(window, 5000);
  await settle(window, 2);
  assert.ok(wirelessCalls(calls) >= 2, 'manual-join polling kept running');
  assert.equal(credCalls(calls), 1, 'background polling never re-requests the secret');
});

test('privacy on never requests the password and explains why it is hidden', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes(), { privacy: true });

  await tick(window, 2600);
  await settle(window, 2);
  assert.equal(credCalls(calls), 0, 'credentials endpoint never requested');
  assert.ok(!domIncludesSecret(document), 'password absent from the DOM');
  assert.equal(document.getElementById('devhubManualCredSecretRow').hidden, true);
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, '');

  const privacyBlock = document.getElementById('devhubManualCredPrivacy');
  assert.equal(privacyBlock.hidden, false, 'privacy explanation shown');
  assert.ok(privacyBlock.textContent.includes('Password hidden by Privacy Mode.'));
  assert.ok(privacyBlock.textContent.includes('screen sharing, streaming, or recording'));
  assert.equal(document.getElementById('devhubManualCredSsid').textContent, SSID, 'SSID stays visible');
  assert.equal(document.getElementById('devhubManualCredShowOnce').hidden, false);
  assert.equal(document.getElementById('devhubManualCredPrivacyOff').hidden, false);
  assert.equal(document.getElementById('devhubManualCredKeepHidden').hidden, false);
});

test('show password once confirms, fetches a single time, and keeps Privacy Mode on', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes(), { privacy: true });

  document.getElementById('devhubManualCredShowOnce').click();
  await settle(window, 2);
  assert.equal(document.getElementById('devhubManualCredConfirm').hidden, false, 'confirmation shown');
  assert.equal(credCalls(calls), 0, 'no fetch before confirmation');

  document.getElementById('devhubManualCredConfirmShow').click();
  await settle(window, 4);
  assert.equal(credCalls(calls), 1, 'password fetched once after confirmation');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, PASSWORD);
  assert.equal(document.getElementById('privacyMode').checked, true, 'Pro control stays on');
  assert.equal(document.getElementById('privacyModeBasic').checked, true, 'Basic control stays on');
  assert.notEqual(window.localStorage.getItem('vr_hotspot_privacy'), '0', 'global setting unchanged');

  document.getElementById('devhubManualCredToggle').click();
  await settle(window, 2);
  assert.ok(!domIncludesSecret(document), 'hiding a session reveal purges the password');
  assert.equal(document.getElementById('devhubManualCredPrivacy').hidden, false);
});

test('turn off Privacy Mode synchronizes both controls and reveals the password', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes(), { privacy: true });

  document.getElementById('devhubManualCredPrivacyOff').click();
  await settle(window, 4);

  assert.equal(document.getElementById('privacyMode').checked, false, 'Pro control off');
  assert.equal(document.getElementById('privacyModeBasic').checked, false, 'Basic control off');
  assert.equal(window.localStorage.getItem('vr_hotspot_privacy'), '0', 'global setting updated');
  assert.equal(credCalls(calls), 1);
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, PASSWORD);
});

test('turning Privacy Mode back on immediately purges the visible password', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes());
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, PASSWORD);

  const privacyBox = document.getElementById('privacyMode');
  privacyBox.checked = true;
  privacyBox.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle(window, 2);

  assert.ok(!domIncludesSecret(document), 'password removed from the DOM');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, '');
  assert.equal(document.getElementById('devhubManualCredPrivacy').hidden, false);

  privacyBox.checked = false;
  privacyBox.dispatchEvent(new window.Event('change', { bubbles: true }));
  await settle(window, 4);
  assert.equal(credCalls(calls), 2, 'purged secret must be re-fetched, proving state was cleared');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, PASSWORD);
});

test('closing the wizard with Escape purges the secret; reopening re-fetches it', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes());
  assert.equal(credCalls(calls), 1);

  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await settle(window, 2);
  assert.equal(document.getElementById('devhubWirelessWizard').hidden, true, 'wizard closed');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, '');
  assert.ok(!domIncludesSecret(document), 'secret purged on Escape');

  document.getElementById('devhubWirelessSetup').click();
  await settle(window, 6);
  document.getElementById('devhubWizardAction').click();
  await settle(window, 6);
  assert.equal(credCalls(calls), 2, 'reopened wizard fetches fresh credentials');
});

test('cancel button purges the secret from state and DOM', async () => {
  const { window, document } = await openManualJoinWizard(baseRoutes());

  document.getElementById('devhubWizardCancel').click();
  await settle(window, 2);
  assert.equal(document.getElementById('devhubWirelessWizard').hidden, true);
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, '');
  assert.ok(!domIncludesSecret(document), 'secret purged on cancel');
});

test('keep hidden leaves the password unfetched and collapses the actions', async () => {
  const { window, document, calls } = await openManualJoinWizard(baseRoutes(), { privacy: true });

  document.getElementById('devhubManualCredKeepHidden').click();
  await settle(window, 2);

  assert.equal(credCalls(calls), 0, 'password never fetched');
  assert.equal(document.getElementById('devhubManualCredPrivacyActions').hidden, true);
  assert.equal(document.getElementById('devhubManualCredPrivacy').hidden, false, 'notice remains');
  assert.ok(!domIncludesSecret(document));
});

test('editing the saved hotspot password purges the stale value until re-revealed', async () => {
  const routes = baseRoutes();
  const { window, document, calls } = await openManualJoinWizard(routes);
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, PASSWORD);

  const rotated = 'rotated-hotspot-secret';
  routes[CREDENTIALS] = () => credentialsOk(rotated);
  const passInput = document.getElementById('wpa2_passphrase');
  passInput.value = rotated;
  passInput.dispatchEvent(new window.Event('input', { bubbles: true }));
  await settle(window, 2);

  assert.ok(!domIncludesSecret(document), 'old password no longer displayed');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, MASK);
  await tick(window, 2600);
  await settle(window, 2);
  assert.equal(credCalls(calls), 1, 'no automatic re-fetch after a saved-password change');

  document.getElementById('devhubManualCredToggle').click();
  await settle(window, 4);
  assert.equal(credCalls(calls), 2, 'explicit reveal fetches the fresh secret');
  assert.equal(document.getElementById('devhubManualCredSecret').textContent, rotated);
});
