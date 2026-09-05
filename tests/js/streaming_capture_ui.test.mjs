import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../../assets/index.html', import.meta.url), 'utf8');
const source = readFileSync(new URL('../../assets/ui.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('let streamingCaptureView ='), source.indexOf('// --- Canonical preflight diagnostics view'));
const id = '9fd8fb12-a71f-45ee-9daf-739e93904930';
const idle = { state: 'idle', capture_id: null };
const active = { state: 'running', capture_id: id, elapsed_s: 2, sample_count: 1, marker_count: 0 };

function boot(response = { ok: true, status: 200, json: { result_code: 'ok', data: active } }) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost' });
  const w = dom.window;
  const calls = [], downloads = [];
  w.isAuthenticated = true;
  w.api = async (...args) => { calls.push(args); return response; };
  w.refresh = async () => {};
  w.downloadDiagnosticBlob = (...args) => downloads.push(args);
  w.eval(code);
  w.wireStreamingCapture();
  w.renderStreamingCapture(idle);
  const button = (action) => w.document.querySelector(`[data-capture-action="${action}"]`);
  const click = async (action) => { button(action).click(); await new Promise(resolve => setTimeout(resolve, 0)); };
  return { w, dom, calls, downloads, button, click };
}

test('capture uses existing status refresh and does not poll or send traffic on initialization', () => {
  const b = boot();
  assert.equal(b.calls.length, 0);
  assert.doesNotMatch(code, /setInterval|setTimeout|\/v1\/(start|restart|config)/);
  assert.equal(b.button('start').disabled, false);
  assert.equal(b.button('mark').disabled, true);
  b.dom.window.close();
});

test('start sends only bounded recording duration, never hotspot settings', async () => {
  const b = boot();
  b.w.document.getElementById('streamingCaptureDuration').value = '300';
  await b.click('start');
  assert.deepEqual(b.calls.map(([path, opts]) => [path, JSON.parse(opts.body)]),
    [['/v1/diagnostics/streaming', { duration_s: 300 }]]);
  assert.equal(b.button('start').disabled, true);
  assert.equal(b.button('mark').disabled, false);
  b.dom.window.close();
});

test('freeze marker contains capture identity without free-text personal information', async () => {
  const b = boot();
  b.w.renderStreamingCapture(active);
  await b.click('mark');
  assert.equal(b.calls[0][0], '/v1/diagnostics/streaming/mark');
  assert.deepEqual(JSON.parse(b.calls[0][1].body), { capture_id: id });
  b.dom.window.close();
});

test('stop stops only recording, not the hotspot', async () => {
  const b = boot();
  b.w.renderStreamingCapture(active);
  await b.click('stop');
  assert.equal(b.calls[0][0], '/v1/diagnostics/streaming/stop');
  b.dom.window.close();
});

test('download reuses diagnostic blob download and exact capture report route', async () => {
  const b = boot();
  b.w.renderStreamingCapture({ ...active, state: 'completed' });
  await b.click('download');
  assert.equal(b.calls[0][0], '/v1/diagnostics/streaming/report?capture_id=' + id);
  assert.equal(b.downloads[0][1], 'vr-hotspot-streaming-session.json');
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /Review it before sharing/);
  b.dom.window.close();
});

test('unavailable older daemon and logged-out session cannot trigger capture', async () => {
  const b = boot();
  b.w.renderStreamingCapture(null);
  await b.click('start');
  assert.equal(b.calls.length, 0);
  b.w.isAuthenticated = false;
  b.w.renderStreamingCapture(idle);
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /Sign in/);
  assert.equal(b.button('start').disabled, true);
  b.dom.window.close();
});

test('expired capture reports an actionable error and never restarts recording', async () => {
  const b = boot({ ok: false, status: 404 });
  b.w.renderStreamingCapture(active);
  await b.click('download');
  assert.equal(b.calls.length, 1);
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /expired/);
  assert.equal(b.downloads.length, 0);
  b.dom.window.close();
});

test('a late report response cannot download after logout', async () => {
  const b = boot();
  let finish;
  b.w.api = () => new Promise(resolve => { finish = resolve; });
  b.w.renderStreamingCapture({ ...active, state: 'completed' });
  await b.click('download');
  b.w.isAuthenticated = false;
  b.w.clearStreamingCapture();
  finish({ ok: true, status: 200, json: { result_code: 'ok', data: active } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(b.downloads.length, 0);
  assert.equal(b.button('download').disabled, true);
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /Sign in/);
  assert.match(source.slice(source.indexOf('function renderLoginSplash('), source.indexOf('function showAuthenticatedApp(')),
    /isAuthenticated = false;\s+clearStreamingCapture\(\)/);
  b.dom.window.close();
});

test('a response from the previous login cannot change the new login capture state', async () => {
  const b = boot();
  let finish;
  b.w.api = () => new Promise(resolve => { finish = resolve; });
  b.w.renderStreamingCapture({ ...active, state: 'completed' });
  await b.click('download');
  b.w.isAuthenticated = false;
  b.w.clearStreamingCapture();
  b.w.isAuthenticated = true;
  b.w.renderStreamingCapture(idle);
  finish({ ok: true, status: 200, json: { result_code: 'ok', data: active } });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(b.downloads.length, 0);
  assert.equal(b.button('start').disabled, false);
  assert.equal(b.button('download').disabled, true);
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /Ready/);
  b.dom.window.close();
});

test('a late rejected request cannot overwrite logout status or keep buttons busy', async () => {
  const b = boot();
  let fail;
  b.w.api = () => new Promise((_resolve, reject) => { fail = reject; });
  await b.click('start');
  b.w.isAuthenticated = false;
  b.w.clearStreamingCapture();
  fail(new Error('old session request failed'));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /Sign in/);
  b.w.isAuthenticated = true;
  b.w.renderStreamingCapture(idle);
  assert.equal(b.button('start').disabled, false);
  b.dom.window.close();
});

test('a report for a different capture is never downloaded', async () => {
  const b = boot({ ok: true, status: 200, json: { result_code: 'ok',
    data: { ...active, capture_id: 'a-different-session' } } });
  b.w.renderStreamingCapture({ ...active, state: 'completed' });
  await b.click('download');
  assert.equal(b.downloads.length, 0);
  assert.match(b.w.document.getElementById('streamingCaptureStatus').textContent, /different recording/);
  b.dom.window.close();
});

for (const missingId of ['streamingCaptureDuration', 'streamingCaptureStatus']) {
  test(`missing ${missingId} fails capture controls closed without breaking logout`, async () => {
    const b = boot();
    b.w.document.getElementById(missingId).remove();
    assert.doesNotThrow(() => b.w.renderStreamingCapture(active));
    assert.equal(b.button('start').disabled, true);
    assert.equal(b.button('download').disabled, true);
    // Even a stale enabled button cannot start a request after markup disappears.
    b.button('start').disabled = false;
    await b.click('start');
    assert.equal(b.calls.length, 0);
    b.w.isAuthenticated = false;
    assert.doesNotThrow(() => b.w.clearStreamingCapture());
    assert.equal(b.button('start').disabled, true);
    b.dom.window.close();
  });
}

test('missing capture buttons or the whole panel does not break rendering or logout', () => {
  const b = boot();
  for (const button of b.w.document.querySelectorAll('[data-capture-action]')) button.remove();
  assert.doesNotThrow(() => b.w.renderStreamingCapture(active));
  b.w.isAuthenticated = false;
  assert.doesNotThrow(() => b.w.clearStreamingCapture());
  b.w.document.getElementById('streamingCapturePanel').remove();
  assert.doesNotThrow(() => b.w.clearStreamingCapture());
  assert.doesNotThrow(() => b.w.wireStreamingCapture());
  assert.equal(b.calls.length, 0);
  b.dom.window.close();
});

for (const sharedPath of ['api', 'apiBlob']) {
  test(`shared ${sharedPath} rejects a redirect without forwarding the API token`, async () => {
    const secret = 'private-browser-auth-token';
    const requests = [];
    const server = createServer((request, response) => {
      requests.push({ path: request.url, token: request.headers['x-api-token'] });
      if (request.url === '/redirect') {
        response.writeHead(302, { Location: '/redirect-target' });
        response.end();
      } else {
        response.writeHead(200, { 'Content-Type': 'application/json' });
        response.end('{"result_code":"ok","data":{}}');
      }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const dom = new JSDOM('', { runScripts: 'outside-only', url: origin });
    const w = dom.window;
    try {
      w.BASE = origin;
      w.isAuthenticated = true;
      w.companionAuthBridgeAvailable = () => false;
      w.getToken = () => secret;
      w.cid = () => 'test-correlation';
      w.isUnauthorizedStatus = status => status === 401 || status === 403;
      w.logoutToSplash = () => {};
      w.fetch = globalThis.fetch;
      const sharedApi = source.slice(source.indexOf('async function api('),
        source.indexOf('function filenameFromContentDisposition('));
      w.eval(sharedApi);
      // Caller-provided options must not weaken the central policy.
      await assert.rejects(w[sharedPath]('/redirect', { redirect: 'follow' }));
      assert.equal(requests.length, 1);
      assert.equal(requests[0].path, '/redirect');
      assert.equal(requests[0].token, secret);
      assert.equal(requests.filter(request => request.path === '/redirect-target').length, 0);
    } finally {
      dom.window.close();
      await new Promise(resolve => {
        server.close(resolve);
        server.closeAllConnections();
      });
    }
  });
}
