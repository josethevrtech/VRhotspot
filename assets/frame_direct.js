/* Headset transport within the existing connection setup and lifecycle controls. */
(() => {
  const el = id => document.getElementById(id);
  const key = 'vrhotspot.connectionPurpose';
  let current = null;
  let initialized = false;
  let apRunning = false;
  let selected = 'hotspot';
  try { selected = localStorage.getItem(key) === 'headset' ? 'headset' : 'hotspot'; } catch {}
  function headset() { return selected === 'headset'; }
  function text(id, value) {
    const node = el(id);
    if (node && node.textContent !== value) node.textContent = value;
  }
  function render() {
    const adapter = el('ap_adapter');
    const compatible = current?.adapters?.includes(adapter?.value);
    const fields = el('connectionPurposeFields');
    if (!fields) return;
    fields.hidden = !current || (!compatible && !current.active && !headset());
    const selector = el('connectionPurpose');
    selector.value = selected;
    selector.disabled = !!current?.active || apRunning || (typeof actionInFlight !== 'undefined' && actionInFlight);
    if (adapter && current?.active) {
      adapter.value = current.adapter;
      adapter.disabled = true;
      adapter.dataset.connectionLocked = '1';
    } else if (adapter?.dataset.connectionLocked) {
      adapter.disabled = false;
      delete adapter.dataset.connectionLocked;
    }
    if (document.body.dataset.connectionKind !== selected) {
      document.body.dataset.connectionKind = selected;
      document.dispatchEvent(new Event('vrhotspot-connection-changed'));
    }
    el('headsetPairing').hidden = !headset();
    for (const id of ['headsetSsid', 'headsetBssid', 'headsetPassword', 'saveHeadsetPairing']) {
      el(id).disabled = !!current?.active;
    }
    text('connectionPurposeHint', headset()
      ? (current?.paired ? 'Pairing saved. Each device keeps its own internet connection.'
        : 'Pair your headset once below. Each device keeps its own internet connection.')
      : 'Devices join your hotspot. Internet sharing uses your existing settings.');
    text('btnStop', headset() ? 'Disconnect' : 'Stop Hotspot');
  }
  async function fetchStatus() {
    const response = await api('/v1/frame-direct');
    if (!response.ok || !response.json?.data) throw new Error('Headset connection status is unavailable.');
    current = response.json.data;
    if (current.active) selected = 'headset';
    render();
    return current;
  }
  function present(state) {
    if (!current?.active) return state;
    // Presentation only: never change the backend AP engine state or claim NAT.
    return {...state, phase: current.phase === 'connecting' ? 'starting' : current.connected ? 'running' : 'stopped', running: !!current.connected,
      adapter: current.adapter, ap_interface: null, band: '6ghz', mode: 'headset',
      channel_width_mhz: current.width_mhz, engine: {}, last_error: null,
      last_error_detail: null, fallback_reason: null, telemetry: {enabled: false},
      connection_kind: 'headset'};
  }
  async function refreshState(state) {
    apRunning = !!state.running;
    try {
      await fetchStatus();
      if (!initialized) {
        initialized = true;
        if (apRunning && !current.active) selected = 'hotspot';
      }
      render();
    } catch {
      if (current) current = {...current, connected: false};
      if (headset()) setMsg('Could not check the headset connection. Refresh before connecting.', 'dangerText');
    }
    return present(state);
  }
  async function post(action, body = {}) {
    const r = await api(`/v1/frame-direct/${action}`, {method: 'POST', body: JSON.stringify(body)});
    if (!r.ok) throw new Error(r.json?.result_code === 'direct_connect_failed_rolled_back'
      ? 'Could not connect. Wake the headset and try again; the previous connection was restored.'
      : 'Could not change the headset connection. Check pairing and refresh status.');
    return r;
  }
  async function handle(action) {
    if (!headset() && !current?.active) return false;
    await withActionLock(async () => {
      try {
        const state = await fetchStatus();
        if (action === 'stop') {
          setMsg('Disconnecting…');
          setOptimisticHotspotPhase('stopping');
          await post('disconnect');
          setMsg('Disconnected.');
        } else {
          const adapter = el('ap_adapter')?.value;
          if (!state.paired) {
            el('headsetPairing').open = true;
            el('headsetSsid').focus();
            setMsg('Save your headset pairing below the adapter first.');
            return;
          }
          if (!state.adapters.includes(adapter)) {
            setMsg('Select the compatible USB adapter before connecting.', 'dangerText');
            return;
          }
          if (state.active && state.connected && action === 'start') return;
          setMsg('Connecting to headset…');
          setOptimisticHotspotPhase('starting');
          if (state.active) await post('disconnect');
          await post('connect', {adapter});
          setMsg('Headset connected.');
        }
      } catch (error) { setMsg(error.message, 'dangerText'); }
      finally { await refresh(); }
    });
    render();
    return true;
  }
  function guided(stateName) {
    render();
    const direct = headset();
    for (const id of ['basicGuidedProfileSlot', 'basicGuidedSsidSlot', 'basicGuidedPassSlot']) {
      const step = el(id)?.closest('.basic-guided-step');
      if (step) step.hidden = direct;
    }
    const actionStep = el('basicGuidedActionSlot')?.closest('.basic-guided-step');
    const badge = actionStep?.querySelector('.basic-guided-step-number');
    if (badge && badge.textContent !== (direct ? '2' : '5')) badge.textContent = direct ? '2' : '5';
    if (!direct) return;
    text('basicGuidedActionSlotTitle', 'Connect headset');
    text('basicGuidedStateText', stateName === 'running' ? 'Connected' : stateName === 'stopped' ? 'Disconnected' : 'Connecting…');
    text('basicGuidedStatusSummary', current?.connected
      ? 'Headset connected over 6 GHz. Your internet connection is unchanged.'
      : 'Wake your paired headset, then connect.');
    text('btnStartBasic', stateName === 'running' ? 'Disconnect' : stateName === 'stopped' ? 'Connect' : 'Please wait…');
  }
  function pro(stateName) {
    if (!headset()) return;
    text('proServiceStateText', stateName === 'running' ? 'Connected' : stateName === 'stopped' ? 'Disconnected' : 'Connecting…');
    text('proServiceStateSummary', current?.connected
      ? `Headset connection · 6 GHz · ${current.width_mhz} MHz. Each device keeps its own internet connection.`
      : 'Wake your paired headset, then connect.');
    text('btnStart', stateName === 'running' ? 'Disconnect' : stateName === 'stopped' ? 'Connect' : 'Please wait…');
  }
  function init() {
    el('connectionPurpose').addEventListener('change', async event => {
      if (current?.active || apRunning) { render(); return; }
      selected = event.target.value === 'headset' ? 'headset' : 'hotspot';
      try { localStorage.setItem(key, selected); } catch {}
      render();
      await refresh();
    });
    el('ap_adapter')?.addEventListener('change', render);
    el('saveHeadsetPairing').addEventListener('click', async () => {
      const body = {ssid: el('headsetSsid').value, bssid: el('headsetBssid').value,
        passphrase: el('headsetPassword').value};
      el('headsetPassword').value = '';
      await withActionLock(async () => {
        try {
          await post('pair', body);
          el('headsetPairing').open = false;
          setMsg('Headset pairing saved. You can now connect.');
          await refresh();
        } catch (error) { setMsg(error.message, 'dangerText'); }
      });
    });
    render();
  }
  window.headsetConnection = {headset, handles: () => headset() || !!current?.active, refreshState, handle, guided, pro, render};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
