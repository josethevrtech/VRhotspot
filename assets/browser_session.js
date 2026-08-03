(function persistBrowserTabAuthentication() {
  'use strict';

  const SESSION_PATH = '/v1/auth/browser-session';
  const MAX_WAIT_MS = 15000;
  let installed = false;
  let pendingCandidate = '';
  const startedAt = Date.now();

  async function restoreBrowserSession() {
    try {
      const response = await fetch(SESSION_PATH, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) return false;
      if (typeof enterAuthenticatedApp === 'function') enterAuthenticatedApp();
      return true;
    } catch {
      return false;
    }
  }

  async function createBrowserSession(token) {
    const candidate = String(token || '').trim();
    if (!candidate) return false;
    try {
      const response = await fetch(SESSION_PATH, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'X-Api-Token': candidate,
          'X-Correlation-Id': `ui-session-${Date.now()}`,
        },
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  function captureLoginCandidate() {
    const input = document.getElementById('loginToken');
    pendingCandidate = String((input && input.value) || '').trim();
  }

  function syncAuthenticatedState() {
    const state = document.body && document.body.getAttribute('data-auth-state');
    if (state !== 'authenticated' || !pendingCandidate) return;
    const candidate = pendingCandidate;
    pendingCandidate = '';
    void createBrowserSession(candidate);
  }

  function install() {
    if (installed) return true;
    if (typeof enterAuthenticatedApp !== 'function' || !document.body) return false;
    installed = true;

    const form = document.getElementById('loginForm');
    if (form) form.addEventListener('submit', captureLoginCandidate, true);

    const observer = new MutationObserver(syncAuthenticatedState);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['data-auth-state'],
    });
    void restoreBrowserSession();
    return true;
  }

  function waitForUiAuth() {
    if (install()) return;
    if (Date.now() - startedAt > MAX_WAIT_MS) return;
    window.setTimeout(waitForUiAuth, 25);
  }

  waitForUiAuth();
})();
