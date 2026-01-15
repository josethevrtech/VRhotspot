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

/** @typedef {"basic"|"advanced"} UiMode */
const UI_MODE_KEY = 'vrhs_ui_mode';
const FIELD_VISIBILITY = (window.UI_FIELD_VISIBILITY || {});

const ADVANCED_DEFAULTS = {
  channel_6g: null,
  channel_width: 'auto',
  beacon_interval: 50,
  dtim_period: 1,
  short_guard_interval: true,
  tx_power: null,
  channel_auto_select: false,
  ap_ready_timeout_s: 6.0,
  fallback_channel_2g: 6,
  optimized_no_virt: false,
  lan_gateway_ip: '192.168.68.1',
  dhcp_start_ip: '192.168.68.10',
  dhcp_end_ip: '192.168.68.250',
  dhcp_dns: 'gateway',
  wifi_power_save_disable: false,
  usb_autosuspend_disable: false,
  cpu_governor_performance: false,
  sysctl_tuning: false,
  interrupt_coalescing: false,
  tcp_low_latency: false,
  memory_tuning: false,
  io_scheduler_optimize: false,
  cpu_affinity: '',
  irq_affinity: '',
  telemetry_enable: true,
  telemetry_interval_s: 2.0,
  watchdog_enable: true,
  watchdog_interval_s: 2.0,
  connection_quality_monitoring: true,
  auto_channel_switch: false,
  qos_preset: 'off',
  nat_accel: false,
  bridge_mode: false,
  bridge_name: 'vrbr0',
  bridge_uplink: '',
  firewalld_enabled: true,
  firewalld_enable_masquerade: true,
  firewalld_enable_forward: true,
  firewalld_cleanup_on_stop: true,
  firewalld_zone: 'trusted',
  debug: false
};
const ADVANCED_KEYS = Object.keys(FIELD_VISIBILITY).filter((key) => FIELD_VISIBILITY[key] === 'advanced');
const ADVANCED_KEYS_FALLBACK = ADVANCED_KEYS.length ? ADVANCED_KEYS : Object.keys(ADVANCED_DEFAULTS);
const BASIC_QUICK_FIELDS = ['ap_adapter', 'band_preference', 'ap_security', 'country', 'enable_internet', 'qos_preset'];
const BASIC_CONNECT_FIELDS = ['ssid', 'wpa2_passphrase'];
const BASIC_FIELD_KEYS = Object.keys(FIELD_VISIBILITY).filter((key) => FIELD_VISIBILITY[key] === 'basic');
const BASIC_FIELD_KEYS_FALLBACK = BASIC_FIELD_KEYS.length ? BASIC_FIELD_KEYS : BASIC_QUICK_FIELDS.concat(BASIC_CONNECT_FIELDS);
const FIELD_HOMES = new Map();
let bandOptionsCache = null;

function readUiMode(){
  const raw = (STORE.getItem(UI_MODE_KEY) || '').trim().toLowerCase();
  return raw === 'advanced' ? 'advanced' : 'basic';
}

function loadUiMode(){
  return readUiMode();
}

function writeUiMode(mode){
  try{ STORE.setItem(UI_MODE_KEY, mode); }catch{}
}

function useUiMode(){
  /** @type {UiMode} */
  let mode = readUiMode();
  function setMode(next){
    mode = (next === 'advanced') ? 'advanced' : 'basic';
    writeUiMode(mode);
    applyUiMode(mode);
  }
  return { getMode: () => mode, setMode };
}

function getUiMode(){
  return uiModeState.getMode();
}

const uiModeState = useUiMode();

function getFieldElement(key){
  return document.querySelector(`[data-field="${key}"]`);
}

function rememberFieldHome(el){
  if (!el || FIELD_HOMES.has(el)) return;
  FIELD_HOMES.set(el, { parent: el.parentNode, next: el.nextSibling });
}

function moveFieldToContainer(key, container){
  const el = getFieldElement(key);
  if (!el || !container) return;
  rememberFieldHome(el);
  container.appendChild(el);
}

function restoreFieldToHome(key){
  const el = getFieldElement(key);
  if (!el) return;
  const home = FIELD_HOMES.get(el);
  if (!home || !home.parent) return;
  if (home.next && home.next.parentNode === home.parent){
    home.parent.insertBefore(el, home.next);
  }else{
    home.parent.appendChild(el);
  }
}

function applyBasicLayout(mode){
  const quick = document.getElementById('basicQuickFields');
  const connect = document.getElementById('basicConnectFields');
  if (!quick || !connect) return;
  if (mode === 'basic'){
    for (const key of BASIC_QUICK_FIELDS) moveFieldToContainer(key, quick);
    for (const key of BASIC_CONNECT_FIELDS) moveFieldToContainer(key, connect);
  }else{
    const all = BASIC_QUICK_FIELDS.concat(BASIC_CONNECT_FIELDS);
    for (const key of all.slice().reverse()) restoreFieldToHome(key);
  }
}

function getAdapterByIfname(ifname){
  if (!lastAdapters || !Array.isArray(lastAdapters.adapters)) return null;
  for (const a of lastAdapters.adapters){
    if (a.ifname === ifname) return a;
  }
  return null;
}

function getSelectedAdapter(){
  const sel = document.getElementById('ap_adapter');
  if (!sel) return null;
  return getAdapterByIfname(sel.value);
}

function getRecommendedBand(adapter){
  if (adapter){
    if (adapter.supports_5ghz) return '5ghz';
    if (adapter.supports_6ghz) return '6ghz';
    if (adapter.supports_2ghz) return '2.4ghz';
  }
  return '5ghz';
}

function formatBandLabel(band){
  if (band === '6ghz') return '6 GHz';
  if (band === '5ghz') return '5 GHz';
  if (band === '2.4ghz') return '2.4 GHz';
  return band;
}

function resolveBandPref(raw){
  if (raw === 'recommended') return getRecommendedBand(getSelectedAdapter());
  return raw;
}

function cacheBandOptions(select){
  if (bandOptionsCache) return;
  bandOptionsCache = [];
  for (const opt of select.options){
    bandOptionsCache.push({ value: opt.value, label: opt.textContent });
  }
}

function setBandOptions(select, options, value){
  select.innerHTML = '';
  for (const optDef of options){
    const opt = document.createElement('option');
    opt.value = optDef.value;
    opt.textContent = optDef.label;
    select.appendChild(opt);
  }
  if (value) select.value = value;
}

function updateBandOptions(){
  const sel = document.getElementById('band_preference');
  if (!sel) return;

  cacheBandOptions(sel);
  if (getUiMode() !== 'basic'){
    const current = sel.value === 'recommended' ? getRecommendedBand(getSelectedAdapter()) : sel.value;
    setBandOptions(sel, bandOptionsCache, current || '5ghz');
    return;
  }

  const adapter = getSelectedAdapter();
  const recommended = getRecommendedBand(adapter);
  const supports6 = adapter ? !!adapter.supports_6ghz : false;
  const options = [
    { value: 'recommended', label: `Recommended (${formatBandLabel(recommended)})` },
    { value: '2.4ghz', label: '2.4 GHz' },
    { value: '5ghz', label: '5 GHz' },
  ];
  if (supports6) options.push({ value: '6ghz', label: '6 GHz (Wi-Fi 6E)' });

  let nextValue = sel.value || 'recommended';
  if (nextValue === '6ghz' && !supports6) nextValue = 'recommended';
  if (!options.some((opt) => opt.value === nextValue)) nextValue = 'recommended';
  setBandOptions(sel, options, nextValue);
}

// --- Sticky edit guard
let cfgDirty = false;
let cfgJustSaved = false;
let passphraseDirty = false;
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
  const text = cfgDirty ? 'Unsaved changes' : '';
  const dirtyEls = [document.getElementById('dirty'), document.getElementById('dirtyBasic')];
  for (const el of dirtyEls){
    if (el) el.textContent = text;
  }
}

function markDirty(ev){
  if (ev && ev.isTrusted === false) return;
  if (!cfgDirty) setDirty(true);
}

function markPassphraseDirty(ev){
  if (ev && ev.isTrusted === false) return;
  passphraseDirty = true;
}

function resetPassphraseUi(cfg){
  const passEl = document.getElementById('wpa2_passphrase');
  if (!passEl) return;
  const hasSaved = !!(cfg && cfg.wpa2_passphrase_set);
  passEl.type = 'password';
  passEl.value = '';
  passEl.placeholder = hasSaved ? 'Type new passphrase to change (currently saved)' : 'Type a new passphrase to set it';
  passEl.readOnly = false;
  const passHint = document.getElementById('passHint');
  if (passHint){
    if (hasSaved){
      let hint = 'Passphrase is saved';
      if (Number.isInteger(cfg.wpa2_passphrase_len)){
        hint = `Passphrase saved (${cfg.wpa2_passphrase_len} chars). Type to change.`;
      }
      passHint.textContent = hint;
    }else{
      passHint.textContent = '';
    }
  }
  passphraseDirty = false;
}

function _coerceDefault(v, def){
  if (def === null) return (v === undefined || v === null || v === '') ? null : v;
  if (typeof def === 'number'){
    const n = Number(v);
    return Number.isNaN(n) ? v : n;
  }
  if (typeof def === 'boolean') return !!v;
  if (typeof def === 'string') return String(v ?? '');
  return v;
}

function hasAdvancedValues(cfg){
  if (!cfg) return false;
  for (const key of ADVANCED_KEYS_FALLBACK){
    if (!(key in cfg)) continue;
    const hasDefault = Object.prototype.hasOwnProperty.call(ADVANCED_DEFAULTS, key);
    const def = hasDefault ? ADVANCED_DEFAULTS[key] : undefined;
    if (!hasDefault){
      const cur = cfg[key];
      if (cur !== undefined && cur !== null && cur !== '') return true;
      continue;
    }
    const cur = _coerceDefault(cfg[key], def);
    const normalizedDef = _coerceDefault(def, def);
    if (cur !== normalizedDef) return true;
  }
  return false;
}

function updateBasicInfoBanner(){
  const banner = document.getElementById('basicInfoBanner');
  if (!banner) return;
  const show = (getUiMode() === 'basic') && hasAdvancedValues(lastCfg);
  banner.style.display = show ? 'block' : 'none';
}

function parseIntMaybe(value){
  if (value === undefined || value === null || value === '') return null;
  const n = parseInt(value, 10);
  return Number.isNaN(n) ? null : n;
}

function isValid2gChannel(channel){
  return Number.isInteger(channel) && channel >= 1 && channel <= 14;
}

function getBasicChannelFix(){
  if (getUiMode() !== 'basic') return null;
  if (!lastCfg) return null;
  const band = resolveBandPref(document.getElementById('band_preference').value);
  if (band !== '2.4ghz') return null;
  const fallbackRaw = lastCfg.fallback_channel_2g;
  const fallback = parseIntMaybe(fallbackRaw);
  if (isValid2gChannel(fallback)) return null;
  const sanitized = 6;
  return {
    fallback: sanitized,
    overrides: {
      channel_auto_select: true,
      fallback_channel_2g: sanitized,
    },
  };
}

function updateBasicChannelBanner(){
  const banner = document.getElementById('basicChannelBanner');
  if (!banner) return;
  const fix = getBasicChannelFix();
  if (!fix){
    banner.style.display = 'none';
    banner.textContent = '';
    return;
  }
  banner.textContent =
    `Basic mode auto-fix: 2.4 GHz requires channels 1–14. ` +
    `Starting with auto channel select (fallback ${fix.fallback}).`;
  banner.style.display = 'block';
}

const QOS_ALLOWED = new Set(['off', 'vr', 'balanced', 'ultra_low_latency', 'high_throughput']);
const QOS_BASIC_VALUES = new Set(['ultra_low_latency', 'high_throughput', 'balanced', 'vr']);
let currentQosPreset = 'vr';

function setQoS(value, opts = {}){
  const select = document.getElementById('qos_preset');
  const radios = document.querySelectorAll('input[name="qos_basic"]');
  if (!select && !radios.length) return;
  const raw = (value || '').toString().trim().toLowerCase();
  const normalized = QOS_ALLOWED.has(raw) ? raw : 'off';
  const mode = (opts.mode === 'basic' || opts.mode === 'advanced') ? opts.mode : getUiMode();
  const next = (mode === 'basic' && !QOS_BASIC_VALUES.has(normalized)) ? 'vr' : normalized;
  currentQosPreset = next;

  if (select && select.value !== next) select.value = next;

  if (radios.length){
    let matched = false;
    for (const radio of radios){
      const shouldCheck = (radio.value === next);
      radio.checked = shouldCheck;
      if (shouldCheck) matched = true;
    }
    if (!matched){
      if (mode === 'basic' && QOS_BASIC_VALUES.has('vr')){
        for (const radio of radios){
          radio.checked = (radio.value === 'vr');
        }
      }else{
        for (const radio of radios){
          radio.checked = false;
        }
      }
    }
  }
}

function updateBasicQosBanner(opts = {}){
  const banner = document.getElementById('basicQosBanner');
  if (!banner) return;
  if (!lastCfg || !Object.prototype.hasOwnProperty.call(lastCfg, 'qos_preset')){
    banner.classList.remove('show');
    return;
  }
  const mode = getUiMode();
  if (mode !== 'basic') {
    banner.classList.remove('show');
    return;
  }
  
  const raw = (lastCfg && lastCfg.qos_preset !== undefined && lastCfg.qos_preset !== null)
    ? String(lastCfg.qos_preset).trim().toLowerCase()
    : '';
  
  // Check if the saved config value is in basic values
  const savedValue = QOS_ALLOWED.has(raw) ? raw : 'off';
  
  // Check if user has selected a basic value in the UI (currentQosPreset is updated by setQoS)
  const uiValue = currentQosPreset || 'vr';
  
  // Only show banner if:
  // 1. Saved value is not in basic values (i.e., it's "off")
  // 2. AND user hasn't selected a basic value in the UI yet
  const needs = !QOS_BASIC_VALUES.has(savedValue) && !QOS_BASIC_VALUES.has(uiValue);
  
  if (needs){
    // Update banner text based on saved value
    const displayValue = savedValue === 'off' ? 'Off' : savedValue;
    banner.textContent = `QoS is currently ${displayValue} in Advanced settings. Basic defaults to Stability. Click Save to apply.`;
    banner.classList.add('show');
    if (opts.markDirty !== false) markDirty();
  }else{
    banner.classList.remove('show');
  }
}

function wireQosBasic(){
  const radios = document.querySelectorAll('input[name="qos_basic"]');
  if (radios.length){
    for (const radio of radios){
      radio.addEventListener('change', () => {
        setQoS(radio.value);
        markDirty();
        updateBasicQosBanner();
      });
    }
  }
  const select = document.getElementById('qos_preset');
  if (select){
    select.addEventListener('change', () => {
      setQoS(select.value);
      updateBasicQosBanner();
    });
  }
}

function applyUiMode(mode){
  document.body.dataset.uiMode = mode;
  const toggle = document.getElementById('uiModeToggle');
  const label = document.getElementById('uiModeLabel');
  if (toggle) toggle.checked = (mode === 'advanced');
  if (label) label.textContent = (mode === 'advanced') ? 'Advanced' : 'Basic';
  applyBasicLayout(mode);
  updateBandOptions();
  enforceBandRules();
  applyFieldVisibility(mode);
  setQoS(currentQosPreset, {mode});
  updateBasicInfoBanner();
  updateBasicChannelBanner();
  updateBasicQosBanner();
}

function filterConfigForMode(out){
  if (getUiMode() !== 'basic') return out;
  return pickBasicFields(out);
}

function pickBasicFields(cfg){
  const out = {};
  if (!cfg) return out;
  const keys = BASIC_FIELD_KEYS_FALLBACK.includes('qos_preset')
    ? BASIC_FIELD_KEYS_FALLBACK
    : BASIC_FIELD_KEYS_FALLBACK.concat('qos_preset');
  for (const key of keys){
    if (Object.prototype.hasOwnProperty.call(cfg, key)){
      const value = cfg[key];
      if (value !== undefined) out[key] = value;
    }
  }
  return out;
}

function getFieldMode(key){
  const mode = FIELD_VISIBILITY[key];
  return (mode === 'basic' || mode === 'advanced') ? mode : 'advanced';
}

function applyFieldVisibility(mode){
  const fields = document.querySelectorAll('[data-field]');
  for (const el of fields){
    const key = el.getAttribute('data-field') || '';
    const fieldMode = getFieldMode(key);
    const show = (mode === 'advanced') || (fieldMode === 'basic');
    const bandVisible = el.dataset.bandVisible;
    const finalShow = (bandVisible === '0') ? false : show;
    el.style.display = finalShow ? '' : 'none';
  }
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

  const passEl = document.getElementById('wpa2_passphrase');
  if (passEl){
    passEl.addEventListener('input', markPassphraseDirty);
    passEl.addEventListener('change', markPassphraseDirty);
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
    markDirty();
  });

  // Band/security coupling
  document.getElementById('band_preference').addEventListener('change', () => {
    enforceBandRules();
    maybeAutoPickAdapterForBand();
  });

  document.getElementById('ap_security').addEventListener('change', () => {
    enforceBandRules();
  });

  const apSel = document.getElementById('ap_adapter');
  apSel.addEventListener('change', () => {
    updateBandOptions();
    enforceBandRules();
  });
}

function cid(){ return 'ui-' + Date.now() + '-' + Math.random().toString(16).slice(2); }

function getToken(){
  const advEl = document.getElementById('apiToken');
  const basicEl = document.getElementById('apiTokenBasic');
  const adv = advEl ? (advEl.value || '').trim() : '';
  const basic = basicEl ? (basicEl.value || '').trim() : '';
  return basic || adv || ((STORE.getItem(LS.token) || '').trim());
}
function setToken(v){
  const val = (v || '').trim();
  try{ STORE.setItem(LS.token, val); }catch{}
  const advEl = document.getElementById('apiToken');
  const basicEl = document.getElementById('apiTokenBasic');
  if (advEl && advEl.value !== val) advEl.value = val;
  if (basicEl && basicEl.value !== val) basicEl.value = val;
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
  const els = [document.getElementById('msg'), document.getElementById('msgBasic')];
  for (const el of els){
    if (!el) continue;
    el.textContent = text || '';
    el.className = 'small mt-10' + (kind ? (' ' + kind) : '');
  }
}

async function startHotspot(overrides, label){
  const prefix = label ? `Starting (${label})...` : 'Starting...';
  setMsg(prefix);
  const opts = {method:'POST'};
  if (overrides) opts.body = JSON.stringify({overrides});
  const r = await api('/v1/start', opts);
  setMsg(r.json ? ('Start: ' + r.json.result_code) : ('Start failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
}

async function copyFieldValue(fieldId, label){
  const el = document.getElementById(fieldId);
  if (!el) return;
  const value = (el.value || '').toString().trim();
  if (!value){
    setMsg(`${label} is empty`, 'dangerText');
    return;
  }
  if (navigator.clipboard && window.isSecureContext){
    try{
      await navigator.clipboard.writeText(value);
      setMsg(`${label} copied`);
      return;
    }catch{}
  }
  try{
    const temp = document.createElement('textarea');
    temp.value = value;
    temp.style.position = 'fixed';
    temp.style.opacity = '0';
    document.body.appendChild(temp);
    temp.focus();
    temp.select();
    document.execCommand('copy');
    document.body.removeChild(temp);
    setMsg(`${label} copied`);
  }catch{
    setMsg(`Failed to copy ${label}`, 'dangerText');
  }
}

function setPill(state){
  const running = !!state.running;
  const phase = state.phase || '--';
  const adapter = state.adapter || '--';
  const band = state.band || '--';
  const mode = state.mode || '--';

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

  const statusText = (statusParts.length === 0) ? 'Loading...' : statusParts.join(' | ');

  const apply = (pill, txt) => {
    if (!pill || !txt) return;
    pill.classList.remove('ok','err');
    if (running) pill.classList.add('ok');
    else if (phase === 'error') pill.classList.add('err');
    pill.style.display = 'inline-flex';
    txt.textContent = statusText;
  };
  apply(document.getElementById('pill'), document.getElementById('pillTxt'));
  apply(document.getElementById('basicPill'), document.getElementById('basicPillTxt'));
}

function truncateText(text, maxLen){
  const raw = (text || '').toString();
  if (raw.length <= maxLen) return raw;
  return raw.slice(0, Math.max(0, maxLen - 3)) + '...';
}

function updateBasicStatusMeta(state){
  const adapter = state.adapter || '--';
  const band = state.band || '--';
  const metaEl = document.getElementById('basicStatusAdapterBand');
  if (metaEl) metaEl.textContent = `Adapter: ${adapter} | Band: ${band}`;

  const errEl = document.getElementById('basicLastError');
  if (!errEl) return;
  const err = state.last_error || (state.engine && state.engine.last_error) || '';
  if (err){
    errEl.textContent = `Last error: ${truncateText(err, 140)}`;
    errEl.style.display = '';
  }else{
    errEl.textContent = '';
    errEl.style.display = 'none';
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
  const rawBand = document.getElementById('band_preference').value;
  const band = resolveBandPref(rawBand);
  const secEl = document.getElementById('ap_security');
  const secHint = document.getElementById('secHint');
  const bandHint = document.getElementById('bandHint');
  const sixgBox = document.getElementById('sixgBox');
  if (sixgBox) sixgBox.dataset.bandVisible = (band === '6ghz') ? '1' : '0';

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
  applyFieldVisibility(getUiMode());
  updateBasicChannelBanner();
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
  const rawBand = document.getElementById('band_preference').value;
  const band = resolveBandPref(rawBand);
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
  setQoS(profile.qos_preset);
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
    band_preference: resolveBandPref(document.getElementById('band_preference').value),
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
    qos_preset: currentQosPreset,
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
  const passEl = document.getElementById('wpa2_passphrase');
  const pw = passEl ? (passEl.value || '').trim() : '';
  if (passphraseDirty && pw) out.wpa2_passphrase = pw;

  return filterConfigForMode(out);
}

function applyConfig(cfg){
  lastCfg = cfg || {};
  updateBasicQosBanner({markDirty: false});

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
  setQoS(cfg.qos_preset || 'off');
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

  updateBandOptions();

  resetPassphraseUi(cfg);

  cfgJustSaved = false;

  enforceBandRules();
  updateBasicInfoBanner();
  updateBasicChannelBanner();
  updateBasicQosBanner();
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
  const mode = getUiMode();
  for (const a of list){
    // Hide wlan0 in Basic Mode (known AP mode issues, prefer wlan1+)
    if (mode === 'basic' && a.ifname === 'wlan0') {
      continue;
    }

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

  let didSet = trySet(current);
  if (!didSet && lastCfg && lastCfg.ap_adapter) didSet = trySet(lastCfg.ap_adapter);
  if (!didSet && rec) trySet(rec);

  // After loading adapters, enforce band rules that may auto-pick.
  updateBandOptions();
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
  updateBasicStatusMeta(s);

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

  const basicAuto = document.getElementById('autoRefreshBasic');
  const basicEvery = document.getElementById('refreshEveryBasic');
  if (basicAuto) basicAuto.checked = enabled;
  if (basicEvery) basicEvery.value = String(every);
}

function applyPrivacyMode(){
  const adv = document.getElementById('privacyMode');
  const basic = document.getElementById('privacyModeBasic');
  const v = adv ? adv.checked : (basic ? basic.checked : true);
  if (adv) adv.checked = v;
  if (basic) basic.checked = v;
  const tokenEl = document.getElementById('apiToken');
  const tokenBasic = document.getElementById('apiTokenBasic');
  if (tokenEl) tokenEl.type = v ? 'password' : 'text';
  if (tokenBasic) tokenBasic.type = v ? 'password' : 'text';
}

document.getElementById('btnRefresh').addEventListener('click', refresh);
const btnRefreshBasic = document.getElementById('btnRefreshBasic');
if (btnRefreshBasic) btnRefreshBasic.addEventListener('click', refresh);

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
    updateBandOptions();
    enforceBandRules();
  }
});

const btnRevealPass = document.getElementById('btnRevealPass');
if (btnRevealPass) btnRevealPass.addEventListener('click', async () => {
  const passEl = document.getElementById('wpa2_passphrase');
  if (!passEl) return;
  btnRevealPass.disabled = true;
  setMsg('Revealing passphrase...');
  const r = await api('/v1/config/reveal_passphrase', {method:'POST', body: JSON.stringify({confirm: true})});
  if (r.ok && r.json && r.json.data && typeof r.json.data.wpa2_passphrase === 'string'){
    passEl.value = r.json.data.wpa2_passphrase;
    passEl.type = 'text';
    passphraseDirty = false;
    setMsg('Passphrase revealed');
  }else{
    const code = (r.json && r.json.result_code) ? r.json.result_code : `HTTP ${r.status}`;
    setMsg(`Reveal failed: ${code}`, 'dangerText');
  }
  btnRevealPass.disabled = false;
});

const btnCopySsid = document.getElementById('btnCopySsid');
if (btnCopySsid) btnCopySsid.addEventListener('click', () => copyFieldValue('ssid', 'SSID'));
const btnCopyPass = document.getElementById('btnCopyPass');
if (btnCopyPass) btnCopyPass.addEventListener('click', () => copyFieldValue('wpa2_passphrase', 'Passphrase'));
const btnSavePassBasic = document.getElementById('btnSavePassBasic');
if (btnSavePassBasic) btnSavePassBasic.addEventListener('click', async () => {
  const passField = document.getElementById('wpa2_passphrase');
  if (!passField || !passField.value.trim()) {
    showMessage('Enter a passphrase (8-63 characters)', 'copyHint');
    return;
  }
  passphraseDirty = true; // Force passphrase to be included in config
  await saveConfigOnly();
  showMessage('Passphrase saved to config', 'copyHint');
});

document.getElementById('privacyMode').addEventListener('change', () => {
  const v = document.getElementById('privacyMode').checked;
  STORE.setItem(LS.privacy, v ? '1' : '0');
  applyPrivacyMode();
  refresh();
});
const privacyBasic = document.getElementById('privacyModeBasic');
if (privacyBasic) privacyBasic.addEventListener('change', () => {
  const adv = document.getElementById('privacyMode');
  if (adv) adv.checked = privacyBasic.checked;
  STORE.setItem(LS.privacy, privacyBasic.checked ? '1' : '0');
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
const apiTokenBasic = document.getElementById('apiTokenBasic');
if (apiTokenBasic){
  apiTokenBasic.addEventListener('input', (e) => {
    setToken(e.target.value.trim());
  });
  apiTokenBasic.addEventListener('change', (e) => {
    setToken(e.target.value.trim());
  });
  apiTokenBasic.addEventListener('blur', (e) => {
    setToken(e.target.value.trim());
  });
}

const btnSaveTokenBasic = document.getElementById('btnSaveTokenBasic');
if (btnSaveTokenBasic) btnSaveTokenBasic.addEventListener('click', () => {
  const tokenField = document.getElementById('apiTokenBasic');
  if (tokenField) {
    setToken(tokenField.value.trim());
    setMsg('API token saved. Refreshing page...');
    setTimeout(() => {
      window.location.reload();
    }, 500);
  }
});

const btnSaveToken = document.getElementById('btnSaveToken');
if (btnSaveToken) btnSaveToken.addEventListener('click', () => {
  const tokenField = document.getElementById('apiToken');
  if (tokenField) {
    setToken(tokenField.value.trim());
    setMsg('API token saved to browser storage');
  }
});

document.getElementById('autoRefresh').addEventListener('change', applyAutoRefresh);
document.getElementById('refreshEvery').addEventListener('change', applyAutoRefresh);
const autoRefreshBasic = document.getElementById('autoRefreshBasic');
if (autoRefreshBasic) autoRefreshBasic.addEventListener('change', () => {
  document.getElementById('autoRefresh').checked = autoRefreshBasic.checked;
  applyAutoRefresh();
});
const refreshEveryBasic = document.getElementById('refreshEveryBasic');
if (refreshEveryBasic) refreshEveryBasic.addEventListener('change', () => {
  document.getElementById('refreshEvery').value = refreshEveryBasic.value;
  applyAutoRefresh();
});

function init(){
  const tok = STORE.getItem(LS.token) || '';
  if (tok) setToken(tok);

  const privacy = (STORE.getItem(LS.privacy) || '1') === '1';
  document.getElementById('privacyMode').checked = privacy;
  applyPrivacyMode();

  const auto = (STORE.getItem(LS.auto) || '0') === '1';
  document.getElementById('autoRefresh').checked = auto;

  const every = STORE.getItem(LS.every) || '2000';
  document.getElementById('refreshEvery').value = every;

  const mode = loadUiMode();
  applyUiMode(mode);

  // Wire up button listeners
  document.getElementById('btnStart').addEventListener('click', async () => {
    await startHotspot();
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
  const btnStartBasic = document.getElementById('btnStartBasic');
  if (btnStartBasic) btnStartBasic.addEventListener('click', async () => {
  const fix = getBasicChannelFix();
  if (fix) {
    await startHotspot(fix.overrides, 'auto-fix 2.4 GHz channel');
  } else {
    await startHotspot();
  }
});
const btnStopBasic = document.getElementById('btnStopBasic');
if (btnStopBasic) btnStopBasic.addEventListener('click', () => {
  document.getElementById('btnStop').click();
});
const btnRepairBasic = document.getElementById('btnRepairBasic');
if (btnRepairBasic) btnRepairBasic.addEventListener('click', () => {
  document.getElementById('btnRepair').click();
});

async function saveConfigOnly() {
  const cfg = getForm();
  setMsg('Saving config...');
  const r = await api('/v1/config', {method:'POST', body: JSON.stringify(cfg)});
  setMsg(r.json ? ('Config: ' + r.json.result_code) : ('Config save failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');

  if (r.ok){
    setDirty(false);
    cfgJustSaved = true;
    const passEl = document.getElementById('wpa2_passphrase');
    if (passEl){
      passEl.value = '';
      passEl.type = 'password';
    }
    passphraseDirty = false;
  }
  await refresh();
  return r;
}

document.getElementById('btnSaveConfig').addEventListener('click', async () => {
  await saveConfigOnly();
});
const btnSaveConfigBasic = document.getElementById('btnSaveConfigBasic');
if (btnSaveConfigBasic) btnSaveConfigBasic.addEventListener('click', () => {
  document.getElementById('btnSaveConfig').click();
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
  const passEl = document.getElementById('wpa2_passphrase');
  if (passEl){
    passEl.value = '';
    passEl.type = 'password';
  }
  passphraseDirty = false;

  const r2 = await api('/v1/restart', {method:'POST'});
  setMsg(r2.json ? ('Save & Restart: ' + r2.json.result_code) : ('Restart failed: HTTP ' + r2.status), r2.ok ? '' : 'dangerText');
  await refresh();
});
const btnSaveRestartBasic = document.getElementById('btnSaveRestartBasic');
if (btnSaveRestartBasic) btnSaveRestartBasic.addEventListener('click', () => {
  document.getElementById('btnSaveRestart').click();
});
  const modeToggle = document.getElementById('uiModeToggle');
  if (modeToggle) {
    modeToggle.addEventListener('change', () => {
      uiModeState.setMode(modeToggle.checked ? 'advanced' : 'basic');
    });
  }

  wireDirtyTracking();
  wireQosBasic();
  enforceBandRules();

  // Load adapters first so the adapter select is populated before applying config.
  loadAdapters().then(refresh).then(applyAutoRefresh);
}

if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', init);
}else{
  init();
}
