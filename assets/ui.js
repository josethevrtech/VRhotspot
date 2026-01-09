const BASE = '';
const STORE = (function(){
  try{ localStorage.setItem('__t','1'); localStorage.removeItem('__t'); return localStorage; }catch{ return sessionStorage; }
})();
const LS = {
  token: 'vr_hotspot_api_token',
  privacy: 'vr_hotspot_privacy',
  auto: 'vr_hotspot_auto',
  every: 'vr_hotspot_every'
};

// --- Sticky edit guard
let cfgDirty = false;
let cfgJustSaved = false;
let lastCfg = null;
let lastAdapters = null;

const CFG_IDS = [
  "ssid","wpa2_passphrase","band_preference","ap_security","channel_6g","country","country_sel",
  "optimized_no_virt","ap_adapter","ap_ready_timeout_s","fallback_channel_2g",
  "channel_width","beacon_interval","dtim_period","short_guard_interval","tx_power","channel_auto_select",
  "lan_gateway_ip","dhcp_start_ip","dhcp_end_ip","dhcp_dns","enable_internet",
  "wifi_power_save_disable","usb_autosuspend_disable","cpu_governor_performance","cpu_affinity","sysctl_tuning",
  "irq_affinity","interrupt_coalescing","tcp_low_latency","memory_tuning","io_scheduler_optimize",
  "telemetry_enable","telemetry_interval_s","watchdog_enable","watchdog_interval_s",
  "connection_quality_monitoring","auto_channel_switch",
  "qos_preset","nat_accel","bridge_mode","bridge_name","bridge_uplink",
  "firewalld_enabled","firewalld_enable_masquerade","firewalld_enable_forward","firewalld_cleanup_on_stop",
  "debug"
];

function setDirty(v){
  cfgDirty = !!v;
  document.getElementById('dirty').textContent = cfgDirty ? 'Unsaved changes' : '';
}

function markDirty(ev){
  if (ev && ev.isTrusted === false) return;
  if (!cfgDirty) setDirty(true);
}

function wireDirtyTracking(){
  for (const id of CFG_IDS){
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener('pointerdown', markDirty);
    el.addEventListener('keydown', markDirty);
    el.addEventListener('change', markDirty);
    el.addEventListener('input', markDirty);
    el.addEventListener('click', markDirty);
  }

  // Normalize country input to uppercase and 2 chars (or 00).
  const c = document.getElementById('country');
  c.addEventListener('input', (e) => {
    const raw = (e.target.value || '').toString().toUpperCase().replace(/[^A-Z0-9]/g,'');
    e.target.value = raw.slice(0,2);
    syncCountrySelectFromInput();
  });

  const csel = document.getElementById('country_sel');
  csel.addEventListener('change', () => {
    const v = csel.value;
    if (v === '__custom') {
      c.disabled = false;
      c.focus();
      return;
    }
    c.disabled = false;
    c.value = v;
  });

  // Band/security coupling
  document.getElementById('band_preference').addEventListener('change', () => {
    enforceBandRules();
    maybeAutoPickAdapterForBand();
  });

  document.getElementById('ap_security').addEventListener('change', () => {
    enforceBandRules();
  });
}

function cid(){ return 'ui-' + Date.now() + '-' + Math.random().toString(16).slice(2); }

function getToken(){
  const input = (document.getElementById('apiToken').value || '').trim();
  return input || ((STORE.getItem(LS.token) || '').trim());
}
function setToken(v){
  try{ STORE.setItem(LS.token, (v || '').trim()); }catch{}
}

function fmtTs(epoch){
  if (!epoch) return '--';
  try{ return new Date(epoch * 1000).toLocaleString(); }catch{ return String(epoch); }
}

function fmtNum(v, digits=1){
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const n = Number(v);
  if (Number.isNaN(n)) return '--';
  return n.toFixed(digits);
}

function fmtPct(v){
  return (v === null || v === undefined) ? '--' : fmtNum(v, 1);
}

function fmtDbm(v){
  return (v === null || v === undefined) ? '--' : `${v} dBm`;
}

function fmtMbps(v){
  return (v === null || v === undefined) ? '--' : fmtNum(v, 1);
}

function renderTelemetry(t){
  const sumEl = document.getElementById('telemetrySummary');
  const warnEl = document.getElementById('telemetryWarnings');
  const body = document.getElementById('telemetryBody');
  if (!body || !sumEl || !warnEl) return;

  body.innerHTML = '';
  if (!t || t.enabled === false){
    sumEl.textContent = 'Telemetry disabled.';
    warnEl.textContent = '';
    return;
  }

  const summary = t.summary || {};
  sumEl.textContent =
    `clients=${summary.client_count ?? 0} ` +
    `rssi_avg=${fmtDbm(summary.rssi_avg_dbm)} ` +
    `quality_avg=${fmtNum(summary.quality_score_avg, 0)} ` +
    `loss_avg=${fmtPct(summary.loss_pct_avg)}%`;

  const warns = (t.warnings || []).join(' | ');
  warnEl.textContent = warns ? `warnings: ${warns}` : '';

  const clients = t.clients || [];
  if (!clients.length){
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 7;
    td.textContent = 'No clients connected.';
    td.className = 'muted';
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  for (const c of clients){
    const tr = document.createElement('tr');
    const id = (c.mac || '--') + (c.ip ? ` (${c.ip})` : '');
    const qualityScore = (c.quality_score !== null && c.quality_score !== undefined) ? fmtNum(c.quality_score, 0) : '--';
    const cols = [
      id,
      fmtDbm(c.signal_dbm),
      fmtMbps(c.tx_bitrate_mbps),
      fmtMbps(c.rx_bitrate_mbps),
      qualityScore,
      fmtPct(c.retry_pct),
      fmtPct(c.loss_pct),
    ];
    for (const text of cols){
      const td = document.createElement('td');
      td.textContent = text;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

async function api(path, opts={}){
  const headers = Object.assign({}, opts.headers || {}, {'X-Correlation-Id': cid()});
  const tok = getToken();
  if (tok) headers['X-Api-Token'] = tok;
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';

  const res = await fetch(BASE + path, Object.assign({}, opts, {headers}));
  const text = await res.text();
  let json = null;
  try{ json = JSON.parse(text); }catch{}
  return {ok: res.ok, status: res.status, json, raw: text};
}

function setMsg(text, kind=''){
  const el = document.getElementById('msg');
  el.textContent = text || '';
  el.className = 'small mt-10' + (kind ? (' ' + kind) : '');
}

function setPill(state){
  const pill = document.getElementById('pill');
  const txt  = document.getElementById('pillTxt');
  const running = !!state.running;
  const phase = state.phase || '--';
  const adapter = state.adapter || '--';
  const band = state.band || '--';
  const mode = state.mode || '--';

  pill.classList.remove('ok','err');
  if (running) pill.classList.add('ok');
  else if (phase === 'error') pill.classList.add('err');

  // Show the pill
  pill.style.display = 'inline-flex';

  // Create clean, readable status text
  const statusParts = [];
  
  if (running) {
    statusParts.push('Running');
  } else if (phase === 'error') {
    statusParts.push('Error');
  } else {
    statusParts.push('Stopped');
  }
  
  if (phase && phase !== '--' && phase !== 'stopped' && phase !== 'error' && !(phase === 'stopped' && !running)) {
    statusParts.push(phase.charAt(0).toUpperCase() + phase.slice(1));
  }
  
  if (adapter && adapter !== '--') {
    statusParts.push(adapter);
  }
  
  if (band && band !== '--') {
    statusParts.push(band);
  }
  
  if (mode && mode !== '--' && mode !== 'nat') {
    statusParts.push(mode);
  }

  if (statusParts.length === 0) {
    txt.textContent = 'Loading...';
  } else {
    txt.textContent = statusParts.join(' | ');
  }
}

function syncCountrySelectFromInput(){
  const c = (document.getElementById('country').value || '').toString().toUpperCase();
  const sel = document.getElementById('country_sel');
  let found = false;
  for (const opt of sel.options){
    if (opt.value === c){ sel.value = c; found = true; break; }
  }
  if (!found) sel.value = '__custom';
}

function enforceBandRules(){
  const band = document.getElementById('band_preference').value;
  const secEl = document.getElementById('ap_security');
  const secHint = document.getElementById('secHint');
  const bandHint = document.getElementById('bandHint');
  const sixgBox = document.getElementById('sixgBox');

  if (band === '6ghz'){
    // 6 GHz requires WPA3-SAE; lock it to prevent confusing start errors.
    secEl.value = 'wpa3_sae';
    secEl.disabled = true;
    sixgBox.style.display = '';
    bandHint.innerHTML = "<strong>6 GHz:</strong> requires a 6 GHz-capable adapter and a correct Country. WPA3-SAE is enforced.";
    secHint.textContent = "Locked: 6 GHz requires WPA3 (SAE).";
  }else{
    secEl.disabled = false;
    sixgBox.style.display = 'none';
    if (band === '5ghz') bandHint.innerHTML = "<strong>5 GHz:</strong> best default for VR streaming on most adapters.";
    else bandHint.innerHTML = "<strong>2.4 GHz:</strong> compatibility/fallback band (higher latency / more interference).";
    secHint.textContent = "WPA2 (PSK) is typical. WPA3 (SAE) may be supported but depends on driver + clients.";
  }
}

function capsLabel(a){
  const parts = [];
  if (a.supports_6ghz) parts.push('6G');
  if (a.supports_5ghz) parts.push('5G');
  if (a.supports_2ghz) parts.push('2G');
  return parts.length ? parts.join('/') : '--';
}

function adapterSupportsBand(a, band){
  if (!a) return false;
  if (band === '6ghz') return !!a.supports_6ghz;
  if (band === '5ghz') return !!a.supports_5ghz;
  if (band === '2.4ghz') return !!a.supports_2ghz;
  return true;
}

function maybeAutoPickAdapterForBand(){
  const band = document.getElementById('band_preference').value;
  const sel = document.getElementById('ap_adapter');
  const hint = document.getElementById('adapterHint');
  if (!lastAdapters || !Array.isArray(lastAdapters.adapters)) return;

  const byIf = new Map();
  for (const a of lastAdapters.adapters) byIf.set(a.ifname, a);

  const cur = sel.value;
  const curA = byIf.get(cur);

  if (band === '6ghz'){
    const any6 = lastAdapters.adapters.filter(a => a.supports_ap && a.supports_6ghz);
    if (!any6.length){
      hint.innerHTML = "<span class='pillWarn'>No 6 GHz-capable AP adapter detected</span>";
      return;
    }
    hint.textContent = "6 GHz requires an adapter that supports 6 GHz in AP mode.";
    if (!curA || !adapterSupportsBand(curA, '6ghz')){
      // Prefer recommended if it also supports 6 GHz, else first 6G adapter.
      const rec = lastAdapters.recommended;
      const recA = byIf.get(rec);
      const pick = (recA && recA.supports_ap && recA.supports_6ghz) ? rec : any6[0].ifname;
      sel.value = pick;
      setDirty(true);
    }
  }else{
    hint.textContent = "";
  }
}

function applyVrProfile(profileName = 'balanced'){
  const profiles = {
    'ultra_low_latency': {
      band_preference: '5ghz',
      ap_security: 'wpa2',
      optimized_no_virt: false,
      enable_internet: true,
      wifi_power_save_disable: true,
      usb_autosuspend_disable: true,
      cpu_governor_performance: true,
      sysctl_tuning: true,
      tcp_low_latency: true,
      memory_tuning: true,
      interrupt_coalescing: true,
      telemetry_enable: true,
      telemetry_interval_s: '1.0',
      watchdog_enable: true,
      watchdog_interval_s: '1.0',
      qos_preset: 'ultra_low_latency',
      nat_accel: true,
      bridge_mode: false,
      beacon_interval: 20,
      dtim_period: 1,
      short_guard_interval: true,
    },
    'high_throughput': {
      band_preference: '5ghz',
      ap_security: 'wpa2',
      optimized_no_virt: false,
      enable_internet: true,
      wifi_power_save_disable: true,
      usb_autosuspend_disable: true,
      cpu_governor_performance: true,
      sysctl_tuning: true,
      telemetry_enable: true,
      telemetry_interval_s: '2.0',
      watchdog_enable: true,
      watchdog_interval_s: '2.0',
      qos_preset: 'high_throughput',
      nat_accel: true,
      bridge_mode: false,
      channel_width: '80',
      beacon_interval: 100,
      dtim_period: 1,
      short_guard_interval: true,
    },
    'balanced': {
      band_preference: '5ghz',
      ap_security: 'wpa2',
      optimized_no_virt: false,
      enable_internet: true,
      wifi_power_save_disable: true,
      usb_autosuspend_disable: true,
      cpu_governor_performance: true,
      sysctl_tuning: true,
      telemetry_enable: true,
      telemetry_interval_s: '2.0',
      watchdog_enable: true,
      watchdog_interval_s: '2.0',
      qos_preset: 'vr',
      nat_accel: true,
      bridge_mode: false,
      beacon_interval: 50,
      dtim_period: 1,
      short_guard_interval: true,
    },
    'stability': {
      band_preference: '5ghz',
      ap_security: 'wpa2',
      optimized_no_virt: false,
      enable_internet: true,
      wifi_power_save_disable: true,
      usb_autosuspend_disable: true,
      cpu_governor_performance: false,
      sysctl_tuning: false,
      telemetry_enable: true,
      telemetry_interval_s: '3.0',
      watchdog_enable: true,
      watchdog_interval_s: '3.0',
      qos_preset: 'balanced',
      nat_accel: false,
      bridge_mode: false,
      beacon_interval: 100,
      dtim_period: 3,
      short_guard_interval: false,
    }
  };
  
  const profile = profiles[profileName] || profiles['balanced'];
  
  document.getElementById('band_preference').value = profile.band_preference;
  document.getElementById('ap_security').value = profile.ap_security;
  document.getElementById('optimized_no_virt').checked = profile.optimized_no_virt;
  document.getElementById('enable_internet').checked = profile.enable_internet;
  document.getElementById('wifi_power_save_disable').checked = profile.wifi_power_save_disable;
  document.getElementById('usb_autosuspend_disable').checked = profile.usb_autosuspend_disable;
  document.getElementById('cpu_governor_performance').checked = profile.cpu_governor_performance;
  document.getElementById('sysctl_tuning').checked = profile.sysctl_tuning;
  if (document.getElementById('tcp_low_latency')) {
    document.getElementById('tcp_low_latency').checked = profile.tcp_low_latency || false;
  }
  if (document.getElementById('memory_tuning')) {
    document.getElementById('memory_tuning').checked = profile.memory_tuning || false;
  }
  if (document.getElementById('interrupt_coalescing')) {
    document.getElementById('interrupt_coalescing').checked = profile.interrupt_coalescing || false;
  }
  document.getElementById('telemetry_enable').checked = profile.telemetry_enable;
  document.getElementById('telemetry_interval_s').value = profile.telemetry_interval_s;
  document.getElementById('watchdog_enable').checked = profile.watchdog_enable;
  document.getElementById('watchdog_interval_s').value = profile.watchdog_interval_s;
  document.getElementById('qos_preset').value = profile.qos_preset;
  document.getElementById('nat_accel').checked = profile.nat_accel;
  document.getElementById('bridge_mode').checked = profile.bridge_mode;
  if (document.getElementById('channel_width')) {
    document.getElementById('channel_width').value = profile.channel_width || 'auto';
  }
  if (document.getElementById('beacon_interval')) {
    document.getElementById('beacon_interval').value = profile.beacon_interval;
  }
  if (document.getElementById('dtim_period')) {
    document.getElementById('dtim_period').value = profile.dtim_period;
  }
  if (document.getElementById('short_guard_interval')) {
    document.getElementById('short_guard_interval').checked = profile.short_guard_interval;
  }
  enforceBandRules();
  maybeAutoPickAdapterForBand();
  setDirty(true);
}

function getForm(){
  const out = {
    ssid: document.getElementById('ssid').value,
    band_preference: document.getElementById('band_preference').value,
    ap_security: document.getElementById('ap_security').value,
    country: document.getElementById('country').value,
    optimized_no_virt: document.getElementById('optimized_no_virt').checked,
    ap_adapter: document.getElementById('ap_adapter').value,
    ap_ready_timeout_s: parseFloat(document.getElementById('ap_ready_timeout_s').value || '6.0'),
    fallback_channel_2g: parseInt(document.getElementById('fallback_channel_2g').value || '6', 10),
    channel_width: document.getElementById('channel_width').value,
    beacon_interval: parseInt(document.getElementById('beacon_interval').value || '50', 10),
    dtim_period: parseInt(document.getElementById('dtim_period').value || '1', 10),
    short_guard_interval: document.getElementById('short_guard_interval').checked,
    channel_auto_select: document.getElementById('channel_auto_select').checked,
    enable_internet: document.getElementById('enable_internet').checked,
    wifi_power_save_disable: document.getElementById('wifi_power_save_disable').checked,
    usb_autosuspend_disable: document.getElementById('usb_autosuspend_disable').checked,
    cpu_governor_performance: document.getElementById('cpu_governor_performance').checked,
    sysctl_tuning: document.getElementById('sysctl_tuning').checked,
    interrupt_coalescing: document.getElementById('interrupt_coalescing').checked,
    tcp_low_latency: document.getElementById('tcp_low_latency').checked,
    memory_tuning: document.getElementById('memory_tuning').checked,
    io_scheduler_optimize: document.getElementById('io_scheduler_optimize').checked,
    telemetry_enable: document.getElementById('telemetry_enable').checked,
    telemetry_interval_s: parseFloat(document.getElementById('telemetry_interval_s').value || '2.0'),
    watchdog_enable: document.getElementById('watchdog_enable').checked,
    watchdog_interval_s: parseFloat(document.getElementById('watchdog_interval_s').value || '2.0'),
    connection_quality_monitoring: document.getElementById('connection_quality_monitoring').checked,
    auto_channel_switch: document.getElementById('auto_channel_switch').checked,
    qos_preset: document.getElementById('qos_preset').value,
    nat_accel: document.getElementById('nat_accel').checked,
    bridge_mode: document.getElementById('bridge_mode').checked,
    firewalld_enabled: document.getElementById('firewalld_enabled').checked,
    firewalld_enable_masquerade: document.getElementById('firewalld_enable_masquerade').checked,
    firewalld_enable_forward: document.getElementById('firewalld_enable_forward').checked,
    firewalld_cleanup_on_stop: document.getElementById('firewalld_cleanup_on_stop').checked,
    debug: document.getElementById('debug').checked,
    firewalld_zone: (lastCfg && lastCfg.firewalld_zone) ? lastCfg.firewalld_zone : 'trusted',
  };

  // Optional 6 GHz channel
  const ch6 = (document.getElementById('channel_6g').value || '').trim();
  if (ch6){
    const n = parseInt(ch6, 10);
    if (!Number.isNaN(n)) out.channel_6g = n;
  }

  // Optional TX power
  const txPower = (document.getElementById('tx_power').value || '').trim();
  if (txPower){
    const n = parseInt(txPower, 10);
    if (!Number.isNaN(n)) out.tx_power = n;
  }

  const gw = (document.getElementById('lan_gateway_ip').value || '').trim();
  if (gw) out.lan_gateway_ip = gw;

  const dhcpStart = (document.getElementById('dhcp_start_ip').value || '').trim();
  if (dhcpStart) out.dhcp_start_ip = dhcpStart;

  const dhcpEnd = (document.getElementById('dhcp_end_ip').value || '').trim();
  if (dhcpEnd) out.dhcp_end_ip = dhcpEnd;

  const dhcpDns = (document.getElementById('dhcp_dns').value || '').trim();
  if (dhcpDns) out.dhcp_dns = dhcpDns;

  out.cpu_affinity = (document.getElementById('cpu_affinity').value || '').trim();
  out.irq_affinity = (document.getElementById('irq_affinity').value || '').trim();

  out.bridge_name = (document.getElementById('bridge_name').value || '').trim();
  out.bridge_uplink = (document.getElementById('bridge_uplink').value || '').trim();

  // Only send passphrase if user typed a new one.
  const pw = (document.getElementById('wpa2_passphrase').value || '').trim();
  if (pw) out.wpa2_passphrase = pw;

  return out;
}

function applyConfig(cfg){
  lastCfg = cfg || {};

  // Do not overwrite unsaved edits from polling.
  if (cfgDirty && !cfgJustSaved) return;

  document.getElementById('ssid').value = cfg.ssid || '';
  document.getElementById('band_preference').value = cfg.band_preference || '5ghz';

  // Security
  document.getElementById('ap_security').value = (cfg.ap_security || 'wpa2');

  // Country
  document.getElementById('country').value = (cfg.country || 'US').toString().toUpperCase();
  syncCountrySelectFromInput();

  document.getElementById('optimized_no_virt').checked = !!cfg.optimized_no_virt;
  document.getElementById('ap_ready_timeout_s').value = (cfg.ap_ready_timeout_s ?? 6.0);
  document.getElementById('fallback_channel_2g').value = (cfg.fallback_channel_2g ?? 6);
  if (document.getElementById('channel_width')) {
    document.getElementById('channel_width').value = (cfg.channel_width || 'auto');
  }
  if (document.getElementById('beacon_interval')) {
    document.getElementById('beacon_interval').value = (cfg.beacon_interval ?? 50);
  }
  if (document.getElementById('dtim_period')) {
    document.getElementById('dtim_period').value = (cfg.dtim_period ?? 1);
  }
  if (document.getElementById('short_guard_interval')) {
    document.getElementById('short_guard_interval').checked = (cfg.short_guard_interval !== false);
  }
  if (document.getElementById('tx_power')) {
    document.getElementById('tx_power').value = (cfg.tx_power ?? '');
  }
  if (document.getElementById('channel_auto_select')) {
    document.getElementById('channel_auto_select').checked = !!cfg.channel_auto_select;
  }
  document.getElementById('lan_gateway_ip').value = (cfg.lan_gateway_ip || '192.168.68.1');
  document.getElementById('dhcp_start_ip').value = (cfg.dhcp_start_ip || '192.168.68.10');
  document.getElementById('dhcp_end_ip').value = (cfg.dhcp_end_ip || '192.168.68.250');
  document.getElementById('dhcp_dns').value = (cfg.dhcp_dns || 'gateway');
  document.getElementById('enable_internet').checked = (cfg.enable_internet !== false);
  document.getElementById('wifi_power_save_disable').checked = !!cfg.wifi_power_save_disable;
  document.getElementById('usb_autosuspend_disable').checked = !!cfg.usb_autosuspend_disable;
  document.getElementById('cpu_governor_performance').checked = !!cfg.cpu_governor_performance;
  document.getElementById('sysctl_tuning').checked = !!cfg.sysctl_tuning;
  if (document.getElementById('irq_affinity')) {
    document.getElementById('irq_affinity').value = (cfg.irq_affinity || '');
  }
  if (document.getElementById('interrupt_coalescing')) {
    document.getElementById('interrupt_coalescing').checked = !!cfg.interrupt_coalescing;
  }
  if (document.getElementById('tcp_low_latency')) {
    document.getElementById('tcp_low_latency').checked = !!cfg.tcp_low_latency;
  }
  if (document.getElementById('memory_tuning')) {
    document.getElementById('memory_tuning').checked = !!cfg.memory_tuning;
  }
  if (document.getElementById('io_scheduler_optimize')) {
    document.getElementById('io_scheduler_optimize').checked = !!cfg.io_scheduler_optimize;
  }
  document.getElementById('telemetry_enable').checked = (cfg.telemetry_enable !== false);
  document.getElementById('telemetry_interval_s').value = (cfg.telemetry_interval_s ?? 2.0);
  document.getElementById('watchdog_enable').checked = (cfg.watchdog_enable !== false);
  document.getElementById('watchdog_interval_s').value = (cfg.watchdog_interval_s ?? 2.0);
  if (document.getElementById('connection_quality_monitoring')) {
    document.getElementById('connection_quality_monitoring').checked = (cfg.connection_quality_monitoring !== false);
  }
  if (document.getElementById('auto_channel_switch')) {
    document.getElementById('auto_channel_switch').checked = !!cfg.auto_channel_switch;
  }
  document.getElementById('qos_preset').value = (cfg.qos_preset || 'off');
  document.getElementById('nat_accel').checked = !!cfg.nat_accel;
  document.getElementById('bridge_mode').checked = !!cfg.bridge_mode;
  document.getElementById('bridge_name').value = (cfg.bridge_name || 'vrbr0');
  document.getElementById('bridge_uplink').value = (cfg.bridge_uplink || '');
  document.getElementById('cpu_affinity').value = (cfg.cpu_affinity || '');
  document.getElementById('firewalld_enabled').checked = !!cfg.firewalld_enabled;
  document.getElementById('firewalld_enable_masquerade').checked = !!cfg.firewalld_enable_masquerade;
  document.getElementById('firewalld_enable_forward').checked = !!cfg.firewalld_enable_forward;
  document.getElementById('firewalld_cleanup_on_stop').checked = !!cfg.firewalld_cleanup_on_stop;
  document.getElementById('debug').checked = !!cfg.debug;

  document.getElementById('channel_6g').value = (cfg.channel_6g ?? '');

  if (cfg.ap_adapter){
    document.getElementById('ap_adapter').value = cfg.ap_adapter;
  }

  const passHint = document.getElementById('passHint');
  passHint.textContent = cfg._wpa2_passphrase_redacted ? 'Saved passphrase is hidden' : '';

  cfgJustSaved = false;

  enforceBandRules();
}

async function loadAdapters(){
  const r = await api('/v1/adapters');
  const el = document.getElementById('ap_adapter');
  if (!r.ok || !r.json) return;

  const data = r.json.data || {};
  lastAdapters = data;

  const rec = data.recommended || '';
  const list = data.adapters || [];

  // Preserve current selection if possible.
  const current = el.value;

  el.innerHTML = '';
  for (const a of list){
    const opt = document.createElement('option');
    opt.value = a.ifname;

    const ap = a.supports_ap ? 'AP' : 'no-AP';
    const caps = capsLabel(a);
    const reg = a.regdom && a.regdom.country ? a.regdom.country : '--';
    const star = (a.ifname === rec) ? '* ' : '';

    opt.textContent = `${star}${a.ifname} (${a.phy || 'phy?'}, ${caps}, reg=${reg}, score=${a.score}, ${ap})`;
    el.appendChild(opt);
  }
  el.dataset.recommended = rec;

  const trySet = (v) => {
    if (!v) return false;
    for (const opt of el.options){
      if (opt.value === v){ el.value = v; return true; }
    }
    return false;
  };

  if (!trySet(current)){
    if (lastCfg && lastCfg.ap_adapter) trySet(lastCfg.ap_adapter);
  }

  // After loading adapters, enforce band rules that may auto-pick.
  enforceBandRules();
  maybeAutoPickAdapterForBand();
}

async function refresh(){
  const privacy = document.getElementById('privacyMode').checked;
  const stPath = privacy ? '/v1/status' : '/v1/status?include_logs=1';

  const [st, cfg] = await Promise.all([api(stPath), api('/v1/config')]);

  if (cfg.ok && cfg.json){
    applyConfig(cfg.json.data || {});
  }

  if (!st.ok || !st.json){
    if (st.status === 401){
      setMsg('Unauthorized: paste API token and try again.', 'dangerText');
    }else{
      setMsg(st.json ? (st.json.result_code || 'error') : `Failed: HTTP ${st.status}`, 'dangerText');
    }
    return;
  }

  const s = st.json.data || {};
  setPill(s);

  document.getElementById('statusMeta').textContent =
    `last_op=${s.last_op || '--'} | ${fmtTs(s.last_op_ts)} | cid=${s.last_correlation_id || '--'}`;

  document.getElementById('rawStatus').textContent = JSON.stringify(st.json, null, 2);

  const eng = (s.engine || {});
  const out = (eng.stdout_tail || []).join('\n');
  const err = (eng.stderr_tail || []).join('\n');
  document.getElementById('stdout').textContent = privacy ? '(hidden)' : (out || '(empty)');
  document.getElementById('stderr').textContent = privacy ? '(hidden)' : (err || '(empty)');

  renderTelemetry(s.telemetry);
}

let refreshTimer = null;
function applyAutoRefresh(){
  const enabled = document.getElementById('autoRefresh').checked;
  const every = parseInt(document.getElementById('refreshEvery').value || '2000', 10);

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;

  if (enabled) refreshTimer = setInterval(refresh, every);

  STORE.setItem(LS.auto, enabled ? '1' : '0');
  STORE.setItem(LS.every, String(every));
}

function applyPrivacyMode(){
  const v = document.getElementById('privacyMode').checked;
  const tokenEl = document.getElementById('apiToken');
  if (tokenEl) tokenEl.type = v ? 'password' : 'text';
}

document.getElementById('btnRefresh').addEventListener('click', refresh);

document.getElementById('btnReloadAdapters').addEventListener('click', async () => {
  await loadAdapters();
  await refresh();
});

document.getElementById('btnUseRecommended').addEventListener('click', async () => {
  const sel = document.getElementById('ap_adapter');
  if (!sel.dataset.recommended) await loadAdapters();
  const rec = sel.dataset.recommended || '';
  if (rec){
    sel.value = rec;
    setDirty(true);
  }
});

document.getElementById('showPass').addEventListener('change', (e) => {
  document.getElementById('wpa2_passphrase').type = e.target.checked ? 'text' : 'password';
});

document.getElementById('privacyMode').addEventListener('change', () => {
  const v = document.getElementById('privacyMode').checked;
  STORE.setItem(LS.privacy, v ? '1' : '0');
  applyPrivacyMode();
  refresh();
});

document.getElementById('apiToken').addEventListener('input', (e) => {
  setToken(e.target.value.trim());
});
document.getElementById('apiToken').addEventListener('change', (e) => {
  setToken(e.target.value.trim());
});
document.getElementById('apiToken').addEventListener('blur', (e) => {
  setToken(e.target.value.trim());
});

document.getElementById('autoRefresh').addEventListener('change', applyAutoRefresh);
document.getElementById('refreshEvery').addEventListener('change', applyAutoRefresh);

document.getElementById('btnStart').addEventListener('click', async () => {
  setMsg('Starting...');
  const r = await api('/v1/start', {method:'POST'});
  setMsg(r.json ? ('Start: ' + r.json.result_code) : ('Start failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnStartOverrides').addEventListener('click', async () => {
  const overrides = getForm();
  setMsg('Starting (use form)...');
  const r = await api('/v1/start', {method:'POST', body: JSON.stringify({overrides})});
  setMsg(r.json ? ('Start: ' + r.json.result_code) : ('Start failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnStop').addEventListener('click', async () => {
  setMsg('Stopping...');
  const r = await api('/v1/stop', {method:'POST'});
  setMsg(r.json ? ('Stop: ' + r.json.result_code) : ('Stop failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnRepair').addEventListener('click', async () => {
  setMsg('Repairing...');
  const r = await api('/v1/repair', {method:'POST'});
  setMsg(r.json ? ('Repair: ' + r.json.result_code) : ('Repair failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnRestart').addEventListener('click', async () => {
  setMsg('Restarting...');
  const r = await api('/v1/restart', {method:'POST'});
  setMsg(r.json ? ('Restart: ' + r.json.result_code) : ('Restart failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnSaveConfig').addEventListener('click', async () => {
  const cfg = getForm();
  setMsg('Saving config...');
  const r = await api('/v1/config', {method:'POST', body: JSON.stringify(cfg)});
  setMsg(r.json ? ('Config: ' + r.json.result_code) : ('Config save failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');

  if (r.ok){
    setDirty(false);
    cfgJustSaved = true;
    document.getElementById('wpa2_passphrase').value = '';
  }
  await refresh();
});

document.getElementById('btnApplyVrProfile').addEventListener('click', () => {
  applyVrProfile('balanced');
  setMsg('Balanced VR profile applied (not saved).');
});
document.getElementById('btnApplyVrProfileUltra').addEventListener('click', () => {
  applyVrProfile('ultra_low_latency');
  setMsg('Ultra Low Latency VR profile applied (not saved).');
});
document.getElementById('btnApplyVrProfileHigh').addEventListener('click', () => {
  applyVrProfile('high_throughput');
  setMsg('High Throughput VR profile applied (not saved).');
});
document.getElementById('btnApplyVrProfileStable').addEventListener('click', () => {
  applyVrProfile('stability');
  setMsg('Stability VR profile applied (not saved).');
});

document.getElementById('btnSaveRestart').addEventListener('click', async () => {
  const cfg = getForm();
  setMsg('Saving & restarting...');
  const r1 = await api('/v1/config', {method:'POST', body: JSON.stringify(cfg)});
  if (!r1.ok){
    setMsg(r1.json ? ('Config: ' + r1.json.result_code) : ('Config save failed: HTTP ' + r1.status), 'dangerText');
    return;
  }

  setDirty(false);
  cfgJustSaved = true;
  document.getElementById('wpa2_passphrase').value = '';

  const r2 = await api('/v1/restart', {method:'POST'});
  setMsg(r2.json ? ('Save & Restart: ' + r2.json.result_code) : ('Restart failed: HTTP ' + r2.status), r2.ok ? '' : 'dangerText');
  await refresh();
});

(function init(){
  const tok = STORE.getItem(LS.token) || '';
  if (tok) document.getElementById('apiToken').value = tok;

  const privacy = (STORE.getItem(LS.privacy) || '1') === '1';
  document.getElementById('privacyMode').checked = privacy;
  applyPrivacyMode();

  const auto = (STORE.getItem(LS.auto) || '0') === '1';
  document.getElementById('autoRefresh').checked = auto;

  const every = STORE.getItem(LS.every) || '2000';
  document.getElementById('refreshEvery').value = every;

  wireDirtyTracking();
  enforceBandRules();

  // Load adapters first so the adapter select is populated before applying config.
  loadAdapters().then(refresh).then(applyAutoRefresh);
})();
