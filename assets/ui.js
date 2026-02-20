const BASE = '';
const STORE = (function () {
  try { localStorage.setItem('__t', '1'); localStorage.removeItem('__t'); return localStorage; } catch { return sessionStorage; }
})();
const TOKEN_KEY = 'vr_hotspot_token';
const LEGACY_TOKEN_KEY = 'vr_hotspot_api_token';
const DEBUG_TOKEN = false;
const LS = {
  token: TOKEN_KEY,
  privacy: 'vr_hotspot_privacy',
  showTelemetry: 'vr_hotspot_show_telemetry',
  auto: 'vr_hotspot_auto',
  every: 'vr_hotspot_every'
};

/** @typedef {"basic"|"advanced"} UiMode */
const UI_MODE_KEY = 'vrhs_ui_mode';
const FIELD_VISIBILITY = (window.UI_FIELD_VISIBILITY || {});

const ADVANCED_DEFAULTS = {
  channel_6g: null,
  channel_width: '80',
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
const BASIC_CONNECT_FIELDS = ['ssid'];
const BASIC_FIELD_KEYS = Object.keys(FIELD_VISIBILITY).filter((key) => FIELD_VISIBILITY[key] === 'basic');
const BASIC_FIELD_KEYS_FALLBACK = BASIC_FIELD_KEYS.length ? BASIC_FIELD_KEYS : BASIC_QUICK_FIELDS.concat(BASIC_CONNECT_FIELDS);
const FIELD_HOMES = new Map();
let bandOptionsCache = null;
const FLOATING_TIP_LAYER_ID = 'floatingTipLayer';
let floatingTipLayer = null;
let activeTipTarget = null;
let floatingTipWired = false;
let isAuthenticated = false;
let authFlowLocked = false;
let uiBootstrapped = false;
let refreshTimer = null;

function readUiMode() {
  const raw = (STORE.getItem(UI_MODE_KEY) || '').trim().toLowerCase();
  return raw === 'advanced' ? 'advanced' : 'basic';
}

function loadUiMode() {
  return readUiMode();
}

function writeUiMode(mode) {
  try { STORE.setItem(UI_MODE_KEY, mode); } catch { }
}

function useUiMode() {
  /** @type {UiMode} */
  let mode = readUiMode();
  function setMode(next) {
    mode = (next === 'advanced') ? 'advanced' : 'basic';
    writeUiMode(mode);
    applyUiMode(mode);
  }
  return { getMode: () => mode, setMode };
}

function getUiMode() {
  return uiModeState.getMode();
}

const uiModeState = useUiMode();

function getFieldElement(key) {
  return document.querySelector(`[data-field="${key}"]`);
}

function rememberFieldHome(el) {
  if (!el || FIELD_HOMES.has(el)) return;
  FIELD_HOMES.set(el, { parent: el.parentNode, next: el.nextSibling });
}

function moveFieldToContainer(key, container) {
  const el = getFieldElement(key);
  if (!el || !container) return;
  rememberFieldHome(el);
  container.appendChild(el);
}

function restoreFieldToHome(key) {
  const el = getFieldElement(key);
  if (!el) return;
  const home = FIELD_HOMES.get(el);
  if (!home || !home.parent) return;
  if (home.next && home.next.parentNode === home.parent) {
    home.parent.insertBefore(el, home.next);
  } else {
    home.parent.appendChild(el);
  }
}

function applyBasicLayout(mode) {
  const quick = document.getElementById('basicQuickFields');
  const connect = document.getElementById('basicConnectFields');
  if (!quick || !connect) return;
  if (mode === 'basic') {
    for (const key of BASIC_QUICK_FIELDS) moveFieldToContainer(key, quick);
    for (const key of BASIC_CONNECT_FIELDS) moveFieldToContainer(key, connect);
  } else {
    const all = BASIC_QUICK_FIELDS.concat(BASIC_CONNECT_FIELDS);
    for (const key of all.slice().reverse()) restoreFieldToHome(key);
  }
}

function getAdapterByIfname(ifname) {
  if (!lastAdapters || !Array.isArray(lastAdapters.adapters)) return null;
  for (const a of lastAdapters.adapters) {
    if (a.ifname === ifname) return a;
  }
  return null;
}

function getSelectedAdapter() {
  const sel = document.getElementById('ap_adapter');
  if (!sel) return null;
  return getAdapterByIfname(sel.value);
}

function getRecommendedBand(adapter) {
  if (adapter) {
    if (adapter.supports_5ghz) return '5ghz';
    if (adapter.supports_6ghz) return '6ghz';
    if (adapter.supports_2ghz) return '2.4ghz';
  }
  return '5ghz';
}

function formatBandLabel(band) {
  if (band === '6ghz') return '6 GHz';
  if (band === '5ghz') return '5 GHz';
  if (band === '2.4ghz') return '2.4 GHz';
  return band;
}

function normalizeBandValue(raw) {
  if (!raw) return null;
  const s = raw.toString().trim().toLowerCase();
  if (s === '2.4' || s === '2.4ghz' || s === '2ghz') return '2.4ghz';
  if (s === '5' || s === '5ghz' || s === '5g') return '5ghz';
  if (s === '6' || s === '6ghz' || s === '6g' || s === '6e') return '6ghz';
  return s;
}

function parseEngineCmd(cmd) {
  if (!Array.isArray(cmd)) return {};
  const joined = cmd.join(' ');
  const out = {};
  if (joined.includes('hostapd_nat_engine')) out.engine = 'hostapd_nat';
  else if (joined.includes('hostapd6_engine')) out.engine = 'hostapd6';
  else if (joined.includes('lnxrouter')) out.engine = 'lnxrouter';

  for (let i = 0; i < cmd.length; i++) {
    const arg = cmd[i];
    if (arg === '--ap-ifname') {
      out.apIfname = cmd[i + 1];
      i += 1;
      continue;
    }
    if (arg === '--ap') {
      out.apIfname = cmd[i + 1];
      i += 2; // skip iface + SSID
      continue;
    }
    if (arg === '--band' || arg === '--freq-band') {
      out.band = normalizeBandValue(cmd[i + 1]);
      i += 1;
      continue;
    }
    if (arg === '--channel' || arg === '-c') {
      out.channel = cmd[i + 1];
      i += 1;
      continue;
    }
    if (arg === '--channel-width') {
      out.channelWidth = cmd[i + 1];
      i += 1;
      continue;
    }
    if (arg === '--country') {
      out.country = cmd[i + 1];
      i += 1;
      continue;
    }
    if (arg === '--no-virt') {
      out.noVirt = true;
      continue;
    }
    if (arg === '--no-internet' || arg === '-n') {
      out.internet = false;
      continue;
    }
  }

  if (out.engine && out.noVirt === undefined) out.noVirt = false;
  if (out.engine && out.internet === undefined) out.internet = true;
  return out;
}

function formatEffectiveSummary(state) {
  const info = parseEngineCmd(state && state.engine ? state.engine.cmd : null);
  const parts = [];
  const adapter = state.adapter || '';
  if (adapter) parts.push(`Adapter: ${adapter}`);
  const band = state.band || info.band || '';
  if (band) parts.push(`Band: ${formatBandLabel(normalizeBandValue(band))}`);
  if (state.mode) parts.push(`Mode: ${state.mode}`);
  const ap = state.ap_interface || info.apIfname || '';
  if (ap) parts.push(`AP: ${ap}`);
  if (state.fallback_reason) parts.push(`Fallback: ${state.fallback_reason}`);
  return parts.join(' | ');
}

function resolveBandPref(raw) {
  if (raw === 'recommended') return getRecommendedBand(getSelectedAdapter());
  return raw;
}

function cacheBandOptions(select) {
  if (bandOptionsCache) return;
  bandOptionsCache = [];
  for (const opt of select.options) {
    bandOptionsCache.push({ value: opt.value, label: opt.textContent });
  }
}

function setBandOptions(select, options, value) {
  select.innerHTML = '';
  for (const optDef of options) {
    const opt = document.createElement('option');
    opt.value = optDef.value;
    opt.textContent = optDef.label;
    select.appendChild(opt);
  }
  if (value) select.value = value;
}

function updateBandOptions() {
  const sel = document.getElementById('band_preference');
  if (!sel) return;

  cacheBandOptions(sel);

  // === BASIC MODE: Enforce VR Minimums ===
  // Basic Mode requires 5GHz + 80MHz for VR streaming.
  // Only offer 5GHz to prevent users from selecting unsuitable bands.
  if (typeof getUiMode === 'function' && getUiMode() === 'basic') {
    const adapter = getSelectedAdapter();

    // Tri-state 5GHz support labeling
    let label;
    if (adapter && adapter.supports_5ghz === true) {
      label = '5 GHz (VR Required)';
    } else if (adapter && adapter.supports_5ghz === false) {
      label = '5 GHz (Required — adapter does not support 5 GHz; will fail)';
    } else {
      // supports_5ghz is undefined/missing
      label = '5 GHz (Required — capability unknown; will validate at start)';
    }

    // In Basic Mode, only 5GHz is allowed for VR
    const options = [
      { value: '5ghz', label: label },
    ];

    // Force 5GHz selection and lock the select in Basic Mode
    setBandOptions(sel, options, '5ghz');
    sel.value = '5ghz';
    sel.disabled = true; // Lock the selector in Basic Mode
    return;
  }

  // === ADVANCED MODE ===
  // Advanced Mode preserves legacy band options (including any HTML-provided entries).
  sel.disabled = false;
  const adapter = getSelectedAdapter();

  // Fallback if bandOptionsCache is empty or not an array
  let options = bandOptionsCache;
  if (!Array.isArray(options) || options.length === 0) {
    const supports6 = adapter ? !!adapter.supports_6ghz : false;
    const recommended = getRecommendedBand(adapter);
    options = [
      { value: 'recommended', label: `Recommended (${formatBandLabel(recommended)})` },
      { value: '2.4ghz', label: '2.4 GHz' },
      { value: '5ghz', label: '5 GHz' },
    ];
    if (supports6) {
      options.push({ value: '6ghz', label: '6 GHz (Wi-Fi 6E)' });
    }
  }

  const current = sel.value === 'recommended' ? getRecommendedBand(adapter) : sel.value;
  setBandOptions(sel, options, current || '5ghz');
}

// --- Sticky edit guard
let cfgDirty = false;
let cfgJustSaved = false;
let passphraseDirty = false;
let lastCfg = null;
let lastAdapters = null;
let lastStatus = null;

const CFG_IDS = [
  "ssid", "wpa2_passphrase", "band_preference", "ap_security", "channel_6g", "country", "country_sel",
  "optimized_no_virt", "ap_adapter", "ap_ready_timeout_s", "fallback_channel_2g",
  "channel_width", "beacon_interval", "dtim_period", "short_guard_interval", "tx_power", "channel_auto_select",
  "lan_gateway_ip", "dhcp_start_ip", "dhcp_end_ip", "dhcp_dns", "enable_internet",
  "wifi_power_save_disable", "usb_autosuspend_disable", "cpu_governor_performance", "cpu_affinity", "sysctl_tuning",
  "irq_affinity", "interrupt_coalescing", "tcp_low_latency", "memory_tuning", "io_scheduler_optimize",
  "telemetry_enable", "telemetry_interval_s", "watchdog_enable", "watchdog_interval_s",
  "connection_quality_monitoring", "auto_channel_switch",
  "qos_preset", "nat_accel", "bridge_mode", "bridge_name", "bridge_uplink",
  "firewalld_enabled", "firewalld_enable_masquerade", "firewalld_enable_forward", "firewalld_cleanup_on_stop",
  "debug"
];

function setDirty(v) {
  cfgDirty = !!v;
  const text = cfgDirty ? 'Unsaved changes' : '';
  const dirtyEls = [document.getElementById('dirty'), document.getElementById('dirtyBasic')];
  for (const el of dirtyEls) {
    if (el) el.textContent = text;
  }
}

function markDirty(ev) {
  if (ev && ev.isTrusted === false) return;
  if (!cfgDirty) setDirty(true);
}

function markPassphraseDirty(ev) {
  if (ev && ev.isTrusted === false) return;
  passphraseDirty = true;
}

function getPassphraseInputs() {
  return {
    advanced: document.getElementById('wpa2_passphrase'),
    basic: document.getElementById('wpa2_passphrase_basic'),
  };
}

function syncPassphraseInputs(sourceEl, ev) {
  if (!sourceEl) return;
  const { advanced, basic } = getPassphraseInputs();
  const value = sourceEl.value || '';
  if (sourceEl === advanced && basic && basic.value !== value) {
    basic.value = value;
  } else if (sourceEl === basic && advanced && advanced.value !== value) {
    advanced.value = value;
  }
  markPassphraseDirty(ev);
  markDirty(ev);
}

function getPassphraseValue() {
  const { advanced, basic } = getPassphraseInputs();
  const advVal = advanced ? (advanced.value || '').trim() : '';
  if (advVal) return advVal;
  const basicVal = basic ? (basic.value || '').trim() : '';
  return basicVal;
}

function clearPassphraseInputs() {
  const { advanced, basic } = getPassphraseInputs();
  if (advanced) {
    advanced.value = '';
    advanced.type = 'password';
  }
  if (basic) {
    basic.value = '';
    basic.type = 'password';
  }
}

function getEl(id) {
  return document.getElementById(id);
}

function getValueIf(id) {
  const el = getEl(id);
  return el ? el.value : undefined;
}

function getCheckedIf(id) {
  const el = getEl(id);
  return el ? el.checked : undefined;
}

function setValueIf(id, value) {
  const el = getEl(id);
  if (el) el.value = value;
}

function setCheckedIf(id, value) {
  const el = getEl(id);
  if (el) el.checked = !!value;
}

function resetPassphraseUi(cfg) {
  const { advanced: passEl, basic: passBasic } = getPassphraseInputs();
  if (!passEl && !passBasic) return;
  if (passphraseDirty) return; /* Do not overwrite user input */
  const hasSaved = !!(cfg && cfg.wpa2_passphrase_set);
  if (passEl) {
    passEl.type = 'password';
    passEl.value = '';
    passEl.placeholder = hasSaved ? 'Type new passphrase to change (currently saved)' : 'Type a new passphrase to set it';
    passEl.readOnly = false;
  }
  if (passBasic) {
    passBasic.type = 'password';
    passBasic.value = '';
    passBasic.placeholder = hasSaved ? 'Saved (tap eye to reveal)' : 'Enter a passphrase (8-63 characters)';
    passBasic.readOnly = false;
  }
  const passHint = document.getElementById('passHint');
  if (passHint) {
    if (hasSaved) {
      let hint = 'Passphrase is saved';
      if (Number.isInteger(cfg.wpa2_passphrase_len)) {
        hint = `Passphrase saved (${cfg.wpa2_passphrase_len} chars). Type to change.`;
      }
      passHint.textContent = hint;
    } else {
      passHint.textContent = '';
    }
  }
  const basicHint = document.getElementById('copyHint');
  if (basicHint) {
    basicHint.textContent = hasSaved ? 'Passphrase saved' : '';
    basicHint.style.color = '';
  }
  passphraseDirty = false;
}

function _coerceDefault(v, def) {
  if (def === null) return (v === undefined || v === null || v === '') ? null : v;
  if (typeof def === 'number') {
    const n = Number(v);
    return Number.isNaN(n) ? v : n;
  }
  if (typeof def === 'boolean') return !!v;
  if (typeof def === 'string') return String(v ?? '');
  return v;
}

function hasAdvancedValues(cfg) {
  if (!cfg) return false;
  for (const key of ADVANCED_KEYS_FALLBACK) {
    if (!(key in cfg)) continue;
    const hasDefault = Object.prototype.hasOwnProperty.call(ADVANCED_DEFAULTS, key);
    const def = hasDefault ? ADVANCED_DEFAULTS[key] : undefined;
    if (!hasDefault) {
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

function updateBasicInfoBanner() {
  const banner = document.getElementById('basicInfoBanner');
  if (!banner) return;
  const show = (getUiMode() === 'basic') && hasAdvancedValues(lastCfg);
  if (show) {
    banner.textContent = 'Advanced settings are currently saved. Basic mode only edits quick fields.';
  } else {
    banner.textContent = '';
  }
  banner.style.display = show ? 'block' : 'none';
}

function parseIntMaybe(value) {
  if (value === undefined || value === null || value === '') return null;
  const n = parseInt(value, 10);
  return Number.isNaN(n) ? null : n;
}

function isValid2gChannel(channel) {
  return Number.isInteger(channel) && channel >= 1 && channel <= 14;
}

function getBasicChannelFix() {
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

function updateBasicChannelBanner() {
  const banner = document.getElementById('basicChannelBanner');
  if (!banner) return;
  const fix = getBasicChannelFix();
  if (!fix) {
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
const QOS_BASIC_VALUES = new Set(['off', 'ultra_low_latency', 'vr']);
let currentQosPreset = 'vr';

function setQoS(value, opts = {}) {
  const select = document.getElementById('qos_preset');
  const radios = document.querySelectorAll('input[name="qos_basic"]');
  if (!select && !radios.length) return;
  const raw = (value || '').toString().trim().toLowerCase();
  const normalized = QOS_ALLOWED.has(raw) ? raw : 'off';
  const mode = (opts.mode === 'basic' || opts.mode === 'advanced') ? opts.mode : getUiMode();
  const next = (mode === 'basic' && !QOS_BASIC_VALUES.has(normalized)) ? 'vr' : normalized;
  currentQosPreset = next;

  if (select && select.value !== next) select.value = next;

  if (radios.length) {
    let matched = false;
    for (const radio of radios) {
      const shouldCheck = (radio.value === next);
      radio.checked = shouldCheck;
      if (shouldCheck) matched = true;
    }
    if (!matched) {
      if (mode === 'basic' && QOS_BASIC_VALUES.has('vr')) {
        for (const radio of radios) {
          radio.checked = (radio.value === 'vr');
        }
      } else {
        for (const radio of radios) {
          radio.checked = false;
        }
      }
    }
  }
}

function updateBasicQosBanner(opts = {}) {
  const banner = document.getElementById('basicQosBanner');
  if (!banner) return;
  if (!lastCfg || !Object.prototype.hasOwnProperty.call(lastCfg, 'qos_preset')) {
    banner.classList.remove('show');
    banner.textContent = '';
    return;
  }
  const mode = getUiMode();
  if (mode !== 'basic') {
    banner.classList.remove('show');
    banner.textContent = '';
    return;
  }

  const raw = (lastCfg && lastCfg.qos_preset !== undefined && lastCfg.qos_preset !== null)
    ? String(lastCfg.qos_preset).trim().toLowerCase()
    : '';

  const savedValue = QOS_ALLOWED.has(raw) ? raw : 'off';
  const uiValue = currentQosPreset || 'vr';
  const needs = !QOS_BASIC_VALUES.has(savedValue) && savedValue !== uiValue;

  if (needs) {
    const displayMap = {
      high_throughput: 'High Throughput',
      balanced: 'Balanced',
    };
    const displayValue = displayMap[savedValue] || savedValue;
    banner.textContent = `Saved QoS is ${displayValue}. Basic mode uses Speed/Stable/Off. Click Save to apply your Basic choice.`;
    banner.classList.add('show');
    if (opts.markDirty !== false) markDirty();
  } else {
    banner.classList.remove('show');
    banner.textContent = '';
  }
}

function wireQosBasic() {
  const radios = document.querySelectorAll('input[name="qos_basic"]');
  if (radios.length) {
    for (const radio of radios) {
      radio.addEventListener('change', () => {
        setQoS(radio.value);
        markDirty();
        updateBasicQosBanner();
      });
    }
  }
  const select = document.getElementById('qos_preset');
  if (select) {
    select.addEventListener('change', () => {
      setQoS(select.value);
      updateBasicQosBanner();
    });
  }
}

function applyUiMode(mode, opts = {}) {
  document.body.dataset.uiMode = mode;
  const toggle = document.getElementById('uiModeToggle');
  const label = document.getElementById('uiModeLabel');
  if (toggle) toggle.checked = (mode === 'advanced');
  if (label) label.textContent = (mode === 'advanced') ? 'Advanced' : 'Basic';

  // Update Adapter Label
  const adapterLbl = document.querySelector('label[for="ap_adapter"]');
  const adapterTip = document.getElementById('adapterLabelTip');
  if (adapterLbl) {
    adapterLbl.textContent = (mode === 'basic') ? 'USB WiFi Adapter' : 'AP adapter';
  }
  if (adapterTip) {
    const tipText = (mode === 'basic')
      ? 'Basic mode lists USB adapters only. Use Recommended to auto-select the best detected USB adapter.'
      : 'Select the interface that will run AP mode. Recommended chooses the best detected adapter.';
    renderHintTip(adapterTip, tipText);
  }

  // Reload adapters to apply filtering and renaming rules
  if (!opts.skipAdapters) loadAdapters();

  applyBasicLayout(mode);
  updateBandOptions();
  enforceBandRules();
  applyFieldVisibility(mode);
  setQoS(currentQosPreset, { mode });
  updateBasicInfoBanner();
  updateBasicChannelBanner();
  updateBasicQosBanner();

  // Telemetry Visibility
  const telCard = document.getElementById('cardTelemetry');
  if (telCard) {
    const show = (mode === 'advanced') || showTelemetryState;
    telCard.style.display = show ? '' : 'none';
  }
}

function filterConfigForMode(out) {
  if (getUiMode() !== 'basic') return out;
  return pickBasicFields(out);
}

function pickBasicFields(cfg) {
  const out = {};
  if (!cfg) return out;
  const keys = BASIC_FIELD_KEYS_FALLBACK.includes('qos_preset')
    ? BASIC_FIELD_KEYS_FALLBACK
    : BASIC_FIELD_KEYS_FALLBACK.concat('qos_preset');
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(cfg, key)) {
      const value = cfg[key];
      if (value !== undefined) out[key] = value;
    }
  }
  // Explicitly preserve passphrase if set (critical fix for Basic Mode)
  if (cfg.wpa2_passphrase !== undefined) {
    out.wpa2_passphrase = cfg.wpa2_passphrase;
  }
  return out;
}

function getFieldMode(key) {
  const mode = FIELD_VISIBILITY[key];
  return (mode === 'basic' || mode === 'advanced') ? mode : 'advanced';
}

function applyFieldVisibility(mode) {
  const fields = document.querySelectorAll('[data-field]');
  for (const el of fields) {
    const key = el.getAttribute('data-field') || '';
    const fieldMode = getFieldMode(key);
    const show = (mode === 'advanced') || (fieldMode === 'basic');
    const hideBandPreferenceInBasic = (mode === 'basic' && key === 'band_preference');
    const bandVisible = el.dataset.bandVisible;
    const finalShow = (bandVisible === '0') ? false : (show && !hideBandPreferenceInBasic);
    el.style.display = finalShow ? '' : 'none';
  }
}

function wireDirtyTracking() {
  for (const id of CFG_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener('pointerdown', markDirty);
    el.addEventListener('keydown', markDirty);
    el.addEventListener('change', markDirty);
    el.addEventListener('input', markDirty);
    el.addEventListener('click', markDirty);
  }

  const passEl = document.getElementById('wpa2_passphrase');
  if (passEl) {
    const onPass = (ev) => syncPassphraseInputs(passEl, ev);
    passEl.addEventListener('input', onPass);
    passEl.addEventListener('change', onPass);
  }
  const passBasic = document.getElementById('wpa2_passphrase_basic');
  if (passBasic) {
    const onPassBasic = (ev) => syncPassphraseInputs(passBasic, ev);
    passBasic.addEventListener('input', onPassBasic);
    passBasic.addEventListener('change', onPassBasic);
  }

  // Normalize country input to uppercase and 2 chars (or 00).
  const c = document.getElementById('country');
  c.addEventListener('input', (e) => {
    const raw = (e.target.value || '').toString().toUpperCase().replace(/[^A-Z0-9]/g, '');
    e.target.value = raw.slice(0, 2);
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

function cid() { return 'ui-' + Date.now() + '-' + Math.random().toString(16).slice(2); }

function getLocalStorageSafe() {
  try { return localStorage; } catch { return null; }
}

function getStoredToken() {
  const ls = getLocalStorageSafe();
  if (ls) return (ls.getItem(TOKEN_KEY) || '').trim();
  try { return (STORE.getItem(TOKEN_KEY) || '').trim(); } catch { return ''; }
}

function setStoredToken(token) {
  const val = (token || '').trim();
  const ls = getLocalStorageSafe();
  try {
    if (ls) {
      if (val) ls.setItem(TOKEN_KEY, val);
      else ls.removeItem(TOKEN_KEY);
    }
  } catch { /* ignore */ }
  if (!ls) {
    try {
      if (val) STORE.setItem(TOKEN_KEY, val);
      else STORE.removeItem(TOKEN_KEY);
    } catch { /* ignore */ }
  }
}

function migrateLegacyToken() {
  const current = getStoredToken();
  if (current) return current;
  const ls = getLocalStorageSafe();
  let legacy = '';
  if (ls) legacy = (ls.getItem(LEGACY_TOKEN_KEY) || '').trim();
  if (!legacy) {
    try { legacy = (STORE.getItem(LEGACY_TOKEN_KEY) || '').trim(); } catch { legacy = ''; }
  }
  if (legacy) {
    setStoredToken(legacy);
    try { if (ls) ls.removeItem(LEGACY_TOKEN_KEY); } catch { /* ignore */ }
    try { STORE.removeItem(LEGACY_TOKEN_KEY); } catch { /* ignore */ }
    return legacy;
  }
  return '';
}

function debugTokenLog(injected) {
  if (!DEBUG_TOKEN) return;
  const len = getStoredToken().length;
  try {
    console.log('[token]', { TOKEN_KEY, tokenLength: len, injected });
  } catch { /* ignore */ }
}

function getToken() {
  return getStoredToken();
}

function setToken(v) {
  const val = (v || '').trim();
  setStoredToken(val);
}

function setAuthState(state) {
  if (!document.body) return;
  document.body.setAttribute('data-auth-state', state);
}

function isUnauthorizedStatus(status) {
  return status === 401 || status === 403;
}

function clearStoredTokenEverywhere() {
  setStoredToken('');
  const ls = getLocalStorageSafe();
  try { if (ls) ls.removeItem(TOKEN_KEY); } catch { /* ignore */ }
  try { if (ls) ls.removeItem(LEGACY_TOKEN_KEY); } catch { /* ignore */ }
  try { STORE.removeItem(TOKEN_KEY); } catch { /* ignore */ }
  try { STORE.removeItem(LEGACY_TOKEN_KEY); } catch { /* ignore */ }
}

function clearLoggedOutRouteState() {
  if (!window.location.hash) return;
  const cleanUrl = window.location.pathname + window.location.search;
  if (window.history && typeof window.history.replaceState === 'function') {
    window.history.replaceState(null, '', cleanUrl);
  } else {
    window.location.hash = '';
  }
}

function setLoginError(text = '') {
  const el = document.getElementById('loginError');
  if (el) el.textContent = text;
}

function renderLoginSplash(errorText = '', opts = {}) {
  const keepInput = !!opts.keepInput;
  const input = document.getElementById('loginToken');
  const submit = document.getElementById('btnLoginSubmit');
  const splash = document.getElementById('login-splash');
  if (splash) splash.setAttribute('aria-hidden', 'false');
  setAuthState('unauthenticated');
  isAuthenticated = false;
  clearLoggedOutRouteState();
  stopActivePolling();
  setMsg('');
  setLoginError(errorText);
  if (submit) submit.disabled = false;
  if (input) {
    input.disabled = false;
    if (!keepInput) input.value = '';
    input.focus();
  }
}

function showAuthenticatedApp() {
  const splash = document.getElementById('login-splash');
  if (splash) splash.setAttribute('aria-hidden', 'true');
  setAuthState('authenticated');
  setLoginError('');
  isAuthenticated = true;
  authFlowLocked = false;
}

function logoutToSplash(errorText = 'Invalid token') {
  if (authFlowLocked) return;
  authFlowLocked = true;
  clearStoredTokenEverywhere();
  renderLoginSplash(errorText);
}

async function validateTokenCandidate(token) {
  const val = (token || '').trim();
  if (!val) return { ok: false, reason: 'missing' };
  try {
    const st = await api('/v1/status', {
      method: 'GET',
      tokenOverride: val,
      skipAuthHandling: true,
    });
    if (st.ok) return { ok: true };
    if (isUnauthorizedStatus(st.status)) return { ok: false, reason: 'invalid' };
    return { ok: false, reason: 'http', status: st.status };
  } catch {
    return { ok: false, reason: 'network' };
  }
}

async function submitLoginSplashToken() {
  const input = document.getElementById('loginToken');
  const submit = document.getElementById('btnLoginSubmit');
  if (!input || !submit) return;
  const token = (input.value || '').trim();
  if (!token) {
    setLoginError('Token required');
    return;
  }

  setLoginError('');
  input.disabled = true;
  submit.disabled = true;

  const result = await validateTokenCandidate(token);
  if (result.ok) {
    setToken(token);
    window.location.reload();
    return;
  }

  if (result.reason === 'invalid') {
    clearStoredTokenEverywhere();
    input.value = '';
    setLoginError('Invalid token');
  } else if (result.reason === 'network') {
    setLoginError('Network error while validating token');
  } else {
    const code = result.status ? `HTTP ${result.status}` : 'error';
    setLoginError(`Unable to validate token (${code})`);
  }

  input.disabled = false;
  submit.disabled = false;
  input.focus();
}

function wireLoginSplash() {
  const form = document.getElementById('loginForm');
  if (!form || form.dataset.wired === '1') return;
  form.dataset.wired = '1';
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    await submitLoginSplashToken();
  });
  const input = document.getElementById('loginToken');
  if (input) {
    input.addEventListener('input', () => {
      setLoginError('');
    });
  }
}

/**
 * Safely set text content and color on an element. No-ops if el is null/undefined.
 * @param {HTMLElement|null|undefined} el - The element to update
 * @param {string} text - The text to display
 * @param {string} [colorVar] - Optional CSS variable for color, e.g., 'var(--good)'
 */
function safeText(el, text, colorVar) {
  if (!el) return;
  try {
    el.textContent = text;
    if (colorVar) el.style.color = colorVar;
  } catch {
    // Ignore any DOM errors
  }
}

function renderHintTip(el, text) {
  if (!el) return;
  const tipText = (text || '--').toString();
  el.textContent = '';
  el.classList.add('tip-only');
  const tip = document.createElement('span');
  tip.className = 'tip';
  tip.textContent = 'ⓘ';
  tip.setAttribute('data-tip', tipText);
  tip.setAttribute('aria-label', tipText);
  tip.setAttribute('tabindex', '0');
  el.appendChild(tip);
}

function ensureFloatingTipLayer() {
  if (floatingTipLayer && floatingTipLayer.isConnected) return floatingTipLayer;
  const existing = document.getElementById(FLOATING_TIP_LAYER_ID);
  if (existing) {
    floatingTipLayer = existing;
    return floatingTipLayer;
  }
  const layer = document.createElement('div');
  layer.id = FLOATING_TIP_LAYER_ID;
  layer.className = 'floating-tip-layer';
  layer.setAttribute('role', 'tooltip');
  layer.setAttribute('aria-hidden', 'true');
  document.body.appendChild(layer);
  floatingTipLayer = layer;
  return floatingTipLayer;
}

function positionFloatingTipLayer(target) {
  const layer = ensureFloatingTipLayer();
  if (!layer || !target) return;
  if (!target.isConnected) {
    hideFloatingTipFor();
    return;
  }
  const gap = 8;
  const pad = 8;
  const rect = target.getBoundingClientRect();
  const width = layer.offsetWidth;
  const height = layer.offsetHeight;

  let left = rect.left + (rect.width / 2) - (width / 2);
  left = Math.max(pad, Math.min(left, window.innerWidth - width - pad));

  let top = rect.bottom + gap;
  if (top + height + pad > window.innerHeight) {
    top = rect.top - height - gap;
  }
  if (top < pad) top = pad;

  layer.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
}

function showFloatingTipFor(target) {
  if (!target) return;
  const tipText = (target.getAttribute('data-tip') || target.getAttribute('aria-label') || '').trim();
  if (!tipText) return;

  const layer = ensureFloatingTipLayer();
  if (!layer) return;
  activeTipTarget = target;
  layer.textContent = tipText;
  layer.setAttribute('aria-hidden', 'false');
  layer.classList.add('is-visible');
  positionFloatingTipLayer(target);
}

function hideFloatingTipFor(target) {
  if (target && activeTipTarget && target !== activeTipTarget) return;
  const layer = ensureFloatingTipLayer();
  if (!layer) return;
  activeTipTarget = null;
  layer.classList.remove('is-visible');
  layer.setAttribute('aria-hidden', 'true');
  layer.style.transform = 'translate(-200vw, -200vh)';
}

function wireFloatingTips() {
  if (floatingTipWired) return;
  floatingTipWired = true;
  ensureFloatingTipLayer();

  document.addEventListener('pointerover', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('.tip') : null;
    if (!target) return;
    showFloatingTipFor(target);
  }, true);

  document.addEventListener('pointerout', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('.tip') : null;
    if (!target) return;
    const related = ev.relatedTarget;
    if (related instanceof Node && target.contains(related)) return;
    hideFloatingTipFor(target);
  }, true);

  document.addEventListener('focusin', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('.tip') : null;
    if (!target) return;
    showFloatingTipFor(target);
  }, true);

  document.addEventListener('focusout', (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest('.tip') : null;
    if (!target) return;
    hideFloatingTipFor(target);
  }, true);

  window.addEventListener('resize', () => {
    if (!activeTipTarget) return;
    positionFloatingTipLayer(activeTipTarget);
  });

  window.addEventListener('scroll', () => {
    if (!activeTipTarget) return;
    positionFloatingTipLayer(activeTipTarget);
  }, true);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') hideFloatingTipFor();
  });
}

function fmtTs(epoch) {
  if (!epoch) return '--';
  try { return new Date(epoch * 1000).toLocaleString(); } catch { return String(epoch); }
}

function fmtNum(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const n = Number(v);
  if (Number.isNaN(n)) return '--';
  return n.toFixed(digits);
}

function formatOsLabel(platform) {
  if (!platform || typeof platform !== 'object') return '';
  const os = platform.os || {};
  const rawId = (os.id || '').toString().trim();
  const id = rawId.toLowerCase();
  const version = (os.version_id || '').toString().trim();
  const pretty = (os.pretty_name || '').toString().trim();
  const map = {
    steamos: 'SteamOS',
    bazzite: 'Bazzite',
    fedora: 'Fedora',
    cachyos: 'CachyOS',
    arch: 'Arch',
    manjaro: 'Manjaro',
    endeavour: 'EndeavourOS',
    ubuntu: 'Ubuntu',
    debian: 'Debian',
    nixos: 'NixOS',
    nobara: 'Nobara',
    pop: 'Pop!_OS',
    opensuse: 'openSUSE',
    zorin: 'Zorin',
  };
  const name = map[id] || (rawId ? rawId.charAt(0).toUpperCase() + rawId.slice(1) : '');
  if (name) return version ? `${name} ${version}` : name;
  return pretty || '';
}

function formatBandLabel(band) {
  const raw = (band || '').toString().trim().toLowerCase();
  if (!raw) return '--';
  if (raw === '2.4ghz' || raw === '2.4') return '2.4 GHz';
  if (raw === '5ghz' || raw === '5') return '5 GHz';
  if (raw === '6ghz' || raw === '6') return '6 GHz';
  return raw;
}

function setTextById(id, value, fallback = '--') {
  const el = document.getElementById(id);
  if (!el) return;
  const text = (value === null || value === undefined || value === '') ? fallback : String(value);
  el.textContent = text;
}

function labelizeKey(key) {
  return String(key || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function formatDebugValue(value) {
  if (value === null || value === undefined) return '--';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '--';
  if (typeof value === 'object') {
    try {
      const parts = Object.entries(value).map(([k, v]) => `${k}:${formatDebugValue(v)}`);
      return parts.length ? parts.join(' | ') : '--';
    } catch {
      return '--';
    }
  }
  return String(value);
}

function renderKvGrid(container, items, emptyLabel = 'No data') {
  if (!container) return;
  container.innerHTML = '';
  if (!items || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'small muted';
    empty.textContent = emptyLabel;
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const wrap = document.createElement('div');
    wrap.className = 'kv-item';
    const label = document.createElement('div');
    label.className = 'kv-label';
    label.textContent = item.label;
    const value = document.createElement('div');
    value.className = 'kv-value';
    value.textContent = item.value;
    wrap.appendChild(label);
    wrap.appendChild(value);
    container.appendChild(wrap);
  }
}

function renderBadges(container, items) {
  if (!container) return;
  container.innerHTML = '';
  if (!items || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'small muted';
    empty.textContent = 'None';
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = item;
    container.appendChild(badge);
  }
}

async function copyToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* ignore */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'absolute';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

async function refreshInfo() {
  if (!isAuthenticated) return;
  const versionEl = document.getElementById('uiVersion');
  if (!versionEl) return;
  const fallback = (versionEl.textContent || '').trim();
  try {
    const r = await api('/v1/info');
    const data = (r.ok && r.json && r.json.data) ? r.json.data : null;
    const raw = data ? (data.server_version || data.version || '') : '';
    const cleaned = raw ? String(raw).trim() : '';
    if (cleaned) {
      versionEl.textContent = cleaned.startsWith('v') ? cleaned : `v${cleaned}`;
      return;
    }
  } catch {
    // Ignore fetch errors; fall back to existing value.
  }
  if (!fallback) versionEl.textContent = '--';
}

function fmtPct(v) {
  return (v === null || v === undefined) ? '--' : fmtNum(v, 1);
}

function fmtDbm(v) {
  return (v === null || v === undefined) ? '--' : `${v} dBm`;
}

function fmtMbps(v) {
  return (v === null || v === undefined) ? '--' : fmtNum(v, 1);
}

function toFiniteNumberOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function resolveTelemetryTrafficMbps(t) {
  const summary = (t && t.summary && typeof t.summary === 'object') ? t.summary : {};
  let tx = toFiniteNumberOrNull(summary.tx_mbps_total);
  let rx = toFiniteNumberOrNull(summary.rx_mbps_total);

  if (tx !== null && rx !== null) return { tx, rx };

  const clients = Array.isArray(t && t.clients) ? t.clients : [];
  let txSum = 0;
  let rxSum = 0;
  let txSeen = false;
  let rxSeen = false;
  for (const c of clients) {
    const cTx = toFiniteNumberOrNull(c && c.tx_mbps);
    const cRx = toFiniteNumberOrNull(c && c.rx_mbps);
    if (cTx !== null) {
      txSum += cTx;
      txSeen = true;
    }
    if (cRx !== null) {
      rxSum += cRx;
      rxSeen = true;
    }
  }

  if (tx === null && txSeen) tx = txSum;
  if (rx === null && rxSeen) rx = rxSum;

  return { tx, rx };
}

function fmtTrafficMbpsOrNA(v) {
  const n = toFiniteNumberOrNull(v);
  return n === null ? 'N/A' : `${fmtNum(n, 1)} Mbps`;
}

function renderTelemetry(t) {
  const sumEl = document.getElementById('telemetrySummary');
  const warnEl = document.getElementById('telemetryWarnings');
  const body = document.getElementById('telemetryBody');
  if (!body || !sumEl || !warnEl) return;

  body.innerHTML = '';
  if (!t || t.enabled === false) {
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
  if (!clients.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 7;
    td.textContent = 'No clients connected.';
    td.className = 'muted';
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  for (const c of clients) {
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
    for (const text of cols) {
      const td = document.createElement('td');
      td.textContent = text;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }

  if (t) {
    // Basic Mode Telemetry
    const basicC = document.getElementById('basicTelemetryContainer');
    if (basicC) {
      const summary = t.summary || {};
      const traffic = resolveTelemetryTrafficMbps(t);
      basicC.innerHTML = `
        <div>Clients: ${summary.client_count || 0}</div>
        <div>Traffic: TX ${fmtTrafficMbpsOrNA(traffic.tx)} / RX ${fmtTrafficMbpsOrNA(traffic.rx)}</div>
      `;
    }
    // Advanced charts
    if (window.updateCharts) window.updateCharts(t);
  }

  updateCharts(t);
}

function updateDebugDetails(status) {
  if (!status || typeof status !== 'object') return;

  const band = formatBandLabel(status.selected_band || status.band_preference || '');
  const width = status.selected_width_mhz ? `${status.selected_width_mhz} MHz` : '--';
  const channel = (status.selected_channel !== null && status.selected_channel !== undefined)
    ? String(status.selected_channel)
    : '--';
  const country = status.selected_country || '--';
  const uplink = status.network_tuning && status.network_tuning.uplink_ifname
    ? status.network_tuning.uplink_ifname
    : '--';

  setTextById('dbgAdapter', status.adapter || '--');
  setTextById('dbgApInterface', status.ap_interface || '--');
  setTextById('dbgBand', band);
  setTextById('dbgWidth', width);
  setTextById('dbgChannel', channel);
  setTextById('dbgCountry', country);
  setTextById('dbgUplink', uplink);

  renderBadges(document.getElementById('dbgWarnings'), Array.isArray(status.warnings) ? status.warnings : []);

  const summary = status.telemetry && status.telemetry.summary ? status.telemetry.summary : null;
  const telemetryItems = summary
    ? Object.keys(summary).sort().map((key) => ({
        label: labelizeKey(key),
        value: formatDebugValue(summary[key]),
      }))
    : [];
  renderKvGrid(document.getElementById('dbgTelemetry'), telemetryItems, 'No telemetry summary');

  const eng = status.engine || {};
  setTextById('dbgEnginePid', eng.pid || '--');
  let cmdText = '';
  if (Array.isArray(eng.cmd)) cmdText = eng.cmd.join(' ');
  else if (typeof eng.cmd === 'string') cmdText = eng.cmd;
  setTextById('dbgEngineCmd', cmdText || '--');
  const copyBtn = document.getElementById('btnCopyEngineCmd');
  if (copyBtn) {
    copyBtn.dataset.copyText = cmdText || '';
    copyBtn.disabled = !cmdText;
  }

  const preflight = status.preflight && status.preflight.details ? status.preflight.details : {};
  const preflightItems = [
    { label: 'Hostapd', value: formatDebugValue(preflight.hostapd) },
    { label: 'Regdom', value: formatDebugValue(preflight.regdom) },
    { label: 'Rfkill', value: formatDebugValue(preflight.rfkill) },
  ];
  renderKvGrid(document.getElementById('dbgPreflight'), preflightItems);

  const osLabel = formatOsLabel(status.platform);
  setTextById('dbgPlatformOs', osLabel || '--');
}

function updateStabilityChecklist(status) {
  const banner = document.getElementById('platformStabilityChecklist');
  const title = document.getElementById('platformStabilityTitle');
  const list = document.getElementById('platformStabilityList');
  if (!banner || !list || !title) return;

  const osId = status && status.platform && status.platform.os
    ? (status.platform.os.id || '').toLowerCase()
    : '';
  const isProMode = (getUiMode() === 'advanced');
  const supported = (osId === 'bazzite' || osId === 'pop');
  if (!isProMode || !supported) {
    banner.style.display = 'none';
    list.innerHTML = '';
    return;
  }

  const platformLabel = (osId === 'pop') ? 'Pop!_OS' : 'Bazzite';
  title.textContent = `${platformLabel} Pro Optimization Checklist`;

  const cfg = lastCfg || {};
  const items = [
    { label: 'Disable virtualization (no-virt mode)', ok: !!cfg.optimized_no_virt },
    { label: 'Disable Wi-Fi power save', ok: !!cfg.wifi_power_save_disable },
    { label: 'Disable USB autosuspend', ok: !!cfg.usb_autosuspend_disable },
    { label: 'CPU performance mode', ok: !!cfg.cpu_governor_performance },
    { label: 'Enable systemd/sysctl tuning', ok: !!cfg.sysctl_tuning },
    { label: 'Enable interrupt coalescing', ok: !!cfg.interrupt_coalescing },
  ];

  const nmActive = !!(status && status.platform && status.platform.integration
    && status.platform.integration.network_manager
    && status.platform.integration.network_manager.active);
  if (nmActive) {
    items.push({ label: 'NetworkManager active: ensure AP interface is unmanaged', info: true });
  }

  const firewalldActive = !!(status && status.platform && status.platform.integration
    && status.platform.integration.firewall && status.platform.integration.firewall.firewalld
    && status.platform.integration.firewall.firewalld.active);
  if (firewalldActive) {
    items.push({ label: 'firewalld active: DSCP/QoS marking may be skipped', info: true });
  }

  list.innerHTML = '';
  for (const item of items) {
    const li = document.createElement('li');
    const prefix = item.info ? 'ℹ' : (item.ok ? '✓' : '⚠');
    li.textContent = `${prefix} ${item.label}`;
    li.className = 'stability-item';
    if (item.info) li.classList.add('info');
    else if (!item.ok) li.classList.add('missing');
    list.appendChild(li);
  }

  banner.style.display = '';
}

function getCurrentPlatformOsId() {
  const os = lastStatus && lastStatus.platform && lastStatus.platform.os ? lastStatus.platform.os : null;
  return os && os.id ? String(os.id).toLowerCase() : '';
}

function shouldSuppressTransientStartError(status) {
  const osObj = status && status.platform && status.platform.os ? status.platform.os : null;
  const osId = osObj && osObj.id ? String(osObj.id).toLowerCase() : getCurrentPlatformOsId();
  const phase = status && status.phase ? String(status.phase).toLowerCase() : '';
  const running = !!(status && (status.running || phase === "running"));
  // Bazzite can hit recoverable early engine exits during startup retries.
  return osId === "bazzite" && phase === "starting" && !running;
}

// Chart Globals
let rssiChartRef = null;
let rateChartRef = null;
const MAX_POINTS = 60; // 60 data points (assuming 1s poll ~ 1 min history)

function initCharts() {
  if (typeof Chart === 'undefined') return;

  const rssiCtx = document.getElementById('rssiChart');
  const rateCtx = document.getElementById('rateChart');
  if (!rssiCtx || !rateCtx) return;

  Chart.defaults.color = 'rgba(255,255,255,0.7)';
  Chart.defaults.borderColor = 'rgba(255,255,255,0.1)';
  Chart.defaults.font.family = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif';

  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { display: true, labels: { color: 'rgba(255,255,255,0.7)' } },
    },
    scales: {
      x: { display: false }, // Hide Time Axis for cleaner look
    }
  };

  if (!rssiChartRef) {
    rssiChartRef = new Chart(rssiCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Signal Strength (RSSI dBm)',
          data: [],
          borderColor: '#00d9ff',
          backgroundColor: 'rgba(0, 217, 255, 0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 0
        }]
      },
      options: {
        ...commonOptions,
        scales: {
          ...commonOptions.scales,
          y: { suggestedMin: -90, suggestedMax: -30, grid: { color: 'rgba(255,255,255,0.1)' } }
        }
      }
    });
  }

  if (!rateChartRef) {
    rateChartRef = new Chart(rateCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'TX Bitrate (Mbps)',
            data: [],
            borderColor: '#00ff88',
            backgroundColor: 'rgba(0, 255, 136, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0
          },
          {
            label: 'RX Bitrate (Mbps)',
            data: [],
            borderColor: '#ff4444',
            backgroundColor: 'rgba(255, 68, 68, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0
          }
        ]
      },
      options: {
        ...commonOptions,
        scales: {
          ...commonOptions.scales,
          y: { min: 0, grid: { color: 'rgba(255,255,255,0.1)' } }
        }
      }
    });
  }

  // Wire Table Toggle
  const btnTog = document.getElementById('btnToggleTable');
  const tbl = document.getElementById('telemetryTable');
  if (btnTog && tbl) {
    btnTog.onclick = () => {
      const isHidden = tbl.style.display === 'none';
      tbl.style.display = isHidden ? '' : 'none';
    };
  }
}

function updateCharts(t) {
  if (!rssiChartRef || !rateChartRef) {
    // Try init if not ready (script might have just loaded)
    initCharts();
    if (!rssiChartRef) return;
  }

  const clients = t.clients || [];
  // For simplicity, visualize the *first* client (or average? usually 1 client in VR).
  // Let's visualize the "Best" client (highest signal) or just the first one.
  // VR setup usually implies 1 main headset.
  if (clients.length === 0) return;

  const c = clients[0]; // Primary client
  const now = new Date().toLocaleTimeString();

  // RSSI
  const rssiData = rssiChartRef.data;
  rssiData.labels.push(now);
  rssiData.datasets[0].data.push(c.signal_dbm || -100);
  if (rssiData.labels.length > MAX_POINTS) {
    rssiData.labels.shift();
    rssiData.datasets[0].data.shift();
  }
  rssiChartRef.update('none'); // 'none' for performance

  // Rates
  const rateData = rateChartRef.data;
  rateData.labels.push(now);
  rateData.datasets[0].data.push(c.tx_bitrate_mbps || 0);
  rateData.datasets[1].data.push(c.rx_bitrate_mbps || 0);
  if (rateData.labels.length > MAX_POINTS) {
    rateData.labels.shift();
    rateData.datasets[0].data.shift();
    rateData.datasets[1].data.shift();
  }
  rateChartRef.update('none');
}

async function api(path, opts = {}) {
  const tokenOverride = (typeof opts.tokenOverride === 'string') ? opts.tokenOverride.trim() : '';
  const skipAuthHandling = !!opts.skipAuthHandling;
  if (!tokenOverride && !skipAuthHandling && !isAuthenticated) {
    return { ok: false, status: 401, json: null, raw: '' };
  }
  const fetchOpts = Object.assign({}, opts);
  delete fetchOpts.tokenOverride;
  delete fetchOpts.skipAuthHandling;

  const baseHeaders = {};
  if (fetchOpts.headers) {
    if (fetchOpts.headers instanceof Headers) {
      fetchOpts.headers.forEach((value, key) => { baseHeaders[key] = value; });
    } else {
      Object.assign(baseHeaders, fetchOpts.headers);
    }
  }
  const headerKeys = Object.keys(baseHeaders).reduce((acc, key) => {
    acc[key.toLowerCase()] = key;
    return acc;
  }, {});
  if (!headerKeys['x-correlation-id']) baseHeaders['X-Correlation-Id'] = cid();
  const tok = tokenOverride || getToken();
  const injected = !!(tok && !headerKeys['x-api-token']);
  if (injected) baseHeaders['X-Api-Token'] = tok;
  if (fetchOpts.body && !headerKeys['content-type']) baseHeaders['Content-Type'] = 'application/json';
  debugTokenLog(injected);

  const res = await fetch(BASE + path, Object.assign({}, fetchOpts, { headers: baseHeaders }));
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { }
  if (!skipAuthHandling && isUnauthorizedStatus(res.status)) {
    logoutToSplash('Invalid token');
  }
  return { ok: res.ok, status: res.status, json, raw: text };
}

function setMsg(text, kind = '') {
  const els = [document.getElementById('msg'), document.getElementById('msgBasic')];
  for (const el of els) {
    if (!el) continue;
    el.textContent = text || '';
    el.className = 'small mt-10' + (kind ? (' ' + kind) : '');
  }
}

let actionInFlight = false;
let refreshRequestSeq = 0;

function stopActivePolling() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;
  refreshRequestSeq += 1;
}

function setActionControlsDisabled(disabled) {
  const ids = [
    'btnStart',
    'btnStop',
    'btnRepair',
    'btnRestart',
    'btnStartBasic',
    'btnStopBasic',
    'btnRepairBasic',
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el) el.disabled = !!disabled;
  }
}

async function withActionLock(action) {
  if (actionInFlight) return null;
  actionInFlight = true;
  setActionControlsDisabled(true);
  try {
    return await action();
  } finally {
    actionInFlight = false;
    setActionControlsDisabled(false);
  }
}

function stateIsRunning(state) {
  return !!(state && (state.running || state.phase === 'running'));
}

function startResultLooksSuccessful(resp) {
  if (!resp || !resp.ok || !resp.json) return false;
  const code = String(resp.json.result_code || '').toLowerCase();
  if (code === 'started' || code === 'already_running' || code === 'started_with_fallback') return true;
  return stateIsRunning(resp.json.data || {});
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForRunningStatus(timeoutMs = 12000, intervalMs = 1000) {
  const timeout = Math.max(0, Number(timeoutMs) || 0);
  const interval = Math.max(250, Number(intervalMs) || 1000);
  const deadline = Date.now() + timeout;
  let lastState = null;
  while (isAuthenticated && Date.now() <= deadline) {
    const st = await api('/v1/status');
    if (!isAuthenticated || isUnauthorizedStatus(st.status)) break;
    if (st.ok && st.json && st.json.data) {
      lastState = st.json.data;
      if (stateIsRunning(lastState)) return { running: true, state: lastState };
    }
    if (Date.now() >= deadline) break;
    await sleep(interval);
  }
  return { running: false, state: lastState };
}

async function startHotspot(overrides, label) {
  if (!isAuthenticated) return;
  await withActionLock(async () => {
    const prefix = label ? `Starting (${label})...` : 'Starting...';
    setMsg(prefix);
    const payload = {};
    if (overrides) payload.overrides = overrides;

    // Send basic_mode: true when UI is in Basic Mode for VR-optimized enforcement.
    if (getUiMode() === 'basic') {
      payload.basic_mode = true;
    }

    const runStart = async () => {
      const opts = { method: 'POST' };
      if (Object.keys(payload).length > 0) opts.body = JSON.stringify(payload);
      return api('/v1/start', opts);
    };

    const first = await runStart();
    const firstCode = (first.json && first.json.result_code) ? first.json.result_code : `HTTP ${first.status}`;

    if (startResultLooksSuccessful(first)) {
      setMsg(`Start: ${firstCode}`);
      await refresh();
      return;
    }

    const recovered = await waitForRunningStatus(10000, 1000);
    if (recovered.running) {
      setMsg(`Start recovered: running (${firstCode})`);
      await refresh();
      return;
    }

    setMsg(`Start failed (${firstCode}). Attempting automatic repair + retry...`, 'dangerText');
    const repairRes = await api('/v1/repair', { method: 'POST' });
    const repairCode = (repairRes.json && repairRes.json.result_code) ? repairRes.json.result_code : `HTTP ${repairRes.status}`;
    if (!repairRes.ok) {
      setMsg(`Start failed (${firstCode}); repair failed (${repairCode}).`, 'dangerText');
      await refresh();
      return;
    }

    const retry = await runStart();
    const retryCode = (retry.json && retry.json.result_code) ? retry.json.result_code : `HTTP ${retry.status}`;
    if (startResultLooksSuccessful(retry)) {
      setMsg(`Start recovered after repair: ${retryCode}`);
      await refresh();
      return;
    }

    const recoveredAfterRepair = await waitForRunningStatus(12000, 1000);
    if (recoveredAfterRepair.running) {
      setMsg(`Start recovered after repair: running (${retryCode})`);
      await refresh();
      return;
    }

    setMsg(`Start failed: ${retryCode}`, 'dangerText');
    await refresh();
  });
}

async function stopHotspot() {
  if (!isAuthenticated) return;
  await withActionLock(async () => {
    setMsg('Stopping...');
    const r = await api('/v1/stop', { method: 'POST' });
    setMsg(r.json ? ('Stop: ' + r.json.result_code) : ('Stop failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
    await refresh();
  });
}

async function repairHotspot() {
  if (!isAuthenticated) return;
  await withActionLock(async () => {
    setMsg('Repairing...');
    const r = await api('/v1/repair', { method: 'POST' });
    setMsg(r.json ? ('Repair: ' + r.json.result_code) : ('Repair failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
    await refresh();
  });
}

async function restartHotspot() {
  if (!isAuthenticated) return;
  await withActionLock(async () => {
    setMsg('Restarting...');
    const r = await api('/v1/restart', { method: 'POST' });
    setMsg(r.json ? ('Restart: ' + r.json.result_code) : ('Restart failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
    await refresh();
  });
}

async function copyFieldValue(fieldId, label, fallbackIds = []) {
  const ids = [fieldId].concat(fallbackIds || []);
  let value = '';
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    const v = (el.value || '').toString().trim();
    if (v) {
      value = v;
      break;
    }
  }
  if (!value) {
    setMsg(`${label} is empty`, 'dangerText');
    return;
  }
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      setMsg(`${label} copied`);
      return;
    } catch { }
  }
  try {
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
  } catch {
    setMsg(`Failed to copy ${label}`, 'dangerText');
  }
}

function setPill(state) {
  const cmdInfo = parseEngineCmd(state && state.engine ? state.engine.cmd : null);
  const running = !!state.running;
  const phase = state.phase || '--';
  const adapter = state.adapter || cmdInfo.apIfname || '--';
  const band = state.band || cmdInfo.band || '--';
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

  if (phase && phase !== '--' && phase !== 'stopped' && phase !== 'error' && phase !== 'running' && !(phase === 'stopped' && !running)) {
    statusParts.push(phase.charAt(0).toUpperCase() + phase.slice(1));
  }

  if (adapter && adapter !== '--') {
    statusParts.push(adapter);
  }

  if (band && band !== '--') {
    statusParts.push(formatBandLabel(normalizeBandValue(band)));
  }

  if (mode && mode !== '--' && mode !== 'nat') {
    statusParts.push(mode);
  }

  const statusText = (statusParts.length === 0) ? 'Loading...' : statusParts.join(' | ');

  const apply = (pill, txt) => {
    if (!pill || !txt) return;
    pill.classList.remove('ok', 'err');
    if (running) pill.classList.add('ok');
    else if (phase === 'error') pill.classList.add('err');
    pill.style.display = 'inline-flex';
    txt.textContent = statusText;
  };
  apply(document.getElementById('pill'), document.getElementById('pillTxt'));
  apply(document.getElementById('basicPill'), document.getElementById('basicPillTxt'));
}

function truncateText(text, maxLen) {
  const raw = (text || '').toString();
  if (raw.length <= maxLen) return raw;
  return raw.slice(0, Math.max(0, maxLen - 3)) + '...';
}

function extractRemediationText(detail) {
  if (!detail || typeof detail !== 'object') return '';
  let title = '';
  let remediation = '';
  if (typeof detail.title === 'string') title = detail.title.trim();
  if (typeof detail.remediation === 'string') remediation = detail.remediation.trim();
  if (!remediation && Array.isArray(detail.errors) && detail.errors.length > 0) {
    const first = detail.errors[0];
    if (first && typeof first === 'object') {
      if (!title && typeof first.title === 'string') title = first.title.trim();
      if (typeof first.remediation === 'string') remediation = first.remediation.trim();
    }
  }
  if (!remediation) return '';
  return title ? `${title} - ${remediation}` : remediation;
}

function updateBasicStatusMeta(state) {
  const cmdInfo = parseEngineCmd(state && state.engine ? state.engine.cmd : null);
  const adapter = state.adapter || '--';
  const band = state.band || cmdInfo.band || '--';
  const suppressTransientError = shouldSuppressTransientStartError(state);
  const metaEl = document.getElementById('basicStatusAdapterBand');
  if (metaEl) {
    const bandLabel = band !== '--' ? formatBandLabel(normalizeBandValue(band)) : band;
    let widthLabel = '';
    if (state && state.running) {
      const rawWidth = (state.channel_width_mhz !== undefined && state.channel_width_mhz !== null)
        ? state.channel_width_mhz
        : cmdInfo.channelWidth;
      if (rawWidth !== undefined && rawWidth !== null && rawWidth !== '' && rawWidth !== 'auto') {
        const widthNum = parseInt(rawWidth, 10);
        widthLabel = Number.isFinite(widthNum) ? `${widthNum} MHz` : String(rawWidth);
      }
    }
    metaEl.textContent = `Adapter: ${adapter} | Band: ${bandLabel}${widthLabel ? ` | Width: ${widthLabel}` : ''}`;
  }

  const detailsEl = document.getElementById('basicStatusDetails');
  if (detailsEl) {
    const parts = [];
    if (state.mode) parts.push(`Mode: ${state.mode}`);
    if (state.fallback_reason) parts.push(`Fallback: ${state.fallback_reason}`);
    const apIf = state.ap_interface || cmdInfo.apIfname;
    if (apIf) parts.push(`AP: ${apIf}`);
    const text = parts.join(' | ');
    detailsEl.textContent = text;
    detailsEl.style.display = text ? '' : 'none';
  }

  const errEl = document.getElementById('basicLastError');
  if (!errEl) return;
  const err = state.last_error || (state.engine && state.engine.last_error) || '';
  if (err && !suppressTransientError) {
    errEl.textContent = `Last error: ${truncateText(err, 140)}`;
    errEl.style.display = '';
  } else {
    errEl.textContent = '';
    errEl.style.display = 'none';
  }

  const remEl = document.getElementById('basicLastErrorDetail');
  if (remEl) {
    const remediation = extractRemediationText(state.last_error_detail);
    if (remediation && !suppressTransientError) {
      remEl.textContent = `Remediation: ${remediation}`;
      remEl.style.display = '';
    } else {
      remEl.textContent = '';
      remEl.style.display = 'none';
    }
  }

}

function syncCountrySelectFromInput() {
  const c = (document.getElementById('country').value || '').toString().toUpperCase();
  const sel = document.getElementById('country_sel');
  let found = false;
  for (const opt of sel.options) {
    if (opt.value === c) { sel.value = c; found = true; break; }
  }
  if (!found) sel.value = '__custom';
}

function enforceBandRules() {
  const sel = document.getElementById('band_preference');
  const g6Box = document.getElementById('sixgBox');
  const g5Box = document.getElementById('fivegBox');
  const secEl = document.getElementById('ap_security');
  const secHint = document.getElementById('secHint');
  const bandHint = document.getElementById('bandHint');
  const bandPreferenceTip = document.getElementById('bandPreferenceTip');

  const band = resolveBandPref(sel.value);
  const is6 = (band === '6ghz');
  const is5 = (band === '5ghz');

  if (bandPreferenceTip) {
    renderHintTip(bandPreferenceTip, '5 GHz: best default for VR streaming on most adapters.');
  }

  if (g6Box) g6Box.style.display = is6 ? 'block' : 'none';
  if (g5Box) g5Box.style.display = is5 ? 'block' : 'none';

  // WPA3 is mandatory for 6 GHz (Wi-Fi 6E), but optional for others.
  if (is6) {
    secEl.value = 'wpa3_sae';
    secEl.disabled = true;
    if (g6Box) g6Box.style.display = '';
    if (bandHint) {
      bandHint.innerHTML = "<strong>6 GHz:</strong> requires a 6 GHz-capable adapter and a correct Country. WPA3-SAE is enforced.";
    }
    renderHintTip(secHint, "Locked: 6 GHz requires WPA3 (SAE).");
  } else {
    secEl.disabled = false;
    if (g6Box) g6Box.style.display = 'none';
    if (bandHint) {
      if (band === '5ghz') bandHint.innerHTML = '';
      else bandHint.innerHTML = "<strong>2.4 GHz:</strong> compatibility/fallback band (higher latency / more interference).";
    }
    renderHintTip(secHint, "WPA2 (PSK) is typical. WPA3 (SAE) may be supported but depends on driver + clients.");
  }
  applyFieldVisibility(getUiMode());
  updateBasicChannelBanner();
}

function capsLabel(a) {
  const parts = [];
  if (a.supports_6ghz) parts.push('6G');
  if (a.supports_5ghz) parts.push('5G');
  if (a.supports_2ghz) parts.push('2G');
  return parts.length ? parts.join('/') : '--';
}

function adapterSupportsBand(a, band) {
  if (!a) return false;
  if (band === '6ghz') return !!a.supports_6ghz;
  if (band === '5ghz') return !!a.supports_5ghz;
  if (band === '2.4ghz') return !!a.supports_2ghz;
  return true;
}

function maybeAutoPickAdapterForBand() {
  const rawBand = document.getElementById('band_preference').value;
  const band = resolveBandPref(rawBand);
  const sel = document.getElementById('ap_adapter');
  const hint = document.getElementById('adapterHint');
  if (!lastAdapters || !Array.isArray(lastAdapters.adapters)) return;

  const byIf = new Map();
  for (const a of lastAdapters.adapters) byIf.set(a.ifname, a);

  const cur = sel.value;
  const curA = byIf.get(cur);

  if (band === '6ghz') {
    const any6 = lastAdapters.adapters.filter(a => a.supports_ap && a.supports_6ghz);
    if (!any6.length) {
      hint.innerHTML = "<span class='pillWarn'>No 6 GHz-capable AP adapter detected</span>";
      return;
    }
    hint.textContent = "6 GHz requires an adapter that supports 6 GHz in AP mode.";
    if (!curA || !adapterSupportsBand(curA, '6ghz')) {
      // Prefer recommended if it also supports 6 GHz, else first 6G adapter.
      const rec = lastAdapters.recommended;
      const recA = byIf.get(rec);
      const pick = (recA && recA.supports_ap && recA.supports_6ghz) ? rec : any6[0].ifname;
      sel.value = pick;
      setDirty(true);
    }
  } else {
    hint.textContent = "";
  }
}

function applyVrProfile(profileName = 'balanced') {
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
      channel_width: '80',
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
      channel_width: '80',
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
      channel_width: '80',
      beacon_interval: 100,
      dtim_period: 3,
      short_guard_interval: false,
    }
  };

  let profile = profiles[profileName] || profiles['balanced'];
  const osId = getCurrentPlatformOsId();
  if (osId === 'pop') {
    profile = Object.assign({}, profile, {
      optimized_no_virt: true,
      wifi_power_save_disable: true,
      usb_autosuspend_disable: true,
      cpu_governor_performance: true,
      sysctl_tuning: true,
      interrupt_coalescing: true,
    });
  }

  setValueIf('band_preference', profile.band_preference);
  setValueIf('ap_security', profile.ap_security);
  setCheckedIf('optimized_no_virt', profile.optimized_no_virt);
  setCheckedIf('enable_internet', profile.enable_internet);
  setCheckedIf('wifi_power_save_disable', profile.wifi_power_save_disable);
  setCheckedIf('usb_autosuspend_disable', profile.usb_autosuspend_disable);
  setCheckedIf('cpu_governor_performance', profile.cpu_governor_performance);
  setCheckedIf('sysctl_tuning', profile.sysctl_tuning);
  setCheckedIf('tcp_low_latency', profile.tcp_low_latency || false);
  setCheckedIf('memory_tuning', profile.memory_tuning || false);
  setCheckedIf('interrupt_coalescing', profile.interrupt_coalescing || false);
  setCheckedIf('telemetry_enable', profile.telemetry_enable);
  setValueIf('telemetry_interval_s', profile.telemetry_interval_s);
  setCheckedIf('watchdog_enable', profile.watchdog_enable);
  setValueIf('watchdog_interval_s', profile.watchdog_interval_s);
  setQoS(profile.qos_preset);
  setCheckedIf('nat_accel', profile.nat_accel);
  setCheckedIf('bridge_mode', profile.bridge_mode);
  if (document.getElementById('channel_width')) {
    document.getElementById('channel_width').value = profile.channel_width || '80';
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
  updateStabilityChecklist(lastStatus || {});
}

function getForm() {
  const out = {};

  const ssid = getValueIf('ssid');
  if (ssid !== undefined) out.ssid = ssid;

  const bandPref = getValueIf('band_preference');
  if (bandPref !== undefined) out.band_preference = resolveBandPref(bandPref);

  const apSecurity = getValueIf('ap_security');
  if (apSecurity !== undefined) out.ap_security = apSecurity;

  const country = getValueIf('country');
  if (country !== undefined) out.country = country;

  const optimizedNoVirt = getCheckedIf('optimized_no_virt');
  if (optimizedNoVirt !== undefined) out.optimized_no_virt = optimizedNoVirt;

  const apAdapter = getValueIf('ap_adapter');
  if (apAdapter !== undefined) out.ap_adapter = apAdapter;

  const apReadyRaw = getValueIf('ap_ready_timeout_s');
  if (apReadyRaw !== undefined) out.ap_ready_timeout_s = parseFloat(apReadyRaw || '6.0');

  const fallbackRaw = getValueIf('fallback_channel_2g');
  if (fallbackRaw !== undefined) out.fallback_channel_2g = parseInt(fallbackRaw || '6', 10);

  const channelWidth = getValueIf('channel_width');
  if (channelWidth !== undefined) out.channel_width = channelWidth || '80';

  const beaconRaw = getValueIf('beacon_interval');
  if (beaconRaw !== undefined) out.beacon_interval = parseInt(beaconRaw || '50', 10);

  const dtimRaw = getValueIf('dtim_period');
  if (dtimRaw !== undefined) out.dtim_period = parseInt(dtimRaw || '1', 10);

  const shortGi = getCheckedIf('short_guard_interval');
  if (shortGi !== undefined) out.short_guard_interval = shortGi;

  const channelAuto = getCheckedIf('channel_auto_select');
  if (channelAuto !== undefined) out.channel_auto_select = channelAuto;

  const enableInternet = getCheckedIf('enable_internet');
  if (enableInternet !== undefined) out.enable_internet = enableInternet;

  const wifiPowerSave = getCheckedIf('wifi_power_save_disable');
  if (wifiPowerSave !== undefined) out.wifi_power_save_disable = wifiPowerSave;

  const usbAutosuspend = getCheckedIf('usb_autosuspend_disable');
  if (usbAutosuspend !== undefined) out.usb_autosuspend_disable = usbAutosuspend;

  const cpuGovernor = getCheckedIf('cpu_governor_performance');
  if (cpuGovernor !== undefined) out.cpu_governor_performance = cpuGovernor;

  const sysctl = getCheckedIf('sysctl_tuning');
  if (sysctl !== undefined) out.sysctl_tuning = sysctl;

  const interruptCoal = getCheckedIf('interrupt_coalescing');
  if (interruptCoal !== undefined) out.interrupt_coalescing = interruptCoal;

  const tcpLowLatency = getCheckedIf('tcp_low_latency');
  if (tcpLowLatency !== undefined) out.tcp_low_latency = tcpLowLatency;

  const memoryTuning = getCheckedIf('memory_tuning');
  if (memoryTuning !== undefined) out.memory_tuning = memoryTuning;

  const ioScheduler = getCheckedIf('io_scheduler_optimize');
  if (ioScheduler !== undefined) out.io_scheduler_optimize = ioScheduler;

  const telemetryEnable = getCheckedIf('telemetry_enable');
  if (telemetryEnable !== undefined) out.telemetry_enable = telemetryEnable;

  const telemetryRaw = getValueIf('telemetry_interval_s');
  if (telemetryRaw !== undefined) out.telemetry_interval_s = parseFloat(telemetryRaw || '2.0');

  const watchdogEnable = getCheckedIf('watchdog_enable');
  if (watchdogEnable !== undefined) out.watchdog_enable = watchdogEnable;

  const watchdogRaw = getValueIf('watchdog_interval_s');
  if (watchdogRaw !== undefined) out.watchdog_interval_s = parseFloat(watchdogRaw || '2.0');

  const connectionQuality = getCheckedIf('connection_quality_monitoring');
  if (connectionQuality !== undefined) out.connection_quality_monitoring = connectionQuality;

  const autoSwitch = getCheckedIf('auto_channel_switch');
  if (autoSwitch !== undefined) out.auto_channel_switch = autoSwitch;

  out.qos_preset = currentQosPreset;

  const natAccel = getCheckedIf('nat_accel');
  if (natAccel !== undefined) out.nat_accel = natAccel;

  const bridgeMode = getCheckedIf('bridge_mode');
  if (bridgeMode !== undefined) out.bridge_mode = bridgeMode;

  const firewalldEnabled = getCheckedIf('firewalld_enabled');
  if (firewalldEnabled !== undefined) out.firewalld_enabled = firewalldEnabled;

  const fwMasq = getCheckedIf('firewalld_enable_masquerade');
  if (fwMasq !== undefined) out.firewalld_enable_masquerade = fwMasq;

  const fwForward = getCheckedIf('firewalld_enable_forward');
  if (fwForward !== undefined) out.firewalld_enable_forward = fwForward;

  const fwCleanup = getCheckedIf('firewalld_cleanup_on_stop');
  if (fwCleanup !== undefined) out.firewalld_cleanup_on_stop = fwCleanup;

  const debug = getCheckedIf('debug');
  if (debug !== undefined) out.debug = debug;

  out.firewalld_zone = (lastCfg && lastCfg.firewalld_zone) ? lastCfg.firewalld_zone : 'trusted';

  // Optional 5 GHz channel
  const ch5Raw = getValueIf('channel_5g');
  if (ch5Raw !== undefined) {
    const ch5 = ch5Raw.trim();
    if (ch5) {
      const n = parseInt(ch5, 10);
      if (!Number.isNaN(n)) out.channel_5g = n;
    } else {
      out.channel_5g = null;
    }
  }

  // Optional 6 GHz channel
  const ch6Raw = getValueIf('channel_6g');
  if (ch6Raw !== undefined) {
    const ch6 = ch6Raw.trim();
    if (ch6) {
      const n = parseInt(ch6, 10);
      if (!Number.isNaN(n)) out.channel_6g = n;
    } else {
      out.channel_6g = null;
    }
  }

  // Optional TX power
  const txPowerRaw = getValueIf('tx_power');
  if (txPowerRaw !== undefined) {
    const txPower = txPowerRaw.trim();
    if (txPower) {
      const n = parseInt(txPower, 10);
      if (!Number.isNaN(n)) out.tx_power = n;
    } else {
      out.tx_power = null;
    }
  }

  const gwRaw = getValueIf('lan_gateway_ip');
  if (gwRaw !== undefined) {
    const gw = gwRaw.trim();
    if (gw) out.lan_gateway_ip = gw;
  }

  const dhcpStartRaw = getValueIf('dhcp_start_ip');
  if (dhcpStartRaw !== undefined) {
    const dhcpStart = dhcpStartRaw.trim();
    if (dhcpStart) out.dhcp_start_ip = dhcpStart;
  }

  const dhcpEndRaw = getValueIf('dhcp_end_ip');
  if (dhcpEndRaw !== undefined) {
    const dhcpEnd = dhcpEndRaw.trim();
    if (dhcpEnd) out.dhcp_end_ip = dhcpEnd;
  }

  const dhcpDnsRaw = getValueIf('dhcp_dns');
  if (dhcpDnsRaw !== undefined) {
    const dhcpDns = dhcpDnsRaw.trim();
    if (dhcpDns) out.dhcp_dns = dhcpDns;
  }

  const cpuAffinityRaw = getValueIf('cpu_affinity');
  if (cpuAffinityRaw !== undefined) out.cpu_affinity = cpuAffinityRaw.trim();

  const irqAffinityRaw = getValueIf('irq_affinity');
  if (irqAffinityRaw !== undefined) out.irq_affinity = irqAffinityRaw.trim();

  const bridgeNameRaw = getValueIf('bridge_name');
  if (bridgeNameRaw !== undefined) out.bridge_name = bridgeNameRaw.trim();

  const bridgeUplinkRaw = getValueIf('bridge_uplink');
  if (bridgeUplinkRaw !== undefined) out.bridge_uplink = bridgeUplinkRaw.trim();

  // Only send passphrase if user typed a new one.
  const pw = getPassphraseValue();
  if (passphraseDirty && pw) out.wpa2_passphrase = pw;

  return filterConfigForMode(out);
}

function applyConfig(cfg) {
  lastCfg = cfg || {};
  updateBasicQosBanner({ markDirty: false });

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
    document.getElementById('channel_width').value = (cfg.channel_width || '80');
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
  setCheckedIf('sysctl_tuning', !!cfg.sysctl_tuning);
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
  setCheckedIf('telemetry_enable', (cfg.telemetry_enable !== false));
  setValueIf('telemetry_interval_s', (cfg.telemetry_interval_s ?? 2.0));
  setCheckedIf('watchdog_enable', (cfg.watchdog_enable !== false));
  setValueIf('watchdog_interval_s', (cfg.watchdog_interval_s ?? 2.0));
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
  setValueIf('cpu_affinity', (cfg.cpu_affinity || ''));
  document.getElementById('firewalld_enabled').checked = !!cfg.firewalld_enabled;
  document.getElementById('firewalld_enable_masquerade').checked = !!cfg.firewalld_enable_masquerade;
  document.getElementById('firewalld_enable_forward').checked = !!cfg.firewalld_enable_forward;
  document.getElementById('firewalld_cleanup_on_stop').checked = !!cfg.firewalld_cleanup_on_stop;
  document.getElementById('debug').checked = !!cfg.debug;

  document.getElementById('channel_6g').value = (cfg.channel_6g ?? '');
  document.getElementById('channel_5g').value = (cfg.channel_5g ?? '');

  if (cfg.ap_adapter) {
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

async function loadAdapters() {
  if (!isAuthenticated) return;
  let r;
  try {
    r = await api('/v1/adapters');
  } catch {
    return;
  }
  if (!isAuthenticated) return;
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
  let basicUsbCount = 0;

  for (const a of list) {
    // Basic Mode: Only show USB adapters (hide internal/PCI)
    // Advanced Mode: Show all (internal + USB)
    if (mode === 'basic') {
      // If we detected bus info, enforce USB-only.
      // If bus detection failed (unknown), we might default to hiding it to be safe, 
      // or showing it. Given the request "only surface USB", we hide unless confirmed USB.
      if (a.bus !== 'usb') {
        continue;
      }
    }

    const opt = document.createElement('option');
    opt.value = a.ifname;

    if (mode === 'basic') {
      basicUsbCount++;
      const recStr = (a.ifname === rec) ? ' (Recommended)' : '';
      opt.textContent = `USB WiFi ${basicUsbCount}${recStr}`;
    } else {
      const ap = a.supports_ap ? 'AP' : 'no-AP';
      const caps = capsLabel(a);
      const reg = a.regdom && a.regdom.country ? a.regdom.country : '--';
      const star = (a.ifname === rec) ? '* ' : '';
      opt.textContent = `${star}${a.ifname} (${a.phy || 'phy?'}, ${caps}, reg=${reg}, score=${a.score}, ${ap})`;
    }

    el.appendChild(opt);
  }
  el.dataset.recommended = rec;

  const trySet = (v) => {
    if (!v) return false;
    for (const opt of el.options) {
      if (opt.value === v) { el.value = v; return true; }
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

async function refresh() {
  if (!isAuthenticated) return;
  const requestSeq = ++refreshRequestSeq;
  const privacy = document.getElementById('privacyMode').checked;
  const stPath = privacy ? '/v1/status' : '/v1/status?include_logs=1';

  let st;
  let cfg;
  try {
    [st, cfg] = await Promise.all([api(stPath), api('/v1/config')]);
  } catch {
    if (!isAuthenticated || requestSeq !== refreshRequestSeq) return;
    setMsg('Network error while fetching status.', 'dangerText');
    return;
  }
  if (!isAuthenticated || requestSeq !== refreshRequestSeq) return;

  if (cfg.ok && cfg.json) {
    applyConfig(cfg.json.data || {});
  }

  if (!st.ok || !st.json) {
    if (isUnauthorizedStatus(st.status) || !isAuthenticated) return;
    setMsg(st.json ? (st.json.result_code || 'error') : `Failed: HTTP ${st.status}`, 'dangerText');
    return;
  }

  const s = st.json.data || {};
  lastStatus = s;
  setPill(s);
  updateBasicStatusMeta(s);
  const suppressTransientError = shouldSuppressTransientStartError(s);

  const advErrEl = document.getElementById('statusLastError');
  if (advErrEl) {
    const err = s.last_error || (s.engine && s.engine.last_error) || '';
    if (err && !suppressTransientError) {
      advErrEl.textContent = `Last error: ${truncateText(err, 140)}`;
      advErrEl.style.display = '';
    } else {
      advErrEl.textContent = '';
      advErrEl.style.display = 'none';
    }
  }
  const advRemEl = document.getElementById('statusErrorDetail');
  if (advRemEl) {
    const remediation = extractRemediationText(s.last_error_detail);
    if (remediation && !suppressTransientError) {
      advRemEl.textContent = `Remediation: ${remediation}`;
      advRemEl.style.display = '';
    } else {
      advRemEl.textContent = '';
      advRemEl.style.display = 'none';
    }
  }

  const osLabel = formatOsLabel(s.platform);
  const osEl = document.getElementById('uiOsName');
  if (osEl) osEl.textContent = `• ${osLabel || '--'}`;

  const metaParts = [
    `last_op=${s.last_op || '--'}`,
    fmtTs(s.last_op_ts),
    `cid=${s.last_correlation_id || '--'}`
  ];
  if (s.mode) metaParts.push(`mode=${s.mode}`);
  if (s.fallback_reason) metaParts.push(`fallback=${s.fallback_reason}`);
  const statusMetaEl = document.getElementById('statusMeta');
  if (statusMetaEl) statusMetaEl.textContent = metaParts.join(' | ');

  const eff = formatEffectiveSummary(s);
  const effEl = document.getElementById('statusEffective');
  if (effEl) {
    effEl.textContent = eff || '';
    effEl.style.display = eff ? '' : 'none';
  }

  const rawStatusEl = document.getElementById('rawStatusPre');
  if (rawStatusEl) rawStatusEl.textContent = JSON.stringify(st.json, null, 2);

  updateDebugDetails(s);
  updateStabilityChecklist(s);

  const eng = (s.engine || {});
  // Combine ap_logs_tail and stdout_tail
  const apLogs = (eng.ap_logs_tail || []).join('\n');
  const stdLogs = (eng.stdout_tail || []).join('\n');
  const out = (apLogs ? apLogs + '\n' : '') + stdLogs;
  const err = (eng.stderr_tail || []).join('\n');
  const stdoutEl = document.getElementById('stdout');
  if (stdoutEl) stdoutEl.textContent = privacy ? '(hidden)' : (out || '(empty)');
  const stderrEl = document.getElementById('stderr');
  if (stderrEl) stderrEl.textContent = privacy ? '(hidden)' : (err || '(empty)');

  renderTelemetry(s.telemetry);
}

function applyAutoRefresh() {
  const enabled = document.getElementById('autoRefresh').checked;
  const every = parseInt(document.getElementById('refreshEvery').value || '2000', 10);

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;

  if (!isAuthenticated) return;
  if (enabled) refreshTimer = setInterval(refresh, every);

  STORE.setItem(LS.auto, enabled ? '1' : '0');
  STORE.setItem(LS.every, String(every));

  const basicAuto = document.getElementById('autoRefreshBasic');
  const basicEvery = document.getElementById('refreshEveryBasic');
  if (basicAuto) basicAuto.checked = enabled;
  if (basicEvery) basicEvery.value = String(every);
}

function applyPrivacyMode() {
  const adv = document.getElementById('privacyMode');
  const basic = document.getElementById('privacyModeBasic');
  const v = adv ? adv.checked : (basic ? basic.checked : true);
  if (adv) adv.checked = v;
  if (basic) basic.checked = v;
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
  if (rec) {
    sel.value = rec;
    setDirty(true);
    updateBandOptions();
    enforceBandRules();
  }
});

async function revealPassphrase(btn, targetEl) {
  if (!targetEl) return;
  const existing = getPassphraseValue();
  if (existing) {
    if (targetEl.value !== existing) targetEl.value = existing;
    targetEl.type = (targetEl.type === 'password') ? 'text' : 'password';
    return;
  }

  if (btn) btn.disabled = true;
  setMsg('Revealing passphrase...');
  const r = await api('/v1/config/reveal_passphrase', { method: 'POST', body: JSON.stringify({ confirm: true }) });
  if (r.ok && r.json && r.json.data && typeof r.json.data.wpa2_passphrase === 'string') {
    const pw = r.json.data.wpa2_passphrase;
    const { advanced, basic } = getPassphraseInputs();
    if (advanced) {
      advanced.value = pw;
      advanced.type = 'text';
    }
    if (basic) {
      basic.value = pw;
      basic.type = 'text';
    }
    passphraseDirty = false;
    setMsg('Passphrase revealed');
  } else {
    const code = (r.json && r.json.result_code) ? r.json.result_code : `HTTP ${r.status}`;
    setMsg(`Reveal failed: ${code}`, 'dangerText');
  }
  if (btn) btn.disabled = false;
}

const btnRevealPass = document.getElementById('btnRevealPass');
if (btnRevealPass) btnRevealPass.addEventListener('click', async () => {
  const passEl = document.getElementById('wpa2_passphrase');
  await revealPassphrase(btnRevealPass, passEl);
});

const btnCopySsid = document.getElementById('btnCopySsid');
if (btnCopySsid) btnCopySsid.addEventListener('click', () => copyFieldValue('ssid', 'SSID'));
const btnCopyPass = document.getElementById('btnCopyPass');
if (btnCopyPass) btnCopyPass.addEventListener('click', () => copyFieldValue('wpa2_passphrase', 'Passphrase', ['wpa2_passphrase_basic']));


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

let showTelemetryState = false;
const showTelBasic = document.getElementById('showTelemetryBasic');
if (showTelBasic) showTelBasic.addEventListener('change', () => {
  showTelemetryState = showTelBasic.checked;
  STORE.setItem(LS.showTelemetry, showTelemetryState ? '1' : '0');
  const telCard = document.getElementById('cardTelemetry');
  if (telCard) telCard.style.display = showTelemetryState ? '' : 'none';
  const basicTel = document.getElementById('basicTelemetryContainer');
  if (basicTel) basicTel.style.display = showTelemetryState ? '' : 'none';
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

function wireTabs() {
  const tabs = document.querySelectorAll('.nav-item');
  const panes = document.querySelectorAll('.tab-pane');

  function switchTab(targetName) {
    if (!isAuthenticated) return;
    // Reset tabs
    tabs.forEach(t => t.classList.remove('active'));
    // Set active tab
    tabs.forEach(t => {
      if (t.dataset.tab === targetName) t.classList.add('active');
    });

    // Reset panes (hide all)
    panes.forEach(p => p.classList.remove('active'));

    // Show target pane
    const targetPane = document.getElementById(`tab-${targetName}`);
    if (targetPane) {
      targetPane.classList.add('active');
    }
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      if (!isAuthenticated) return;
      const target = tab.dataset.tab;
      if (target) switchTab(target);
    });
  });
}

function bootstrapAuthenticatedUi() {
  if (uiBootstrapped) return;
  uiBootstrapped = true;

  const privacy = (STORE.getItem(LS.privacy) || '1') === '1';
  document.getElementById('privacyMode').checked = privacy;
  applyPrivacyMode();

  showTelemetryState = (STORE.getItem(LS.showTelemetry) || '0') === '1';
  if (showTelBasic) showTelBasic.checked = showTelemetryState;

  const auto = (STORE.getItem(LS.auto) || '0') === '1';
  document.getElementById('autoRefresh').checked = auto;

  const every = STORE.getItem(LS.every) || '2000';
  document.getElementById('refreshEvery').value = every;

  const mode = loadUiMode();
  applyUiMode(mode, { skipAdapters: true });

  initCharts();

  // Wire up button listeners
  document.getElementById('btnStart').addEventListener('click', async () => {
    await startHotspot();
  });

  document.getElementById('btnStop').addEventListener('click', async () => {
    await stopHotspot();
  });

  document.getElementById('btnRepair').addEventListener('click', async () => {
    await repairHotspot();
  });

  document.getElementById('btnRestart').addEventListener('click', async () => {
    await restartHotspot();
  });

  const btnToggleRawStatus = document.getElementById('btnToggleRawStatus');
  if (btnToggleRawStatus) {
    btnToggleRawStatus.addEventListener('click', () => {
      const raw = document.getElementById('rawStatusPre');
      if (!raw) return;
      const visible = !raw.classList.contains('is-visible');
      raw.classList.toggle('is-visible', visible);
      btnToggleRawStatus.textContent = visible ? 'Hide raw status JSON' : 'Show raw status JSON';
    });
  }

  const btnCopyEngineCmd = document.getElementById('btnCopyEngineCmd');
  if (btnCopyEngineCmd) {
    btnCopyEngineCmd.addEventListener('click', async () => {
      const text = btnCopyEngineCmd.dataset.copyText || '';
      if (!text) return;
      const ok = await copyToClipboard(text);
      setMsg(ok ? 'Command copied.' : 'Copy failed.', ok ? '' : 'dangerText');
    });
  }
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
  if (btnStopBasic) btnStopBasic.addEventListener('click', async () => {
    await stopHotspot();
  });
  const btnRepairBasic = document.getElementById('btnRepairBasic');
  if (btnRepairBasic) btnRepairBasic.addEventListener('click', async () => {
    await repairHotspot();
  });

  async function saveConfigOnly() {
    const cfg = getForm();
    setMsg('Saving config...');
    const r = await api('/v1/config', { method: 'POST', body: JSON.stringify(cfg) });
    setMsg(r.json ? ('Config: ' + r.json.result_code) : ('Config save failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');

    if (r.ok) {
      setDirty(false);
      cfgJustSaved = true;
      clearPassphraseInputs();
      passphraseDirty = false;
    }
    await refresh();
    return r;
  }


  // Basic Mode Passphrase Logic
  const btnSavePassBasic = document.getElementById('btnSavePassBasic');
  if (btnSavePassBasic) btnSavePassBasic.addEventListener('click', async () => {
    const passField = document.getElementById('wpa2_passphrase_basic'); // Use basic input
    const hint = document.getElementById('copyHint');

    // Check if user entered something
    const val = passField ? passField.value.trim() : '';
    if (!passField || !val) {
      if (hint) {
        hint.textContent = 'Enter a passphrase (8-63 characters)';
        hint.style.color = 'var(--bad)';
      }
      return;
    }

    // Sync to main config object manually since getForm() might strictly check the advanced input
    // But wait, getForm() uses document.getElementById... we should update THAT or sync it here.
    // Easiest: Update the advanced input value so getForm() picks it up if we use that.
    // But better: Update the 'out' object in saveConfigOnly() logic?
    // Let's just update the advanced field to match, trigger dirty, then save.
    const advPass = document.getElementById('wpa2_passphrase');
    if (advPass) advPass.value = val;

    passphraseDirty = true;
    const res = await saveConfigOnly();

    if (hint) {
      if (res && res.ok) {
        hint.textContent = 'Passphrase saved to config';
        hint.style.color = 'var(--good)';
      } else {
        const code = (res && res.json && res.json.result_code) ? res.json.result_code : `HTTP ${res ? res.status : 'error'}`;
        hint.textContent = `Passphrase save failed: ${code}`;
        hint.style.color = 'var(--bad)';
      }
    }
  });

  // Basic Reveal Button
  const btnRevealBasic = document.getElementById('btnRevealPassBasic');
  if (btnRevealBasic) btnRevealBasic.addEventListener('click', async () => {
    const el = document.getElementById('wpa2_passphrase_basic');
    await revealPassphrase(btnRevealBasic, el);
  });

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
    const r1 = await api('/v1/config', { method: 'POST', body: JSON.stringify(cfg) });
    if (!r1.ok) {
      setMsg(r1.json ? ('Config: ' + r1.json.result_code) : ('Config save failed: HTTP ' + r1.status), 'dangerText');
      return;
    }

    setDirty(false);
    cfgJustSaved = true;
    clearPassphraseInputs();
    passphraseDirty = false;

    const r2 = await api('/v1/restart', { method: 'POST' });
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
      updateStabilityChecklist(lastStatus || {});
    });
  }

  wireDirtyTracking();
  wireQosBasic();
  enforceBandRules();
  wireQr();
  wireTabs();

  // Load adapters first so the adapter select is populated before applying config.
  loadAdapters()
    .then(refresh)
    .then(() => {
      applyAutoRefresh();
      return refreshInfo();
    });
}

async function init() {
  wireFloatingTips();
  wireLoginSplash();
  window.addEventListener('hashchange', () => {
    if (!isAuthenticated) clearLoggedOutRouteState();
  });

  const tok = migrateLegacyToken() || getStoredToken();
  if (!tok) {
    renderLoginSplash();
    return;
  }

  setAuthState('pending');
  const result = await validateTokenCandidate(tok);
  if (!result.ok) {
    if (result.reason === 'invalid') {
      logoutToSplash('Invalid token');
    } else if (result.reason === 'network') {
      renderLoginSplash('Network error while validating token');
    } else {
      const code = result.status ? `HTTP ${result.status}` : 'error';
      renderLoginSplash(`Unable to validate token (${code})`);
    }
    return;
  }

  setToken(tok);
  showAuthenticatedApp();
  bootstrapAuthenticatedUi();
}

function wireQr() {
  const modal = document.getElementById('qrModal');
  const place = document.getElementById('qrPlaceholder');
  const rawDiv = document.getElementById('qrSsidRaw');
  if (!modal || !place) return;

  async function resolvePassphraseForQr() {
    const typedPass = getPassphraseValue();
    if (typedPass) return { passphrase: typedPass, source: 'typed' };

    const r = await api('/v1/config/reveal_passphrase', { method: 'POST', body: JSON.stringify({ confirm: true }) });
    if (r.ok && r.json && r.json.data && typeof r.json.data.wpa2_passphrase === 'string' && r.json.data.wpa2_passphrase) {
      return { passphrase: r.json.data.wpa2_passphrase, source: 'saved' };
    }

    const resultCode = (r.json && r.json.result_code) ? r.json.result_code : `HTTP ${r.status}`;
    return { passphrase: '', source: 'error', code: resultCode };
  }

  async function showQr() {
    // Gather SSID and passphrase
    let ssid = document.getElementById('ssid').value.trim();

    // If empty in UI, try fallback to saved config
    if (!ssid && lastCfg && lastCfg.ssid) ssid = lastCfg.ssid;

    if (!ssid) {
      setMsg('SSID is missing', 'dangerText');
      return;
    }

    const passLookup = await resolvePassphraseForQr();
    const pass = (passLookup.passphrase || '').trim();
    if (!pass) {
      if (passLookup.code === 'passphrase_not_set') {
        setMsg('Save a passphrase to generate a QR code.', 'dangerText');
      } else {
        setMsg(`Unable to load saved passphrase for QR code (${passLookup.code || 'error'}).`, 'dangerText');
      }
      const { advanced, basic } = getPassphraseInputs();
      const focusEl = (getUiMode() === 'basic' ? basic : advanced) || advanced || basic;
      if (focusEl) focusEl.focus();
      return;
    }

    // Auth Type: WPA (works for WPA2/3 usually) or WPA2-EAP etc.
    // Schema: WIFI:S:MySSID;T:WPA;P:MyPass;;

    // Escape special chars in SSID/Pass?
    // Standard: 
    // escape: \ -> \\, ; -> \;, , -> \,, : -> \:
    const escape = (s) => s.replace(/([\\;,:])/g, '\\$1');
    const wifiStr = `WIFI:S:${escape(ssid)};T:WPA;P:${escape(pass)};;`;

    place.innerHTML = '';
    rawDiv.textContent = `SSID: ${ssid}`;

    try {
      if (typeof QRCode === 'undefined') {
        place.textContent = 'Error: QRCode library not loaded.';
      } else {
        new QRCode(place, {
          text: wifiStr,
          width: 256,
          height: 256,
          colorDark: "#000000",
          colorLight: "#ffffff",
          correctLevel: QRCode.CorrectLevel.M
        });
      }
      modal.style.display = 'flex';
    } catch (e) {
      place.textContent = 'Error generating QR code.';
      console.error(e);
      modal.style.display = 'flex';
    }
  }

  const btns = [document.getElementById('btnShowQr'), document.getElementById('btnShowQrBasic')];
  for (const b of btns) {
    if (b) b.addEventListener('click', showQr);
  }

  document.getElementById('btnCloseQr').addEventListener('click', () => {
    modal.style.display = 'none';
  });

  // Close on outside click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.style.display = 'none';
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
