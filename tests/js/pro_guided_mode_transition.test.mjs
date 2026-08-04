import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const SOURCE_URL = new URL('../../assets/pro_guided_workflow.js', import.meta.url);
const BASIC_QUICK_FIELDS = [
  'ap_adapter',
  'band_preference',
  'ap_security',
  'country',
  'enable_internet',
  'qos_preset',
];
const BASIC_CONNECT_FIELDS = ['ssid'];
const CONNECTION_FIELDS = [
  'ssid',
  'wpa2_passphrase',
  'band_preference',
  'ap_security',
  'country',
  'enable_internet',
];

function field(key, control) {
  return `<div class="form-group" data-field="${key}"><label>${key}</label>${control}</div>`;
}

function fixture() {
  return `<!doctype html>
<html><head></head><body data-ui-mode="basic" data-auth-state="authenticated">
  <div id="basicQuickFields"></div>
  <div id="basicConnectFields"></div>
  <div class="advanced-layout" data-ui-section="advanced">
    <nav><ul>
      <li class="nav-item" data-tab="overview">Overview</li>
      <li class="nav-item" data-tab="telemetry">Telemetry</li>
      <li class="nav-item" data-tab="diagnostics">Diagnostics</li>
      <li class="nav-item" data-tab="logs">Logs</li>
    </ul></nav>
    <main>
      <div id="tab-overview">
        <section class="pro-service-card">
          <div class="pro-service-state-copy"><h3 id="proServiceStateText">Stopped</h3></div>
          <div class="pro-service-secondary">
            <button id="btnRestart">Restart Service</button>
            <button id="btnRepair">Repair Network</button>
          </div>
          <div class="hero-meta"></div>
          <div class="hero-feedback" id="dirty"></div>
          <span id="pillTxt">Stopped</span>
          <button id="btnStart">Start Hotspot</button>
        </section>
        <section class="adapter-readiness-card" data-adapter-readiness-card>
          <div class="card-header"><h2>Adapter Readiness</h2></div>
          <div class="card-body">Ready</div>
        </section>
        <section id="proHotspotConfiguration" class="pro-configuration">
          <div class="settings-header">
            <button id="btnSaveConfig">Save Changes</button>
            <button id="btnSaveRestart">Save & Restart</button>
          </div>
          <div class="preset-bar"><div class="btn-group">
            <button id="btnApplyVrProfileUltra">Ultra Low Latency</button>
            <button id="btnApplyVrProfileHigh">High Throughput</button>
            <button id="btnApplyVrProfile">Balanced</button>
            <button id="btnApplyVrProfileStable">Stability</button>
          </div></div>
          <div id="wirelessHome">
            ${field('ssid', '<input id="ssid">')}
            ${field('wpa2_passphrase', '<div class="input-with-action"><input id="wpa2_passphrase"><button id="btnRevealPass">Eye</button></div><div class="row"><button id="btnShowQr">Show QR</button><span id="passHint"></span></div>')}
            ${field('band_preference', '<select id="band_preference"><option>5 GHz</option></select>')}
            ${field('ap_security', '<select id="ap_security"><option>WPA2</option></select>')}
            ${field('country', '<select id="country_sel"><option>US</option></select><input id="country">')}
            ${field('ap_adapter', '<select id="ap_adapter"></select><div class="row"><button id="btnUseRecommended">Recommended</button><button id="btnReloadAdapters">Rescan</button></div><div id="adapterHint"></div>')}
          </div>
          <div id="networkHome">
            ${field('enable_internet', '<input id="enable_internet" type="checkbox">')}
            ${field('qos_preset', '<select id="qos_preset"><option value="balanced" selected>Balanced</option></select>')}
          </div>
          <details class="pro-config-details" open><summary>Wireless</summary><div class="pro-config-body">${field('channel_auto_select', '<input id="channel_auto_select" type="checkbox">')}${field('channel_5g', '<input id="channel_5g">')}${field('channel_6g', '<input id="channel_6g">')}</div></details>
          <details class="pro-config-details"><summary>Network</summary><div class="pro-config-body">${field('bridge_mode', '<input id="bridge_mode" type="checkbox">')}<input id="bridge_name"><input id="bridge_uplink"></div></details>
          <details class="pro-config-details"><summary>System & Performance</summary><div class="pro-config-body">${field('debug', '<input id="debug" type="checkbox">')}</div></details>
        </section>
      </div>
      <div id="tab-telemetry"><section id="cardTelemetry"><div class="card-header"><span id="telemetrySummary"></span><span id="telemetryWarnings"></span></div><div class="card-body">Charts</div></section></div>
      <div id="tab-diagnostics"><div class="preflight-page"><div class="preflight-header"><div class="action-group"><button id="btnRefreshPreflight">Refresh</button></div></div></div></div>
      <div id="tab-logs"><section class="card">Logs</section></div>
    </main>
  </div>
</body></html>`;
}

function rememberHomes(document) {
  const homes = new Map();
  for (const key of [...BASIC_QUICK_FIELDS, ...BASIC_CONNECT_FIELDS]) {
    const node = document.querySelector(`[data-field="${key}"]`);
    homes.set(node, { parent: node.parentNode, next: node.nextSibling });
  }
  return homes;
}

function restore(node, home) {
  if (home.next && home.next.parentNode === home.parent) home.parent.insertBefore(node, home.next);
  else home.parent.appendChild(node);
}

function applyMode(document, homes, mode) {
  document.body.dataset.uiMode = mode;
  if (mode === 'basic') {
    const quick = document.getElementById('basicQuickFields');
    const connect = document.getElementById('basicConnectFields');
    BASIC_QUICK_FIELDS.forEach((key) => quick.appendChild(document.querySelector(`[data-field="${key}"]`)));
    BASIC_CONNECT_FIELDS.forEach((key) => connect.appendChild(document.querySelector(`[data-field="${key}"]`)));
    return;
  }
  [...BASIC_QUICK_FIELDS, ...BASIC_CONNECT_FIELDS]
    .slice()
    .reverse()
    .forEach((key) => {
      const node = document.querySelector(`[data-field="${key}"]`);
      restore(node, homes.get(node));
    });
}

function tick(window, ms = 30) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function assertProLayout(document) {
  const requiredIds = [
    'ap_adapter',
    'btnUseRecommended',
    'btnReloadAdapters',
    'btnApplyVrProfileUltra',
    'btnApplyVrProfile',
    'btnApplyVrProfileHigh',
    'btnApplyVrProfileStable',
    'qos_preset',
    ...CONNECTION_FIELDS,
    'btnStart',
    'btnSaveConfig',
    'btnSaveRestart',
    'btnRepair',
  ];
  const diagnostics = {
    stage: document.body.dataset.proGuidedStage,
    error: document.body.dataset.proGuidedError || null,
    mode: document.body.dataset.uiMode,
    overview: Boolean(document.getElementById('tab-overview')),
    configuration: Boolean(document.getElementById('proHotspotConfiguration')),
    serviceCard: Boolean(document.querySelector('#tab-overview .pro-service-card')),
    preset: Boolean(document.querySelector('#proHotspotConfiguration .preset-bar')),
    missingIds: requiredIds.filter((id) => !document.getElementById(id)),
  };
  const workflow = document.getElementById('proGuidedWorkflow');
  assert.ok(workflow, `Pro workflow should exist: ${JSON.stringify(diagnostics)}`);
  assert.equal(
    document.body.dataset.proGuidedStage,
    'ready',
    JSON.stringify(diagnostics),
  );
  assert.deepEqual(
    Array.from(workflow.querySelectorAll('.pro-guided-step')).map((node) => node.dataset.step),
    ['1', '2', '3', '4', '5'],
  );
  assert.ok(document.querySelector('#proStepAdapter [data-field="ap_adapter"]'));
  assert.equal(document.querySelector('#proStepAdapter [data-adapter-readiness-card]'), null);
  assert.ok(document.querySelector('#proStepPerformance .preset-bar'));
  for (const key of CONNECTION_FIELDS) {
    assert.ok(document.querySelector(`#proStepHotspot [data-field="${key}"]`), `${key} should be in Step 3`);
  }
  assert.equal(document.querySelectorAll('#proStepAdvanced .pro-config-details').length, 3);
  for (const id of ['btnStart', 'btnSaveConfig', 'btnSaveRestart', 'btnRepair']) {
    assert.ok(document.querySelector(`#proStepAction #${id}`), `${id} should be in Step 5`);
  }
  assert.equal(document.querySelector('.pro-guided-header')?.dataset.proDensityReady, '1');
  assert.equal(document.querySelector('[data-field="ap_adapter"]')?.dataset.proDensityReady, '1');
  assert.equal(document.querySelector('[data-field="wpa2_passphrase"]')?.dataset.proDensityReady, '1');

  const recommended = document.getElementById('btnUseRecommended');
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);
  assert.equal(recommended.style.display, 'none');
}

function assertRecommendedRestoredForBasic(document) {
  const recommended = document.getElementById('btnUseRecommended');
  assert.equal(recommended.hidden, false);
  assert.equal(recommended.hasAttribute('aria-hidden'), false);
  assert.equal(recommended.tabIndex, 0);
  assert.equal(recommended.style.display, '');
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

function assertReadyPublicationsComplete(captures, minimum) {
  assert.ok(
    captures.length >= minimum,
    `expected at least ${minimum} ready publications, saw ${captures.length}`,
  );
  for (const capture of captures) {
    assert.deepEqual(capture, {
      hidden: true,
      ariaHidden: 'true',
      tabIndex: -1,
      display: 'none',
    }, 'recommended button state must be complete when ready is published');
  }
}

test('authoritative Pro composer survives repeated Basic and Pro transitions', async () => {
  const dom = new JSDOM(fixture(), {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8732/ui',
  });
  const { window } = dom;
  const { document } = window;
  const errors = [];
  window.console.error = (...args) => errors.push(args.map(String).join(' '));

  const homes = rememberHomes(document);
  applyMode(document, homes, 'basic');
  const source = await readFile(SOURCE_URL, 'utf8');
  const readyCaptures = captureReadyPublications(window, document);
  window.eval(source);
  await tick(window);
  const recommendedNode = document.getElementById('btnUseRecommended');

  assert.equal(document.getElementById('proGuidedWorkflow'), null);
  assert.equal(document.body.dataset.proGuidedStage, 'waiting-for-pro');
  for (const key of BASIC_QUICK_FIELDS) {
    assert.ok(document.querySelector(`#basicQuickFields [data-field="${key}"]`));
  }
  assert.ok(document.querySelector('#basicConnectFields [data-field="ssid"]'));

  for (let cycle = 0; cycle < 3; cycle += 1) {
    applyMode(document, homes, 'advanced');
    await tick(window, 60);
    assertProLayout(document);
    assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
    assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);

    applyMode(document, homes, 'basic');
    await tick(window, 60);
    assert.equal(document.body.dataset.proGuidedStage, 'waiting-for-pro');
    for (const key of BASIC_QUICK_FIELDS) {
      assert.ok(document.querySelector(`#basicQuickFields [data-field="${key}"]`), `${key} should return to Basic`);
    }
    assert.ok(document.querySelector('#basicConnectFields [data-field="ssid"]'));
    assert.equal(document.querySelector('#basicQuickFields .pro-runtime-wrapper'), null);
    assert.equal(document.querySelector('#basicConnectFields .pro-runtime-wrapper'), null);
    assertRecommendedRestoredForBasic(document);
    assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
    assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);
  }

  applyMode(document, homes, 'advanced');
  await tick(window, 60);
  assertProLayout(document);
  assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
  assertReadyPublicationsComplete(readyCaptures, 4);
  assert.deepEqual(errors, []);
  dom.window.close();
});
