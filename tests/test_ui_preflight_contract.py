from pathlib import Path
import json
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "assets" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


# The project does not carry a browser test dependency.  This harness executes the
# real assets/ui.js in Node's VM with the smallest DOM surface the preflight view
# needs.  Its textContent and innerHTML implementations deliberately behave
# differently so hostile-value tests fail if rendering switches to HTML parsing.
NODE_HARNESS = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const { webcrypto } = require('crypto');

class FakeClassList {
  constructor(owner) {
    this.owner = owner;
  }

  values() {
    return new Set(String(this.owner.className || '').split(/\s+/).filter(Boolean));
  }

  write(values) {
    this.owner.className = Array.from(values).join(' ');
  }

  add(...names) {
    const values = this.values();
    names.forEach((name) => values.add(name));
    this.write(values);
  }

  remove(...names) {
    const values = this.values();
    names.forEach((name) => values.delete(name));
    this.write(values);
  }

  contains(name) {
    return this.values().has(name);
  }

  toggle(name, force) {
    const values = this.values();
    const enabled = force === undefined ? !values.has(name) : Boolean(force);
    if (enabled) values.add(name);
    else values.delete(name);
    this.write(values);
    return enabled;
  }
}

function dataKey(attribute) {
  return attribute
    .slice(5)
    .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

class FakeElement {
  constructor(tagName, ownerDocument, state) {
    this.tagName = String(tagName || 'div').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.state = state;
    this.parentNode = null;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = new Map();
    this.className = '';
    this.classList = new FakeClassList(this);
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.placeholder = '';
    this.readOnly = false;
    this.type = '';
    this.href = '';
    this.download = '';
    this.clicked = 0;
    this._textContent = '';
    this._innerHTML = null;
  }

  get id() {
    return this.attributes.id || '';
  }

  set id(value) {
    this.setAttribute('id', value);
  }

  get textContent() {
    const childText = this.children.map((child) => child.textContent).join('');
    return this._textContent + childText;
  }

  set textContent(value) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._innerHTML = null;
    this._textContent = value === null || value === undefined ? '' : String(value);
  }

  get innerHTML() {
    return this._innerHTML === null ? this.textContent : this._innerHTML;
  }

  set innerHTML(value) {
    const html = value === null || value === undefined ? '' : String(value);
    this.state.htmlWrites += 1;
    this._innerHTML = html;
    this._textContent = '';
    this.children = [];
    for (const tag of ['script', 'img', 'iframe']) {
      if (new RegExp(`<${tag}\\b`, 'i').test(html)) {
        this.state.interpretedDangerousTags.push(tag);
        this.appendChild(this.ownerDocument.createElement(tag));
      }
    }
  }

  get options() {
    return this.children.filter((child) => child.tagName === 'OPTION');
  }

  get nextSibling() {
    if (!this.parentNode) return null;
    const index = this.parentNode.children.indexOf(this);
    return index >= 0 ? this.parentNode.children[index + 1] || null : null;
  }

  setAttribute(name, value) {
    const text = String(value);
    this.attributes[name] = text;
    if (name === 'class') this.className = text;
    if (name.startsWith('data-')) this.dataset[dataKey(name)] = text;
    if (name === 'id') this.ownerDocument.elementsById.set(text, this);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  appendChild(child) {
    if (child.tagName === '#DOCUMENT-FRAGMENT') {
      for (const grandchild of [...child.children]) this.appendChild(grandchild);
      return child;
    }
    if (child.parentNode) {
      const oldIndex = child.parentNode.children.indexOf(child);
      if (oldIndex >= 0) child.parentNode.children.splice(oldIndex, 1);
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child, reference) {
    if (!reference || reference.parentNode !== this) return this.appendChild(child);
    if (child.parentNode) child.remove();
    const index = this.children.indexOf(reference);
    child.parentNode = this;
    this.children.splice(index, 0, child);
    return child;
  }

  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._textContent = '';
    this._innerHTML = null;
    for (const child of children) this.appendChild(child);
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  async dispatch(type, extra = {}) {
    const event = {
      target: this,
      currentTarget: this,
      preventDefault() {},
      stopPropagation() {},
      ...extra,
    };
    for (const listener of this.listeners.get(type) || []) {
      await listener(event);
    }
  }

  click() {
    this.clicked += 1;
    return this.dispatch('click');
  }

  focus() {
    this.state.focusedElement = this;
  }

  select() {}

  querySelectorAll(selector) {
    const matches = [];
    const visit = (element) => {
      for (const child of element.children) {
        if (matchesSelector(child, selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function matchesSelector(element, selector) {
  if (selector.startsWith('.')) return element.classList.contains(selector.slice(1));
  if (selector.startsWith('#')) return element.id === selector.slice(1);
  const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
  if (attribute) {
    const name = attribute[1];
    const expected = attribute[2];
    const actual = name.startsWith('data-')
      ? element.dataset[dataKey(name)]
      : element.getAttribute(name);
    return expected === undefined ? actual !== undefined && actual !== null : actual === expected;
  }
  const tagAttribute = selector.match(/^([a-z0-9-]+)\[([^=]+)="([^"]*)"\]$/i);
  if (tagAttribute) {
    return element.tagName === tagAttribute[1].toUpperCase()
      && element.getAttribute(tagAttribute[2]) === tagAttribute[3];
  }
  return element.tagName === selector.toUpperCase();
}

class FakeDocument {
  constructor(state) {
    this.state = state;
    this.readyState = 'loading';
    this.elements = new Set();
    this.elementsById = new Map();
    this.listeners = new Map();
    this.body = this.createElement('body');
  }

  createElement(tagName) {
    const element = new FakeElement(tagName, this, this.state);
    this.elements.add(element);
    return element;
  }

  createDocumentFragment() {
    return this.createElement('#document-fragment');
  }

  getElementById(id) {
    if (!this.elementsById.has(id)) {
      const element = this.createElement('div');
      element.id = id;
    }
    return this.elementsById.get(id);
  }

  querySelectorAll(selector) {
    return Array.from(this.elements).filter((element) => matchesSelector(element, selector));
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
}

class FakeStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function responseBody(body, status = 200) {
  return { body, status };
}

function createEnvironment() {
  const state = {
    blobs: [],
    focusedElement: null,
    htmlWrites: 0,
    interpretedDangerousTags: [],
    intervals: [],
    objectUrls: [],
    requests: [],
    responses: [],
    revokedUrls: [],
  };
  const document = new FakeDocument(state);
  const localStorage = new FakeStorage();
  const sessionStorage = new FakeStorage();

  class FakeBlob {
    constructor(parts, options = {}) {
      this.parts = parts.map((part) => String(part));
      this.type = options.type || '';
      state.blobs.push(this);
    }

    async text() {
      return this.parts.join('');
    }
  }

  async function fetch(url, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    state.requests.push({ url: String(url), method, options });
    if (!state.responses.length) throw new Error(`No mock response for ${method} ${url}`);
    const next = state.responses.shift();
    if (next.throw) throw next.throw;
    const status = next.status === undefined ? 200 : next.status;
    const raw = next.raw === undefined ? JSON.stringify(next.body) : String(next.raw);
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: new Headers(next.headers || {}),
      async text() { return raw; },
      async blob() { return new FakeBlob([raw]); },
    };
  }

  const sandbox = {
    Blob: FakeBlob,
    Headers,
    URL: {
      createObjectURL(blob) {
        const url = `blob:test-${state.objectUrls.length + 1}`;
        state.objectUrls.push({ blob, url });
        return url;
      },
      revokeObjectURL(url) {
        state.revokedUrls.push(url);
      },
    },
    clearInterval() {},
    clearTimeout() {},
    console: { error() {}, log() {}, warn() {} },
    crypto: webcrypto,
    document,
    fetch,
    localStorage,
    navigator: { clipboard: { async writeText() {} } },
    sessionStorage,
    setInterval(callback, delay) {
      const timer = { callback, delay };
      state.intervals.push(timer);
      return timer;
    },
    setTimeout(callback) {
      return { callback };
    },
  };
  sandbox.window = sandbox;
  sandbox.window.addEventListener = () => {};
  sandbox.window.history = { replaceState() {} };
  sandbox.window.location = { hash: '', pathname: '/', search: '' };
  sandbox.window.isSecureContext = true;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  const source = fs.readFileSync('assets/ui.js', 'utf8');
  vm.runInContext(source, context, { filename: 'assets/ui.js' });

  return {
    context,
    document,
    localStorage,
    run(sourceText) {
      return vm.runInContext(sourceText, context);
    },
    state,
  };
}

function canonicalReport(overrides = {}) {
  return {
    schema_version: 1,
    overall_readiness: 'blocked',
    platform: { os_name: 'Bazzite', os_version: '42', host_kind: 'immutable' },
    firewall: { backend: 'firewalld', status: 'active' },
    services: {
      network_manager: { status: 'active' },
      iwd: { present: false, active: false },
    },
    network: { active_uplink_interface: 'enp4s0' },
    wifi: { selected_adapter: 'wlan1' },
    issues: [
      { severity: 'blocked', code: 'no_ap_capable_adapter', message: 'No AP-capable adapter.' },
      { severity: 'warning', code: 'regdom_unknown', message: 'Regulatory domain is unknown.' },
    ],
    recommended_actions: [
      { code: 'choose_adapter', message: 'Choose an AP-capable adapter.' },
    ],
    ...overrides,
  };
}

function envelope(report, extras = {}) {
  return { result_code: 'preflight_report', data: report, ...extras };
}

function authenticate(environment, token = 'test-auth-token') {
  environment.run(`isAuthenticated = true; authFlowLocked = false; setToken(${JSON.stringify(token)});`);
}

function addNavigation(environment) {
  const { document } = environment;
  const statusTab = document.createElement('button');
  statusTab.className = 'nav-item active';
  statusTab.dataset.tab = 'status';
  document.body.appendChild(statusTab);

  const diagnosticsTab = document.createElement('button');
  diagnosticsTab.className = 'nav-item';
  diagnosticsTab.dataset.tab = 'diagnostics';
  document.body.appendChild(diagnosticsTab);

  const statusPane = document.getElementById('tab-status');
  statusPane.className = 'tab-pane active';
  const diagnosticsPane = document.getElementById('tab-diagnostics');
  diagnosticsPane.className = 'tab-pane';
  return { diagnosticsTab, statusTab };
}

async function wireAuthenticatedUi(environment) {
  const navigation = addNavigation(environment);
  environment.run(`
    applyUiMode = () => {};
    initCharts = () => {};
    wireDirtyTracking = () => {};
    wireQosBasic = () => {};
    enforceBandRules = () => {};
    wireQr = () => {};
    loadAdapters = async () => {};
    loadAdapterReadiness = async () => {};
    refresh = async () => {};
    applyAutoRefresh = () => {};
    refreshInfo = async () => {};
    bootstrapAuthenticatedUi();
  `);
  await settle();
  return navigation;
}

async function settle() {
  for (let index = 0; index < 8; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function waitForPreflightIdle(environment) {
  for (let index = 0; index < 30; index += 1) {
    if (!environment.run('preflightRequestInFlight')) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  throw new Error('preflight request did not settle');
}

function elementText(environment, id) {
  return environment.document.getElementById(id).textContent;
}

function listMessages(environment, id) {
  return environment.document.getElementById(id).children.map((item) => ({
    message: item.children[0] ? item.children[0].textContent : item.textContent,
    code: item.children[1] ? item.children[1].textContent : '',
  }));
}

async function loadReport(environment, report, extras = {}) {
  environment.state.responses.push(responseBody(envelope(report, extras)));
  await environment.run('loadPreflightReport()');
}

async function scenarioOnDemand() {
  const environment = createEnvironment();
  authenticate(environment);
  const navigation = await wireAuthenticatedUi(environment);

  assert.deepStrictEqual(environment.state.requests, []);
  await navigation.statusTab.dispatch('click');
  await settle();
  assert.deepStrictEqual(environment.state.requests, []);

  environment.state.responses.push(responseBody(envelope(canonicalReport())));
  await navigation.diagnosticsTab.dispatch('click');
  await waitForPreflightIdle(environment);
  assert.strictEqual(environment.state.requests.length, 1);

  environment.state.responses.push(responseBody(envelope(canonicalReport({ overall_readiness: 'ready' }))));
  await environment.document.getElementById('btnRefreshPreflight').dispatch('click');
  assert.strictEqual(environment.state.requests.length, 2);

  for (const request of environment.state.requests) {
    assert.strictEqual(request.url, '/v1/diagnostics/preflight');
    assert.strictEqual(request.method, 'GET');
  }
}

async function scenarioPolling() {
  const environment = createEnvironment();
  authenticate(environment);
  environment.context.__apiCalls = [];
  environment.run(`
    api = async (path, options = {}) => {
      __apiCalls.push({ path, method: String(options.method || 'GET').toUpperCase() });
      return { ok: false, status: 503, json: null, raw: '' };
    };
  `);

  environment.document.getElementById('privacyMode').checked = true;
  await environment.run('refreshVisibleUi()');
  const auto = environment.document.getElementById('autoRefresh');
  auto.checked = true;
  environment.document.getElementById('refreshEvery').value = '2500';
  environment.run('applyAutoRefresh()');
  assert.strictEqual(environment.state.intervals.length, 1);
  await environment.state.intervals[0].callback();

  const paths = Array.from(environment.context.__apiCalls, (entry) => entry.path);
  assert(paths.includes('/v1/status'));
  assert(paths.includes('/v1/config'));
  assert(paths.includes('/v1/adapters/readiness'));
  assert(!paths.includes('/v1/diagnostics/preflight'));
}

async function scenarioSuccess() {
  const environment = createEnvironment();
  authenticate(environment);
  const report = canonicalReport();
  await loadReport(environment, report);

  const readiness = environment.document.getElementById('preflightReadiness');
  assert.strictEqual(readiness.textContent, 'Blocked');
  assert.strictEqual(readiness.dataset.readiness, 'blocked');
  assert.deepStrictEqual(listMessages(environment, 'preflightBlockingIssues'), [
    { message: 'No AP-capable adapter.', code: 'no_ap_capable_adapter' },
  ]);
  assert.deepStrictEqual(listMessages(environment, 'preflightWarningIssues'), [
    { message: 'Regulatory domain is unknown.', code: 'regdom_unknown' },
  ]);
  assert.deepStrictEqual(listMessages(environment, 'preflightActions'), [
    { message: 'Choose an AP-capable adapter.', code: 'choose_adapter' },
  ]);
  assert.strictEqual(elementText(environment, 'preflightSelectedAdapter'), 'wlan1');
  assert.strictEqual(elementText(environment, 'preflightUplink'), 'enp4s0');
  assert.strictEqual(elementText(environment, 'preflightPlatform'), 'Bazzite · 42 · Immutable');
  assert.strictEqual(elementText(environment, 'preflightFirewall'), 'Firewalld · Active');
  assert.strictEqual(elementText(environment, 'preflightNetworkManager'), 'Active');
  assert.strictEqual(elementText(environment, 'preflightIwd'), 'Not installed');
  assert.strictEqual(elementText(environment, 'preflightStatus'), 'Canonical preflight report collected.');
  assert.deepStrictEqual(JSON.parse(elementText(environment, 'preflightRawJson')), report);
}

async function scenarioAuthErrors() {
  for (const status of [401, 403]) {
    const environment = createEnvironment();
    authenticate(environment, `secret-${status}`);
    environment.state.responses.push(responseBody({ result_code: 'invalid_token' }, status));
    await environment.run('loadPreflightReport()');

    assert.strictEqual(environment.run('isAuthenticated'), false);
    assert.strictEqual(environment.run('lastPreflightReport'), null);
    assert.strictEqual(environment.localStorage.getItem('vr_hotspot_token'), null);
    assert.strictEqual(environment.document.body.getAttribute('data-auth-state'), 'unauthenticated');
    assert.strictEqual(
      environment.document.getElementById('login-splash').getAttribute('aria-hidden'),
      'false',
    );
    assert.strictEqual(
      elementText(environment, 'loginError'),
      'Your session expired. Sign in again to view diagnostics.',
    );
    assert.strictEqual(environment.document.getElementById('btnExportPreflight').disabled, true);
  }
}

async function scenarioRequestErrors() {
  {
    const environment = createEnvironment();
    authenticate(environment);
    environment.state.responses.push({ throw: new Error('connection refused') });
    await environment.run('loadPreflightReport()');
    assert.strictEqual(
      elementText(environment, 'preflightStatus'),
      'Unable to reach the service. Check the connection and try again.',
    );
    assert.strictEqual(environment.document.getElementById('preflightStatus').dataset.state, 'error');
  }

  {
    const environment = createEnvironment();
    authenticate(environment);
    environment.state.responses.push(responseBody({ result_code: 'preflight_failed' }, 503));
    await environment.run('loadPreflightReport()');
    assert.strictEqual(
      elementText(environment, 'preflightStatus'),
      'Preflight report request failed (HTTP 503: preflight_failed).',
    );
    assert.strictEqual(environment.document.getElementById('preflightStatus').dataset.state, 'error');
  }

  {
    const environment = createEnvironment();
    authenticate(environment);
    environment.state.responses.push(responseBody({ result_code: 'preflight_report', data: { schema_version: 1 } }));
    await environment.run('loadPreflightReport()');
    assert.strictEqual(
      elementText(environment, 'preflightStatus'),
      'The service returned a malformed preflight report. Refresh to try again.',
    );
    assert.strictEqual(environment.document.getElementById('preflightStatus').dataset.state, 'error');
  }
}

async function scenarioStaleRefresh() {
  const environment = createEnvironment();
  authenticate(environment);
  await wireAuthenticatedUi(environment);
  await loadReport(environment, canonicalReport());
  assert.strictEqual(environment.document.getElementById('btnExportPreflight').disabled, false);
  assert.notStrictEqual(elementText(environment, 'preflightRawJson'), '');

  environment.state.responses.push(responseBody({ result_code: 'service_unavailable' }, 503));
  await environment.document.getElementById('btnRefreshPreflight').dispatch('click');

  assert.strictEqual(environment.run('lastPreflightReport'), null);
  assert.strictEqual(environment.document.getElementById('btnExportPreflight').disabled, true);
  assert.strictEqual(elementText(environment, 'preflightRawJson'), '');
  assert.strictEqual(elementText(environment, 'preflightSelectedAdapter'), '--');
  assert.strictEqual(environment.document.getElementById('preflightReadiness').textContent, 'Not collected');
  assert.strictEqual(environment.document.getElementById('preflightStatus').dataset.state, 'error');
  assert.match(elementText(environment, 'preflightStatus'), /HTTP 503/);
}

async function scenarioHostileValues() {
  const environment = createEnvironment();
  authenticate(environment);
  environment.state.htmlWrites = 0;
  environment.state.interpretedDangerousTags = [];
  const hostile = '<img src=x onerror="globalThis.pwned=true"><script>globalThis.pwned=true</script>';
  const report = canonicalReport({
    platform: { os_name: hostile, os_version: '1', host_kind: 'mutable' },
    wifi: { selected_adapter: hostile },
    issues: [{ severity: 'blocked', code: hostile, message: hostile }],
    recommended_actions: [{ code: 'hostile_action', message: hostile }],
  });
  await loadReport(environment, report);

  assert.strictEqual(elementText(environment, 'preflightSelectedAdapter'), hostile);
  assert.match(elementText(environment, 'preflightPlatform'), /<Img\b.*<Script>/);
  assert.strictEqual(listMessages(environment, 'preflightBlockingIssues')[0].message, hostile);
  assert.strictEqual(listMessages(environment, 'preflightBlockingIssues')[0].code, hostile);
  assert.strictEqual(listMessages(environment, 'preflightActions')[0].message, hostile);
  assert.strictEqual(
    JSON.parse(elementText(environment, 'preflightRawJson')).wifi.selected_adapter,
    hostile,
  );
  assert.strictEqual(environment.state.htmlWrites, 0);
  assert.deepStrictEqual(environment.state.interpretedDangerousTags, []);
  assert.strictEqual(environment.run('globalThis.pwned'), undefined);
  for (const id of [
    'preflightSelectedAdapter',
    'preflightPlatform',
    'preflightBlockingIssues',
    'preflightActions',
    'preflightRawJson',
  ]) {
    const element = environment.document.getElementById(id);
    assert.strictEqual(element.querySelectorAll('script').length, 0);
    assert.strictEqual(element.querySelectorAll('img').length, 0);
  }
}

async function scenarioOptionalFields() {
  const environment = createEnvironment();
  authenticate(environment);
  const report = {
    schema_version: 1,
    overall_readiness: 'ready',
    issues: [],
    recommended_actions: [],
  };
  await loadReport(environment, report);

  assert.strictEqual(elementText(environment, 'preflightStatus'), 'Canonical preflight report collected.');
  for (const id of [
    'preflightSelectedAdapter',
    'preflightUplink',
    'preflightPlatform',
    'preflightFirewall',
    'preflightNetworkManager',
    'preflightIwd',
  ]) {
    assert.strictEqual(elementText(environment, id), 'Not reported');
  }
  assert.strictEqual(listMessages(environment, 'preflightBlockingIssues')[0].message, 'No blocking issues.');
  assert.strictEqual(listMessages(environment, 'preflightWarningIssues')[0].message, 'No warnings.');
  assert.strictEqual(listMessages(environment, 'preflightActions')[0].message, 'No actions recommended.');
}

async function scenarioExport() {
  const environment = createEnvironment();
  const token = 'token-that-must-not-be-exported';
  authenticate(environment, token);
  const report = canonicalReport({ report_id: 'canonical-only' });
  await loadReport(environment, report, {
    auth_token: 'envelope-secret',
    ui_state: { selected_tab: 'diagnostics' },
  });
  environment.run('exportPreflightReport()');

  assert.strictEqual(environment.state.blobs.length, 1);
  const blob = environment.state.blobs[0];
  const text = await blob.text();
  const exported = JSON.parse(text);
  assert.deepStrictEqual(exported, report);
  assert.strictEqual(blob.type, 'application/json');
  assert(!text.includes(token));
  assert(!text.includes('envelope-secret'));
  assert(!Object.prototype.hasOwnProperty.call(exported, 'result_code'));
  assert(!Object.prototype.hasOwnProperty.call(exported, 'auth_token'));
  assert(!Object.prototype.hasOwnProperty.call(exported, 'ui_state'));
  assert.strictEqual(environment.state.objectUrls.length, 1);
  assert.deepStrictEqual(environment.state.revokedUrls, ['blob:test-1']);
}

async function scenarioCompanionAuth() {
  const secret = 'companion-auth-secret-value';
  {
    const environment = createEnvironment();
    environment.context.__bridgeMessages = [];
    environment.context.__bootstraps = 0;
    environment.run(`
      window.location.origin = 'http://127.0.0.1:8732';
      window.location.pathname = '/ui';
      window.webkit = {
        messageHandlers: {
          vrHotspotCompanionAuth: {
            postMessage: async (raw) => {
              const message = JSON.parse(raw);
              __bridgeMessages.push(message);
              return message.type === 'auth_accepted' ? 'accepted' : '';
            },
          },
        },
      };
      bootstrapAuthenticatedUi = () => { __bootstraps += 1; };
    `);
    environment.document.getElementById('loginToken').value = secret;
    environment.state.responses.push(responseBody({ result_code: 'ok', data: { phase: 'stopped' } }));

    await environment.run('submitLoginSplashToken()');

    assert.strictEqual(environment.run('isAuthenticated'), true);
    assert.strictEqual(environment.run('getToken()'), secret);
    assert.strictEqual(environment.localStorage.getItem('vr_hotspot_token'), null);
    assert.strictEqual(environment.context.__bootstraps, 1);
    assert.deepStrictEqual(
      Array.from(environment.context.__bridgeMessages, (message) => message.type),
      ['auth_accepted'],
    );
    assert.strictEqual(environment.context.__bridgeMessages[0].version, 1);

    environment.run(`logoutToSplash('Invalid token');`);
    await settle();
    assert.strictEqual(environment.run('isAuthenticated'), false);
    assert.strictEqual(environment.run('getToken()'), '');
    assert.deepStrictEqual(
      Array.from(environment.context.__bridgeMessages, (message) => message.type),
      ['auth_accepted', 'auth_cleared'],
    );
  }

  {
    const environment = createEnvironment();
    environment.context.__bridgeMessages = [];
    environment.context.__bootstraps = 0;
    environment.run(`
      window.location.origin = 'http://127.0.0.1:8732';
      window.location.pathname = '/ui';
      window.webkit = {
        messageHandlers: {
          vrHotspotCompanionAuth: {
            postMessage: async (raw) => {
              const message = JSON.parse(raw);
              __bridgeMessages.push(message);
              return message.type === 'token_request'
                ? ${JSON.stringify(secret)}
                : 'rejected';
            },
          },
        },
      };
      bootstrapAuthenticatedUi = () => { __bootstraps += 1; };
    `);
    environment.state.responses.push(responseBody({ result_code: 'ok', data: { phase: 'running' } }));

    await environment.run('init()');

    assert.strictEqual(environment.run('isAuthenticated'), true);
    assert.strictEqual(environment.run('getToken()'), secret);
    assert.strictEqual(environment.localStorage.getItem('vr_hotspot_token'), null);
    assert.strictEqual(environment.context.__bootstraps, 1);
    assert.deepStrictEqual(
      Array.from(environment.context.__bridgeMessages, (message) => message.type),
      ['token_request'],
    );
  }
}

const scenarios = {
  auth_errors: scenarioAuthErrors,
  companion_auth: scenarioCompanionAuth,
  export: scenarioExport,
  hostile: scenarioHostileValues,
  on_demand: scenarioOnDemand,
  optional: scenarioOptionalFields,
  polling: scenarioPolling,
  request_errors: scenarioRequestErrors,
  stale: scenarioStaleRefresh,
  success: scenarioSuccess,
};

const scenario = process.argv[1];
if (!Object.prototype.hasOwnProperty.call(scenarios, scenario)) {
  throw new Error(`Unknown scenario: ${scenario}`);
}

scenarios[scenario]()
  .then(() => process.stdout.write(JSON.stringify({ scenario, ok: true })))
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
  });
"""


def _run_node_scenario(name: str) -> None:
    if NODE is None:
        raise AssertionError("Node.js is required for executable Web UI behavior tests")
    result = subprocess.run(
        [NODE, "-e", NODE_HARNESS, name],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Node UI scenario {name!r} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    payload = json.loads(result.stdout)
    if payload != {"scenario": name, "ok": True}:
        raise AssertionError(f"Unexpected Node UI scenario result: {payload!r}")


class TestUiPreflightContract(unittest.TestCase):
    def test_diagnostics_view_remains_in_pro_mode(self):
        """Keep one source check for placement; behavior is covered below."""
        advanced_start = HTML.index("<!-- ADVANCED UI SECTION -->")
        basic_html = HTML[:advanced_start]
        advanced_html = HTML[advanced_start:]

        self.assertNotIn('data-tab="diagnostics"', basic_html)
        self.assertNotIn('id="tab-diagnostics"', basic_html)
        self.assertEqual(advanced_html.count('data-tab="diagnostics"'), 1)
        for element_id in (
            "tab-diagnostics",
            "btnRefreshPreflight",
            "btnExportPreflight",
            "preflightStatus",
            "preflightReadiness",
            "preflightSelectedAdapter",
            "preflightUplink",
            "preflightPlatform",
            "preflightFirewall",
            "preflightNetworkManager",
            "preflightIwd",
            "preflightBlockingIssues",
            "preflightWarningIssues",
            "preflightActions",
            "preflightRawJson",
        ):
            self.assertEqual(advanced_html.count(f'id="{element_id}"'), 1)

    def test_tab_open_and_refresh_fetch_exact_canonical_endpoint(self):
        _run_node_scenario("on_demand")

    def test_normal_status_refresh_and_polling_do_not_fetch_diagnostics(self):
        _run_node_scenario("polling")

    def test_successful_report_renders_required_diagnostics(self):
        _run_node_scenario("success")

    def test_unauthorized_responses_follow_logout_behavior(self):
        _run_node_scenario("auth_errors")

    def test_companion_auth_success_wallet_startup_and_logout_sync(self):
        _run_node_scenario("companion_auth")

    def test_network_http_and_malformed_response_errors_are_clear(self):
        _run_node_scenario("request_errors")

    def test_failed_refresh_clears_stale_report_and_disables_export(self):
        _run_node_scenario("stale")

    def test_hostile_report_values_remain_text_not_html(self):
        _run_node_scenario("hostile")

    def test_missing_optional_fields_render_without_crashing(self):
        _run_node_scenario("optional")

    def test_export_is_exactly_the_canonical_report(self):
        _run_node_scenario("export")


if __name__ == "__main__":
    unittest.main()
