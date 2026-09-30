/* Explicit role switching: never infer pairing or switch radios on page load. */
(() => {
  const byId = id => document.getElementById(id);
  let busy = false;
  let current = null;
  function render() {
    if (!current) return;
    const s = current;
    byId('frameDirectStatus').textContent = s.active
      ? (s.connected ? `Connected · ${s.frequency_mhz} MHz · ${s.width_mhz} MHz width`
        : 'Direct session reserved, but link is unavailable. Disconnect to recover.')
      : (s.paired ? 'Paired · disconnected' : 'Pairing required');
    const select = byId('frameDirectAdapter');
    const selected = select.value;
    select.replaceChildren(...s.adapters.map(name => new Option(name, name)));
    if (s.adapters.includes(selected)) select.value = selected;
    select.disabled = busy || s.active;
    byId('frameDirectConnect').disabled = busy || s.active || !s.paired || !s.adapters.length;
    byId('frameDirectDisconnect').disabled = busy || !s.active;
    byId('frameDirectHotspot').disabled = busy || !s.active;
    for (const input of byId('frameDirectPair').elements) input.disabled = busy || s.active;
  }
  async function refresh() {
    if (busy) return;
    const response = await api('/v1/frame-direct');
    if (response.ok && response.json?.data) {
      current = response.json.data;
      render();
    }
  }
  async function action(name, body) {
    if (busy) return;
    busy = true;
    render();
    byId('frameDirectMessage').textContent = 'Working… connection changes can take up to a minute.';
    try {
      const response = await api(`/v1/frame-direct/${name}`, {method: 'POST', body: JSON.stringify(body)});
      byId('frameDirectMessage').textContent = response.ok ? 'Done.'
        : (response.json?.result_code || 'Connection failed. Refresh status before retrying.');
    } catch {
      byId('frameDirectMessage').textContent = 'Service unavailable. Refresh status before retrying.';
    } finally {
      busy = false;
      await refresh();
      render();
    }
  }
  byId('frameDirectConnect').addEventListener('click', () => action('connect', {adapter: byId('frameDirectAdapter').value}));
  byId('frameDirectDisconnect').addEventListener('click', () => action('disconnect', {}));
  byId('frameDirectHotspot').addEventListener('click', () => action('disconnect', {restore_hotspot: true}));
  byId('frameDirectPair').addEventListener('submit', event => {
    event.preventDefault();
    const body = {ssid: byId('frameDirectSsid').value, bssid: byId('frameDirectBssid').value,
      passphrase: byId('frameDirectPassword').value};
    byId('frameDirectPassword').value = '';
    action('pair', body);
  });
  refresh();
  setInterval(refresh, 5000);
})();
