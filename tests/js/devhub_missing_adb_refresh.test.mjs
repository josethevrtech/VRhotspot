import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const ROOT = new URL('../../', import.meta.url);
const readAsset = (path) => readFile(new URL(path, ROOT), 'utf8');

const TOOLS = '/v1/devbridge/tools/status';
const VERSION = '/v1/devbridge/adb/version';
const DEVICES = '/v1/devbridge/adb/devices';
const NETWORK = '/v1/devbridge/devices';

const missingTools = () => ({
  ok: true,
  status: 200,
  json: {
    data: {
      adb: {
        source: 'missing',
        path: null,
        managed: { present: false, installed: false, verified: null },
        system: { present: false, path: null },
      },
    },
  },
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

const toolsUnavailable = () => ({
  ok: false,
  status: 409,
  json: { data: { result_code: 'tools_unavailable' } },
  raw: '',
});

const versionOk = () => ({
  ok: true,
  status: 200,
  json: { data: { stdout: 'Android Debug Bridge version 1.0.41\nVersion 35.0.2' } },
  raw: '',
});

const devicesOk = (devices) => ({
  ok: true,
  status: 200,
  json: { data: { result_code: 'ok', data: { devices } } },
  raw: '',
});

const networkOk = () => ({
  ok: true,
  status: 200,
  json: { data: { devices: [] } },
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

async function makeConsole(routes, { workspace = false } = {}) {
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
  if (workspace) {
    window.document.getElementById('tab-devhub').classList.add('active');
    window.eval(await readAsset('assets/devhub_workspace.js'));
    assert.ok(window.document.getElementById('devhubWorkspace'), 'workspace injected');
  }
  window.eval(await readAsset('assets/field_visibility.js'));
  return { window, document: window.document, calls };
}

async function refresh(window) {
  window.document.getElementById('devhubRefresh').click();
  await settle(window);
}

test('missing ADB refresh renders empty inventory without an error banner', async () => {
  const { window, document, calls } = await makeConsole({
    [TOOLS]: missingTools,
    [NETWORK]: networkOk,
    [VERSION]: toolsUnavailable,
    [DEVICES]: toolsUnavailable,
  });

  await refresh(window);

  const feedback = document.getElementById('devhubFeedback');
  assert.equal(feedback.dataset.state, 'success');
  assert.ok(feedback.textContent.startsWith('Ready.'), feedback.textContent);
  assert.ok(!feedback.textContent.includes('tools_unavailable'), feedback.textContent);

  assert.ok(calls.includes(TOOLS));
  assert.ok(calls.includes(NETWORK));
  assert.ok(!calls.includes(VERSION), 'adb version probe skipped while ADB is missing');
  assert.ok(!calls.includes(DEVICES), 'adb devices probe skipped while ADB is missing');

  assert.equal(document.getElementById('devhubToolSource').textContent, 'missing');
  assert.equal(document.getElementById('devhubToolPath').textContent, 'Not installed');
  assert.equal(document.getElementById('devhubToolVersion').textContent, 'Unavailable');
  assert.ok(document.getElementById('devhubToolDot').classList.contains('error'));

  assert.equal(document.getElementById('devhubSelectedDevice').textContent, 'No device selected');
  assert.ok(document.querySelector('#devhubDeviceList .devhub-empty'), 'empty device inventory rendered');

  const install = document.getElementById('devhubInstallTools');
  assert.ok(install, 'Install Managed ADB stays available');
  assert.equal(install.disabled, false);
  assert.ok(document.getElementById('devhubAcceptToolsLicense'), 'license checkbox stays available');
});

test('available ADB still refreshes version and device inventory', async () => {
  const { window, document, calls } = await makeConsole({
    [TOOLS]: systemTools,
    [NETWORK]: networkOk,
    [VERSION]: versionOk,
    [DEVICES]: () => devicesOk([
      { serial: '192.168.68.24:5555', state: 'device', properties: { model: 'Quest 3' } },
    ]),
  });

  await refresh(window);

  assert.ok(calls.includes(VERSION));
  assert.ok(calls.includes(DEVICES));

  const feedback = document.getElementById('devhubFeedback');
  assert.equal(feedback.dataset.state, 'success');
  assert.ok(feedback.textContent.includes('1 ADB device(s)'), feedback.textContent);
  assert.equal(document.getElementById('devhubToolSource').textContent, 'system');
  assert.equal(
    document.getElementById('devhubToolVersion').textContent,
    'Android Debug Bridge version 1.0.41',
  );
  assert.ok(document.getElementById('devhubToolDot').classList.contains('ready'));
  assert.equal(
    document.getElementById('devhubSelectedDevice').textContent,
    '192.168.68.24:5555',
  );
});

test('unexpected device failure while tools are available still surfaces an error', async () => {
  const { window, document } = await makeConsole({
    [TOOLS]: systemTools,
    [NETWORK]: networkOk,
    [VERSION]: versionOk,
    [DEVICES]: toolsUnavailable,
  });

  await refresh(window);

  const feedback = document.getElementById('devhubFeedback');
  assert.equal(feedback.dataset.state, 'error');
  assert.ok(
    feedback.textContent.includes('ADB device refresh failed: tools_unavailable'),
    feedback.textContent,
  );
});

test('repeated automatic refreshes never surface the missing-ADB activity banner', async () => {
  const { window, document, calls } = await makeConsole({
    [TOOLS]: missingTools,
    [NETWORK]: networkOk,
    [VERSION]: toolsUnavailable,
    [DEVICES]: toolsUnavailable,
  }, { workspace: true });

  const activity = document.getElementById('devhubWorkspaceActivity');
  assert.ok(activity, 'workspace activity banner exists');

  // The workspace auto-refresh clicks #devhubRefresh on its five-second
  // interval and on focus; drive the same path several times.
  for (let cycle = 0; cycle < 3; cycle += 1) {
    window.dispatchEvent(new window.Event('focus'));
    await settle(window, 6);
    const feedback = document.getElementById('devhubFeedback');
    assert.notEqual(feedback.dataset.state, 'error', feedback.textContent);
    assert.ok(!activity.classList.contains('visible'), activity.textContent);
    assert.ok(!activity.textContent.includes('tools_unavailable'), activity.textContent);
  }

  assert.ok(!calls.includes(VERSION));
  assert.ok(!calls.includes(DEVICES));
});
