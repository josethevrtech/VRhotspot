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
  // Composer-only fixture (no ui.js): the attribute-identical fallback in
  // applyStepBadgeHelp must still put step guidance on the numbered badges,
  // and no persistent gray description paragraph may render.
  assert.equal(workflow.querySelectorAll('.pro-guided-help').length, 0,
    'persistent gray step descriptions must be gone');
  for (const section of workflow.querySelectorAll('.pro-guided-step')) {
    const badge = section.querySelector('.pro-guided-number');
    assert.ok(badge, 'each step keeps its numbered badge');
    assert.ok(badge.classList.contains('step-help-badge'));
    assert.ok((badge.getAttribute('data-tip') || '').length > 0, 'badge carries the step guidance');
    assert.equal(badge.getAttribute('aria-label'), badge.getAttribute('data-tip'));
    assert.equal(badge.getAttribute('tabindex'), '0');
    assert.equal(badge.hasAttribute('aria-hidden'), false);
    assert.equal(badge.hasAttribute('title'), false);
  }
  assert.equal(
    workflow
      .querySelector('#proStepHotspot')
      ?.closest('.pro-guided-step')
      ?.querySelector('.pro-guided-number')
      ?.getAttribute('data-tip'),
    'Set the hotspot name, password, band, security mode, and country.',
  );
  assert.ok(document.querySelector('#proStepAdapter [data-field="ap_adapter"]'));
  assert.equal(document.querySelector('#proStepAdapter [data-adapter-readiness-card]'), null);
  assert.ok(document.querySelector('#proStepPerformance .preset-bar'));
  for (const key of CONNECTION_FIELDS.filter((field) => field !== 'enable_internet')) {
    assert.ok(document.querySelector(`#proStepHotspot [data-field="${key}"]`), `${key} should be in Step 3`);
  }
  assert.equal(document.querySelector('#proStepHotspot [data-field="enable_internet"]'), null);
  assert.ok(
    document.querySelector('#tab-troubleshooting #proConnectivityCard [data-field="enable_internet"]'),
    'internet sharing must live under Troubleshooting > Connectivity',
  );
  assert.equal(document.querySelectorAll('[id="enable_internet"]').length, 1);
  const troubleshootingShell = document.querySelector('#tab-troubleshooting .troubleshooting-shell');
  const quality = document.getElementById('proConnectionQuality');
  assert.ok(troubleshootingShell && quality);
  assert.equal(quality.parentElement, troubleshootingShell);
  assert.equal(troubleshootingShell.lastElementChild, quality,
    'Connection Quality must be the final Troubleshooting section');
  assert.equal(document.querySelector('#proGuidedWorkflow #proConnectionQuality'), null);
  assert.equal(document.querySelectorAll('#proStepAdvanced .pro-config-details').length, 3);
  assert.ok(document.querySelector('#proStepAction #btnStart'), 'btnStart should be in Step 5');
  const staging = document.querySelector('#proStepAction .pro-guided-hidden-staging');
  assert.ok(staging && staging.hidden);
  for (const id of ['btnSaveConfig', 'btnSaveRestart']) {
    assert.ok(staging.contains(document.getElementById(id)), `${id} parked in staging`);
    assert.equal(document.getElementById(id).hidden, true);
    assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1);
  }
  assert.equal(document.querySelector('#proStepAction #btnRepair'), null);
  assert.ok(document.querySelector('#tab-troubleshooting .troubleshooting-actions #btnRepair'));
  assert.equal(document.querySelectorAll('[id="btnRepair"]').length, 1);
  assert.equal(document.querySelectorAll('[id="btnSaveRestart"]').length, 1);
  assert.equal(document.querySelector('.pro-guided-header')?.dataset.proDensityReady, '1');
  assert.equal(document.querySelector('[data-field="ap_adapter"]')?.dataset.proDensityReady, '1');
  assert.equal(document.querySelector('[data-field="wpa2_passphrase"]')?.dataset.proDensityReady, '1');

  const recommended = document.getElementById('btnUseRecommended');
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);
  assert.equal(recommended.style.display, 'none');
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
  assert.equal(reveal.parentElement, row);
  assert.equal(qr.parentElement, row);
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
      'original wrappers must not contain the controls in Pro',
    );
  }
  const hint = document.getElementById('passHint');
  assert.ok(hint);
  assert.ok(field.contains(hint) && !row.contains(hint));
  // 4 === Node.DOCUMENT_POSITION_FOLLOWING
  assert.ok(row.compareDocumentPosition(hint) & 4);
}

function assertRecommendedRestoredForBasic(document) {
  // Redundant in both modes: the adapter label already says "(Recommended)",
  // so the live node stays hidden with deterministic state in Basic too.
  const recommended = document.getElementById('btnUseRecommended');
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);
  assert.equal(recommended.style.display, 'none');
  for (const id of ['btnSaveConfig', 'btnSaveRestart']) {
    const control = document.getElementById(id);
    if (!control) continue;
    assert.equal(control.hidden, false, `${id} must leave Pro staging state in Basic`);
    assert.equal(control.tabIndex, 0);
  }
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
  const passwordInputNode = document.getElementById('wpa2_passphrase');
  const revealNode = document.getElementById('btnRevealPass');
  const qrNode = document.getElementById('btnShowQr');
  const assertPasswordIdentity = () => {
    assert.equal(document.getElementById('wpa2_passphrase'), passwordInputNode);
    assert.equal(document.getElementById('btnRevealPass'), revealNode);
    assert.equal(document.getElementById('btnShowQr'), qrNode);
    for (const id of ['wpa2_passphrase', 'btnRevealPass', 'btnShowQr']) {
      assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1);
    }
  };

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
    assertPasswordIdentity();

    applyMode(document, homes, 'basic');
    await tick(window, 60);
    assert.equal(document.body.dataset.proGuidedStage, 'waiting-for-pro');
    assert.equal(document.querySelector('.pro-password-row'), null);
    assert.ok(passwordInputNode.closest('.input-with-action'), 'input must return to its original wrapper in Basic');
    assertPasswordIdentity();
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
  assertPasswordIdentity();

  // Ready must be withheld while password composition is incomplete and
  // recover once the missing control returns.
  const qrParent = qrNode.parentElement;
  qrNode.remove();
  await tick(window, 250);
  assert.equal(document.body.dataset.proGuidedStage, 'composing');
  await tick(window, 400);
  assert.equal(document.body.dataset.proGuidedStage, 'composing');
  qrParent.appendChild(qrNode);
  await tick(window, 400);
  assert.equal(document.body.dataset.proGuidedStage, 'ready');
  assertPasswordRowComposed(document);
  assertPasswordIdentity();

  // Late third-party injections: readiness and composition must hold, and
  // the composer must not fight a wrapper placed around the input.
  const rowEl = document.querySelector('.pro-password-row');
  const injectedSibling = document.createElement('div');
  rowEl.appendChild(injectedSibling);
  const wrapEl = document.createElement('div');
  passwordInputNode.parentNode.insertBefore(wrapEl, passwordInputNode);
  wrapEl.appendChild(passwordInputNode);
  await tick(window, 400);
  assert.equal(document.body.dataset.proGuidedStage, 'ready');
  assertPasswordRowComposed(document);
  assertPasswordIdentity();
  assert.ok(wrapEl.contains(passwordInputNode), 'composer must not unwrap the injected wrapper');

  assertReadyPublicationsComplete(readyCaptures, 4);
  assert.deepEqual(errors, []);
  dom.window.close();
});
