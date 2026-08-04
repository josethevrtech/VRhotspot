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

function apiPayload(url, { passphraseSaved }) {
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
      ...(passphraseSaved
        ? { wpa2_passphrase_set: true, wpa2_passphrase_len: 12 }
        : { wpa2_passphrase_set: false }),
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

function installBrowserStubs(window, options) {
  window.fetch = async (url) => {
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
  throw new Error(`Timed out waiting for ${label}`);
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

  // The composer's Basic restore clears its Pro-only state; the `hidden`
  // property itself is owned by basic_guided.js in the full portal, which
  // hides the button as part of the Basic guided adapter presentation.
  const recommended = document.getElementById('btnUseRecommended');
  assert.ok(recommended);
  assert.equal(recommended.hasAttribute('aria-hidden'), false);
  assert.equal(recommended.tabIndex, 0);
  assert.equal(recommended.style.display, '');
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

  const recommended = document.getElementById('btnUseRecommended');
  assert.ok(recommended);
  assert.equal(recommended.hidden, true);
  assert.equal(recommended.getAttribute('aria-hidden'), 'true');
  assert.equal(recommended.tabIndex, -1);
  assert.equal(recommended.style.display, 'none');

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
  assert.match(currentStyles.href, /148-adapter-source-labels-4/);

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
  assertPasswordRowComposed(document);
}

function assertPasswordRowComposed(document) {
  const rows = document.querySelectorAll('.pro-password-row');
  assert.equal(rows.length, 1, 'exactly one composed password row must exist');
  const row = rows[0];
  assert.deepEqual(
    Array.from(row.children).map((node) => node.id),
    ['wpa2_passphrase', 'btnRevealPass', 'btnShowQr'],
    'input, reveal, and QR must be direct row children in order',
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
  assert.equal(hint.parentElement, field, 'passHint must sit outside the row');
  assert.ok(!row.contains(hint));
  // 4 === Node.DOCUMENT_POSITION_FOLLOWING
  assert.ok(row.compareDocumentPosition(hint) & 4, 'passHint must follow the row');
}

async function runFullPortalScenario({ passphraseSaved }) {
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
  installBrowserStubs(window, { passphraseSaved });
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
  const recommendedNode = document.getElementById('btnUseRecommended');
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
    assertProLayout(document);
    assert.equal(document.getElementById('btnUseRecommended'), recommendedNode);
    assert.equal(document.querySelectorAll('[id="btnUseRecommended"]').length, 1);
    assertPasswordIdentity();

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
  assert.deepEqual(errors, []);
  assert.deepEqual(unhandled, []);
  dom.window.close();
}

test('real portal composes Pro across toggles with a saved passphrase', async () => {
  await runFullPortalScenario({ passphraseSaved: true });
});

test('real portal composes Pro across toggles on a clean install without a saved passphrase', async () => {
  await runFullPortalScenario({ passphraseSaved: false });
});
