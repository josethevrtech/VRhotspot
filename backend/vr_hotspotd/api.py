import copy
import json
import logging
import os
import re
import time
import uuid
import ipaddress
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from vr_hotspotd.adapters.inventory import get_adapters
from vr_hotspotd.config import load_config, write_config_file
from vr_hotspotd.lifecycle import repair, start_hotspot, stop_hotspot, reconcile_state_with_engine
from vr_hotspotd.diagnostics.clients import get_clients_snapshot
from vr_hotspotd.diagnostics.ping import run_ping, ping_available
from vr_hotspotd.diagnostics.load import LoadGenerator
from vr_hotspotd import telemetry
from vr_hotspotd.state import load_state

log = logging.getLogger("vr_hotspotd.api")

# Keep this tight: what the UI is allowed to change on-disk via /v1/config.
_CONFIG_MUTABLE_KEYS = {
    "ssid",
    "wpa2_passphrase",
    "band_preference",
    "country",
    "wifi6",
    "optimized_no_virt",
    "ap_adapter",
    "ap_ready_timeout_s",
    "fallback_channel_2g",
    # NEW:
    "ap_security",   # "wpa2" | "wpa3_sae"
    "channel_6g",    # int (optional)
    # Network
    "lan_gateway_ip",
    "dhcp_start_ip",
    "dhcp_end_ip",
    "dhcp_dns",
    "enable_internet",
    # System tuning
    "wifi_power_save_disable",
    "usb_autosuspend_disable",
    "cpu_governor_performance",
    "cpu_affinity",
    "sysctl_tuning",
    # Watchdog / telemetry / QoS / NAT / bridge
    "watchdog_enable",
    "watchdog_interval_s",
    "telemetry_enable",
    "telemetry_interval_s",
    "qos_preset",
    "nat_accel",
    "bridge_mode",
    "bridge_name",
    "bridge_uplink",
    # Firewall
    "firewalld_enabled",
    "firewalld_zone",
    "firewalld_enable_masquerade",
    "firewalld_enable_forward",
    "firewalld_cleanup_on_stop",
    "debug",
}

# One-shot start overrides (not persisted).
_START_OVERRIDE_KEYS = {
    "ssid",
    "wpa2_passphrase",
    "band_preference",
    "country",
    "wifi6",
    "optimized_no_virt",
    "ap_adapter",
    "ap_ready_timeout_s",
    "fallback_channel_2g",
    # NEW:
    "ap_security",
    "channel_6g",
    # Network
    "lan_gateway_ip",
    "dhcp_start_ip",
    "dhcp_end_ip",
    "dhcp_dns",
    "enable_internet",
    # System tuning
    "wifi_power_save_disable",
    "usb_autosuspend_disable",
    "cpu_governor_performance",
    "cpu_affinity",
    "sysctl_tuning",
    "watchdog_enable",
    "watchdog_interval_s",
    "telemetry_enable",
    "telemetry_interval_s",
    "qos_preset",
    "nat_accel",
    "bridge_mode",
    "bridge_name",
    "bridge_uplink",
    "debug",
}

# Sensitive config keys that should never be returned in cleartext unless explicitly requested.
_SENSITIVE_CONFIG_KEYS = {"wpa2_passphrase"}

# Type coercion (robustness vs. clients sending "true"/"false"/"1"/"0")
_BOOL_KEYS = {
    "optimized_no_virt",
    "enable_internet",
    "wifi_power_save_disable",
    "usb_autosuspend_disable",
    "cpu_governor_performance",
    "sysctl_tuning",
    "watchdog_enable",
    "telemetry_enable",
    "nat_accel",
    "bridge_mode",
    "firewalld_enabled",
    "firewalld_enable_masquerade",
    "firewalld_enable_forward",
    "firewalld_cleanup_on_stop",
    "debug",
}
_INT_KEYS = {"fallback_channel_2g", "channel_6g"}
_FLOAT_KEYS = {"ap_ready_timeout_s", "watchdog_interval_s", "telemetry_interval_s"}
_IP_KEYS = {"lan_gateway_ip", "dhcp_start_ip", "dhcp_end_ip"}

# Country: ISO 3166-1 alpha-2 or "00".
_COUNTRY_RE = re.compile(r"^(00|[A-Z]{2})$")

# Allowed values (normalized)
_ALLOWED_BANDS = {"2.4ghz", "5ghz", "6ghz"}
_ALLOWED_SECURITY = {"wpa2", "wpa3_sae"}
_ALLOWED_QOS = {"off", "vr", "balanced"}

SERVER_VERSION = "vr-hotspotd/0.4"


def _clamp_int(
    value: Any,
    *,
    default: int,
    min_val: int,
    max_val: int,
    warnings: list[str],
    name: str,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{name}_invalid") from exc
    clamped = max(min_val, min(max_val, parsed))
    if clamped != parsed:
        warnings.append(f"{name}_clamped")
    return clamped


def _clamp_float(
    value: Any,
    *,
    default: float,
    min_val: float,
    max_val: float,
    warnings: list[str],
    name: str,
) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"{name}_invalid") from exc
    clamped = max(min_val, min(max_val, parsed))
    if clamped != parsed:
        warnings.append(f"{name}_clamped")
    return clamped


def _classify_ping(ping_result: dict) -> Dict[str, str]:
    if not isinstance(ping_result, dict) or ping_result.get("error"):
        return {"grade": "unusable", "reason": "ping_failed"}
    rtt = ping_result.get("rtt_ms") or {}
    p99_9 = rtt.get("p99_9") if isinstance(rtt, dict) else None
    loss = ping_result.get("packet_loss_pct")

    if p99_9 is None or loss is None:
        return {"grade": "unusable", "reason": "missing_latency_or_loss"}

    if p99_9 <= 20 and loss < 0.5:
        return {"grade": "excellent", "reason": "p99_9<=20ms_and_loss<0.5pct"}
    if p99_9 <= 35 and loss < 1:
        return {"grade": "good", "reason": "p99_9<=35ms_and_loss<1pct"}
    if p99_9 <= 50 and loss < 2:
        return {"grade": "fair", "reason": "p99_9<=50ms_and_loss<2pct"}
    if p99_9 <= 80 and loss < 5:
        return {"grade": "poor", "reason": "p99_9<=80ms_and_loss<5pct"}

    if loss >= 5:
        return {"grade": "unusable", "reason": "loss_ge_5pct"}
    return {"grade": "unusable", "reason": "p99_9_gt_80ms"}

# A compact UI focused on correctness and “sticky” edits.
UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<title>VR Hotspot</title>
<meta name="theme-color" content="#0a0d12" />
<style>
  :root { 
    color-scheme: dark; 
    --bg: #0a0d12;
    --bg-elevated: #11151a;
    --bg-card: #151a20;
    --fg: rgba(255,255,255,.95);
    --fg-muted: rgba(255,255,255,.65);
    --fg-subtle: rgba(255,255,255,.45);
    --bd: rgba(255,255,255,.08);
    --bd-hover: rgba(255,255,255,.15);
    --accent: #5b9fff;
    --accent-hover: #6ba8ff;
    --accent-light: rgba(91,159,255,.15);
    --good: #2fe08b;
    --good-light: rgba(47,224,139,.15);
    --bad: #ff5b5b;
    --bad-light: rgba(255,91,91,.15);
    --warn: #ffb020;
    --warn-light: rgba(255,176,32,.15);
    --shadow-sm: 0 2px 8px rgba(0,0,0,.3);
    --shadow-md: 0 4px 16px rgba(0,0,0,.4);
    --shadow-lg: 0 8px 32px rgba(0,0,0,.5);
    --radius: 12px;
    --radius-sm: 8px;
    --radius-lg: 16px;
    --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { 
    margin: 0; 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
    background: var(--bg);
    background-image: radial-gradient(circle at 20% 50%, rgba(91,159,255,.03) 0%, transparent 50%),
                      radial-gradient(circle at 80% 80%, rgba(47,224,139,.02) 0%, transparent 50%);
    color: var(--fg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  .container { 
    max-width: 1400px; 
    margin: 0 auto; 
    padding: 24px 20px;
  }
  @media (min-width: 768px) {
    .container { padding: 32px 24px; }
  }
  @media (min-width: 1200px) {
    .container { padding: 40px 32px; }
  }

  /* Header */
  .header {
    display: flex;
    flex-direction: column;
    gap: 20px;
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--bd);
  }
  @media (min-width: 768px) {
    .header {
      flex-direction: row;
      justify-content: space-between;
      align-items: flex-start;
    }
  }
  .header-left h1 {
    font-size: 28px;
    font-weight: 700;
    margin: 0 0 6px 0;
    background: linear-gradient(135deg, var(--fg) 0%, var(--fg-muted) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .header-left .subtitle {
    font-size: 14px;
    color: var(--fg-muted);
    margin: 0;
  }
  .header-right {
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 100%;
  }
  @media (min-width: 768px) {
    .header-right {
      width: auto;
      flex-direction: row;
      align-items: center;
      flex-wrap: wrap;
    }
  }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    border-radius: 999px;
    border: 1px solid var(--bd);
    background: var(--bg-card);
    color: var(--fg-muted);
    font-size: 13px;
    font-weight: 500;
    box-shadow: var(--shadow-sm);
    transition: var(--transition);
  }
  .status-pill.ok {
    border-color: var(--good);
    background: var(--good-light);
    color: var(--good);
  }
  .status-pill.err {
    border-color: var(--bad);
    background: var(--bad-light);
    color: var(--bad);
  }
  .status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--warn);
    box-shadow: 0 0 0 3px rgba(255,176,32,.2);
    animation: pulse 2s ease-in-out infinite;
  }
  .status-pill.ok .status-dot {
    background: var(--good);
    box-shadow: 0 0 0 3px rgba(47,224,139,.2);
  }
  .status-pill.err .status-dot {
    background: var(--bad);
    box-shadow: 0 0 0 3px rgba(255,91,91,.2);
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
  }
  .header-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }
  .header-controls-group {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-card);
    border: 1px solid var(--bd);
    border-radius: var(--radius);
  }

  /* Cards */
  .card {
    background: var(--bg-card);
    border: 1px solid var(--bd);
    border-radius: var(--radius-lg);
    padding: 24px;
    margin-bottom: 24px;
    box-shadow: var(--shadow-sm);
    transition: var(--transition);
  }
  .card:hover {
    border-color: var(--bd-hover);
    box-shadow: var(--shadow-md);
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--bd);
  }
  .card-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--fg);
    margin: 0;
  }
  .card-content {
    display: grid;
    gap: 20px;
  }

  /* Grid layouts */
  .grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 20px;
  }
  @media (min-width: 768px) {
    .grid { grid-template-columns: repeat(2, 1fr); }
  }
  @media (min-width: 1200px) {
    .grid-3 { grid-template-columns: repeat(3, 1fr); }
  }
  .grid-full {
    grid-column: 1 / -1;
  }

  /* Form elements */
  .form-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  label {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-muted);
    display: block;
  }
  input, select, textarea {
    width: 100%;
    padding: 12px 14px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bd);
    background: rgba(0,0,0,.3);
    color: var(--fg);
    font-size: 14px;
    font-family: inherit;
    transition: var(--transition);
  }
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--accent);
    background: rgba(0,0,0,.4);
    box-shadow: 0 0 0 3px var(--accent-light);
  }
  input::placeholder {
    color: var(--fg-subtle);
  }
  .form-hint {
    font-size: 12px;
    color: var(--fg-subtle);
    margin-top: 4px;
  }

  /* Buttons */
  .btn {
    padding: 12px 20px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bd);
    background: rgba(255,255,255,.05);
    color: var(--fg);
    font-size: 14px;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    white-space: nowrap;
    transition: var(--transition);
    min-height: 44px;
  }
  .btn:hover:not(:disabled) {
    background: rgba(255,255,255,.1);
    border-color: var(--bd-hover);
    transform: translateY(-1px);
    box-shadow: var(--shadow-sm);
  }
  .btn:active:not(:disabled) {
    transform: translateY(0);
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .btn-primary {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
  }
  .btn-primary:hover:not(:disabled) {
    background: var(--accent-hover);
    border-color: var(--accent-hover);
  }
  .btn-danger {
    background: var(--bad-light);
    border-color: var(--bad);
    color: var(--bad);
  }
  .btn-danger:hover:not(:disabled) {
    background: var(--bad);
    color: white;
  }
  .btn-group {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }

  /* Toggle switches */
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    color: var(--fg-muted);
    font-size: 13px;
    user-select: none;
    cursor: pointer;
  }
  .toggle input[type="checkbox"] {
    width: auto;
    margin: 0;
    cursor: pointer;
  }
  .toggle-group {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
  }

  /* Sections */
  .section {
    margin-bottom: 32px;
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .section-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--fg);
  }
  .collapsible {
    cursor: pointer;
    user-select: none;
  }
  .collapsible-content {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease-out;
  }
  .collapsible-content.expanded {
    max-height: 5000px;
    transition: max-height 0.5s ease-in;
  }
  .collapsible-icon {
    transition: transform 0.3s ease;
    color: var(--fg-muted);
  }
  .collapsible.expanded .collapsible-icon {
    transform: rotate(180deg);
  }

  /* Status and messages */
  .status-meta {
    font-size: 12px;
    color: var(--fg-subtle);
    font-family: ui-monospace, monospace;
  }
  .message {
    padding: 12px 16px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    margin-top: 12px;
  }
  .message-success {
    background: var(--good-light);
    color: var(--good);
    border: 1px solid var(--good);
  }
  .message-error {
    background: var(--bad-light);
    color: var(--bad);
    border: 1px solid var(--bad);
  }
  .message-warning {
    background: var(--warn-light);
    color: var(--warn);
    border: 1px solid var(--warn);
  }

  /* Hints and info boxes */
  .hint {
    margin-top: 8px;
    padding: 12px 14px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bd);
    background: rgba(255,255,255,.03);
    color: var(--fg-muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .hint strong {
    color: var(--fg);
    font-weight: 600;
  }
  .pill-warn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid var(--warn);
    background: var(--warn-light);
    color: var(--warn);
    font-size: 12px;
    font-weight: 500;
  }

  /* Code/mono blocks */
  .mono {
    font-family: ui-monospace, 'SF Mono', 'Monaco', 'Consolas', monospace;
    font-size: 12px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
    background: rgba(0,0,0,.4);
    border: 1px solid var(--bd);
    border-radius: var(--radius-sm);
    padding: 16px;
    max-height: 300px;
    overflow: auto;
    color: var(--fg-muted);
    line-height: 1.6;
  }
  .mono::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  .mono::-webkit-scrollbar-track {
    background: rgba(0,0,0,.2);
    border-radius: 4px;
  }
  .mono::-webkit-scrollbar-thumb {
    background: var(--bd);
    border-radius: 4px;
  }
  .mono::-webkit-scrollbar-thumb:hover {
    background: var(--bd-hover);
  }

  /* Tables */
  .table-wrapper {
    overflow-x: auto;
    margin-top: 16px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--bd);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    min-width: 600px;
  }
  th, td {
    text-align: left;
    padding: 12px 16px;
    border-bottom: 1px solid var(--bd);
    font-size: 13px;
  }
  th {
    color: var(--fg-muted);
    font-weight: 600;
    background: rgba(0,0,0,.2);
    position: sticky;
    top: 0;
  }
  tbody tr {
    transition: var(--transition);
  }
  tbody tr:hover {
    background: rgba(255,255,255,.03);
  }
  tbody tr:last-child td {
    border-bottom: none;
  }
  .muted {
    color: var(--fg-subtle);
  }

  /* Responsive utilities */
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }
  .small {
    font-size: 12px;
    color: var(--fg-muted);
  }
  .text-center {
    text-align: center;
  }
  @media (max-width: 767px) {
    .hide-mobile {
      display: none !important;
    }
    .card {
      padding: 20px 16px;
    }
    .btn-group {
      flex-direction: column;
    }
    .btn-group .btn {
      width: 100%;
    }
  }

  /* Loading states */
  .loading {
    opacity: 0.6;
    pointer-events: none;
  }

  /* Animations */
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .card {
    animation: fadeIn 0.3s ease-out;
  }
</style>
</head>
<body>
<div class="container">
  <header class="header">
    <div class="header-left">
      <h1>VR Hotspot</h1>
      <p class="subtitle">Local control panel for hotspot management</p>
    </div>
    <div class="header-right">
      <div id="pill" class="status-pill">
        <span class="status-dot"></span>
        <span id="pillTxt">Loading…</span>
      </div>
      <div class="header-controls">
        <button id="btnRefresh" class="btn">Refresh</button>
        <label class="toggle" title="Auto refresh">
          <input type="checkbox" id="autoRefresh" />
          <span>Auto</span>
        </label>
        <select id="refreshEvery" title="Auto refresh interval" class="btn" style="padding: 12px 14px; min-width: 80px; background: var(--bg-card);">
          <option value="2000">2s</option>
          <option value="3000">3s</option>
          <option value="5000">5s</option>
          <option value="10000">10s</option>
        </select>
        <label class="toggle" title="Hide logs (recommended while streaming)">
          <input type="checkbox" id="privacyMode" checked />
          <span>Privacy</span>
        </label>
      </div>
      <div class="form-group" style="min-width: 280px;">
        <label for="apiToken">API Token (optional)</label>
        <input id="apiToken" placeholder="Paste token if required" />
        <div class="form-hint">Saved locally in your browser</div>
      </div>
    </div>
  </header>

  <!-- Controls and Status -->
  <div class="grid">
    <div class="card">
      <div class="card-header">
        <h2 class="card-title">Controls</h2>
      </div>
      <div class="card-content">
        <div class="btn-group">
          <button class="btn btn-primary" id="btnStart">Start</button>
          <button class="btn" id="btnStartOverrides">Start (use form)</button>
          <button class="btn btn-danger" id="btnStop">Stop</button>
          <button class="btn" id="btnRestart">Restart</button>
          <button class="btn" id="btnRepair">Repair</button>
        </div>
        <div id="msg" class="message" style="display: none;"></div>
        <div id="dirty" class="message message-warning" style="display: none;"></div>
        <div class="small" style="margin-top: 12px;">
          Polling will not overwrite unsaved edits. Save config to persist changes.
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2 class="card-title">Status</h2>
      </div>
      <div class="card-content">
        <div class="status-meta" id="statusMeta">—</div>
        <div class="mono" id="rawStatus" style="margin-top: 16px;"></div>
      </div>
    </div>
  </div>

  <!-- Configuration -->
  <div class="card">
    <div class="card-header">
      <h2 class="card-title">Configuration</h2>
    </div>
    <div class="card-content">
      <div class="grid">
        <!-- Basic Settings -->
        <div class="form-group grid-full">
          <h3 class="section-title" style="margin-bottom: 16px;">Basic Settings</h3>
        </div>
        <div class="form-group">
          <label for="ssid">SSID</label>
          <input id="ssid" />
        </div>
        <div class="form-group">
          <label for="wpa2_passphrase">Passphrase (8–63 chars)</label>
          <input id="wpa2_passphrase" type="password" placeholder="Type a new passphrase to change it" />
          <div class="row" style="margin-top: 8px;">
            <label class="toggle"><input type="checkbox" id="showPass" /> Show</label>
            <div class="small" id="passHint"></div>
          </div>
        </div>

        <!-- Wireless Settings -->
        <div class="form-group grid-full">
          <h3 class="section-title" style="margin-top: 8px; margin-bottom: 16px;">Wireless Settings</h3>
        </div>
        <div class="form-group">
          <label for="band_preference">Band Preference</label>
          <select id="band_preference">
            <option value="6ghz">6 GHz (Wi-Fi 6E)</option>
            <option value="5ghz">5 GHz</option>
            <option value="2.4ghz">2.4 GHz</option>
          </select>
          <div class="hint" id="bandHint"></div>
        </div>
        <div class="form-group">
          <label for="ap_security">Security</label>
          <select id="ap_security">
            <option value="wpa2">WPA2 (PSK)</option>
            <option value="wpa3_sae">WPA3 (SAE)</option>
          </select>
          <div class="hint" id="secHint"></div>
        </div>
        <div class="form-group" id="sixgBox" style="display:none;">
          <label for="channel_6g">6 GHz Channel (optional)</label>
          <input id="channel_6g" type="number" step="1" min="1" max="233" placeholder="Leave blank for auto" />
          <div class="form-hint">
            If your driver is strict, you may need to set Country above (JP/AU/US etc.) for 6 GHz channels to be available.
          </div>
        </div>
        <div class="form-group">
          <label>Country (Regulatory Domain)</label>
          <div class="grid" style="grid-template-columns: 1fr 120px; gap: 12px;">
            <select id="country_sel" title="Common countries">
              <option value="US">United States (US)</option>
              <option value="JP">Japan (JP)</option>
              <option value="AU">Australia (AU)</option>
              <option value="CA">Canada (CA)</option>
              <option value="GB">United Kingdom (GB)</option>
              <option value="DE">Germany (DE)</option>
              <option value="FR">France (FR)</option>
              <option value="ES">Spain (ES)</option>
              <option value="IT">Italy (IT)</option>
              <option value="NL">Netherlands (NL)</option>
              <option value="SE">Sweden (SE)</option>
              <option value="NO">Norway (NO)</option>
              <option value="DK">Denmark (DK)</option>
              <option value="FI">Finland (FI)</option>
              <option value="CH">Switzerland (CH)</option>
              <option value="AT">Austria (AT)</option>
              <option value="PL">Poland (PL)</option>
              <option value="PT">Portugal (PT)</option>
              <option value="CZ">Czechia (CZ)</option>
              <option value="KR">Korea (KR)</option>
              <option value="SG">Singapore (SG)</option>
              <option value="NZ">New Zealand (NZ)</option>
              <option value="00">World / unset (00)</option>
              <option value="__custom">Custom…</option>
            </select>
            <input id="country" placeholder="US" maxlength="2" title="ISO alpha-2 or 00" />
          </div>
          <div class="form-hint">
            Use the country where the device is physically operating. Kernel enforces channel/power rules.
          </div>
        </div>
        <div class="form-group">
          <label for="ap_adapter">AP Adapter</label>
          <select id="ap_adapter"></select>
          <div class="btn-group" style="margin-top: 8px;">
            <button class="btn" id="btnUseRecommended">Use Recommended</button>
            <button class="btn" id="btnReloadAdapters">Reload Adapters</button>
          </div>
          <div class="small" id="adapterHint" style="margin-top: 8px;"></div>
        </div>
        <div class="form-group">
          <label for="ap_ready_timeout_s">AP Ready Timeout (s)</label>
          <input id="ap_ready_timeout_s" type="number" step="0.1" min="1" />
        </div>
        <div class="form-group">
          <label for="fallback_channel_2g">Fallback 2.4GHz Channel (1–13)</label>
          <input id="fallback_channel_2g" type="number" step="1" min="1" max="13" />
        </div>

        <!-- Network Settings -->
        <div class="form-group grid-full">
          <h3 class="section-title" style="margin-top: 8px; margin-bottom: 16px;">Network Settings</h3>
        </div>
        <div class="form-group">
          <label for="lan_gateway_ip">LAN Gateway IP</label>
          <input id="lan_gateway_ip" placeholder="192.168.68.1" />
          <div class="form-hint">/24 subnet is assumed for now.</div>
        </div>
        <div class="form-group">
          <label for="dhcp_start_ip">DHCP Start IP</label>
          <input id="dhcp_start_ip" placeholder="192.168.68.10" />
        </div>
        <div class="form-group">
          <label for="dhcp_end_ip">DHCP End IP</label>
          <input id="dhcp_end_ip" placeholder="192.168.68.250" />
        </div>
        <div class="form-group">
          <label for="dhcp_dns">DHCP DNS</label>
          <input id="dhcp_dns" placeholder="gateway or 1.1.1.1,8.8.8.8" />
          <div class="form-hint">Use "gateway" (default) or "no" to omit.</div>
        </div>

        <!-- Advanced Settings -->
        <div class="form-group grid-full">
          <h3 class="section-title" style="margin-top: 8px; margin-bottom: 16px;">Advanced Settings</h3>
        </div>
        <div class="form-group">
          <label>Flags</label>
          <div class="toggle-group">
            <label class="toggle"><input type="checkbox" id="optimized_no_virt" /> optimized_no_virt</label>
            <label class="toggle"><input type="checkbox" id="enable_internet" /> enable_internet</label>
            <label class="toggle"><input type="checkbox" id="debug" /> debug</label>
          </div>
        </div>
        <div class="form-group">
          <label>System Tuning</label>
          <div class="toggle-group">
            <label class="toggle"><input type="checkbox" id="wifi_power_save_disable" /> wifi_power_save_disable</label>
            <label class="toggle"><input type="checkbox" id="usb_autosuspend_disable" /> usb_autosuspend_disable</label>
            <label class="toggle"><input type="checkbox" id="cpu_governor_performance" /> cpu_governor_performance</label>
            <label class="toggle"><input type="checkbox" id="sysctl_tuning" /> sysctl_tuning</label>
          </div>
          <div style="margin-top: 12px;">
            <label for="cpu_affinity">CPU Affinity</label>
            <input id="cpu_affinity" placeholder="e.g. 2 or 2-3 or 2,4" />
          </div>
        </div>
        <div class="form-group">
          <label>Telemetry & Watchdog</label>
          <div class="toggle-group">
            <label class="toggle"><input type="checkbox" id="telemetry_enable" /> telemetry_enable</label>
            <label class="toggle"><input type="checkbox" id="watchdog_enable" /> watchdog_enable</label>
          </div>
          <div class="grid" style="margin-top: 12px;">
            <div class="form-group">
              <label for="telemetry_interval_s">Telemetry Interval (s)</label>
              <input id="telemetry_interval_s" type="number" step="0.5" min="0.5" />
            </div>
            <div class="form-group">
              <label for="watchdog_interval_s">Watchdog Interval (s)</label>
              <input id="watchdog_interval_s" type="number" step="0.5" min="0.5" />
            </div>
          </div>
        </div>
        <div class="form-group">
          <label for="qos_preset">QoS Preset</label>
          <select id="qos_preset">
            <option value="off">off</option>
            <option value="vr">vr (DSCP CS5 + cake)</option>
            <option value="balanced">balanced (DSCP AF41 + fq_codel)</option>
          </select>
          <div class="toggle-group" style="margin-top: 12px;">
            <label class="toggle"><input type="checkbox" id="nat_accel" /> nat_accel</label>
          </div>
          <div class="form-hint">DSCP marking is skipped when firewalld is managing rules.</div>
        </div>
        <div class="form-group">
          <label>Bridge Mode</label>
          <div class="toggle-group">
            <label class="toggle"><input type="checkbox" id="bridge_mode" /> bridge_mode</label>
          </div>
          <div class="grid" style="margin-top: 12px;">
            <div class="form-group">
              <label for="bridge_name">Bridge Name</label>
              <input id="bridge_name" placeholder="vrbr0" />
            </div>
            <div class="form-group">
              <label for="bridge_uplink">Bridge Uplink</label>
              <input id="bridge_uplink" placeholder="e.g. eth0" />
            </div>
          </div>
          <div class="form-hint">Bridge mode bypasses NAT/DHCP; AP clients join your LAN.</div>
        </div>
        <div class="form-group">
          <label>Firewall (firewalld)</label>
          <div class="toggle-group">
            <label class="toggle"><input type="checkbox" id="firewalld_enabled" /> enabled</label>
            <label class="toggle"><input type="checkbox" id="firewalld_enable_masquerade" /> masquerade</label>
            <label class="toggle"><input type="checkbox" id="firewalld_enable_forward" /> forward</label>
            <label class="toggle"><input type="checkbox" id="firewalld_cleanup_on_stop" /> cleanup_on_stop</label>
          </div>
        </div>
      </div>

      <div class="btn-group" style="margin-top: 24px;">
        <button class="btn" id="btnApplyVrProfile">Apply VR Profile</button>
        <button class="btn btn-primary" id="btnSaveConfig">Save Config</button>
        <button class="btn btn-primary" id="btnSaveRestart">Save & Restart</button>
      </div>

      <div class="small" style="margin-top: 16px; padding: 12px; background: rgba(255,255,255,.03); border-radius: var(--radius-sm);">
        Security: API never returns passphrases in cleartext. To change passphrase, type a new one then Save.
      </div>
    </div>
  </div>

  <!-- Telemetry -->
  <div class="card">
    <div class="card-header">
      <h2 class="card-title">Telemetry</h2>
    </div>
    <div class="card-content">
      <div class="small">RSSI, bitrate, retries, loss (from station stats).</div>
      <div class="small" id="telemetrySummary" style="margin-top: 12px;"></div>
      <div class="small muted" id="telemetryWarnings" style="margin-top: 8px;"></div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Client</th>
              <th>RSSI</th>
              <th>TX Mbps</th>
              <th>RX Mbps</th>
              <th>Retries %</th>
              <th>Loss %</th>
            </tr>
          </thead>
          <tbody id="telemetryBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Engine Logs -->
  <div class="card">
    <div class="card-header">
      <h2 class="card-title">Engine Logs</h2>
    </div>
    <div class="card-content">
      <div class="small">Logs are hidden while Privacy is ON.</div>
      <div class="mono" id="stdout" style="margin-top: 16px;"></div>
      <div class="mono" id="stderr" style="margin-top: 16px;"></div>
    </div>
  </div>
</div>

<script>
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
  "lan_gateway_ip","dhcp_start_ip","dhcp_end_ip","dhcp_dns","enable_internet",
  "wifi_power_save_disable","usb_autosuspend_disable","cpu_governor_performance","cpu_affinity","sysctl_tuning",
  "telemetry_enable","telemetry_interval_s","watchdog_enable","watchdog_interval_s",
  "qos_preset","nat_accel","bridge_mode","bridge_name","bridge_uplink",
  "firewalld_enabled","firewalld_enable_masquerade","firewalld_enable_forward","firewalld_cleanup_on_stop",
  "debug"
];

function setDirty(v){
  cfgDirty = !!v;
  const el = document.getElementById('dirty');
  if (cfgDirty) {
    el.textContent = '⚠️ Unsaved changes';
    el.className = 'message message-warning';
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
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
  if (!epoch) return '—';
  try{ return new Date(epoch * 1000).toLocaleString(); }catch{ return String(epoch); }
}

function fmtNum(v, digits=1){
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  return n.toFixed(digits);
}

function fmtPct(v){
  return (v === null || v === undefined) ? '—' : fmtNum(v, 1);
}

function fmtDbm(v){
  return (v === null || v === undefined) ? '—' : `${v} dBm`;
}

function fmtMbps(v){
  return (v === null || v === undefined) ? '—' : fmtNum(v, 1);
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
    `loss_avg=${fmtPct(summary.loss_pct_avg)}%`;

  const warns = (t.warnings || []).join(' · ');
  warnEl.textContent = warns ? `warnings: ${warns}` : '';

  const clients = t.clients || [];
  if (!clients.length){
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6;
    td.textContent = 'No clients connected.';
    td.className = 'muted';
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  for (const c of clients){
    const tr = document.createElement('tr');
    const id = (c.mac || '—') + (c.ip ? ` (${c.ip})` : '');
    const cols = [
      id,
      fmtDbm(c.signal_dbm),
      fmtMbps(c.tx_bitrate_mbps),
      fmtMbps(c.rx_bitrate_mbps),
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
  if (!text) {
    el.style.display = 'none';
    return;
  }
  el.textContent = text;
  el.className = 'message';
  if (kind === 'dangerText') {
    el.className = 'message message-error';
  } else if (kind) {
    el.className = 'message message-' + kind;
  }
  el.style.display = '';
}

function setPill(state){
  const pill = document.getElementById('pill');
  const txt  = document.getElementById('pillTxt');
  const running = !!state.running;
  const phase = state.phase || '—';
  const adapter = state.adapter || '—';
  const band = state.band || '—';
  const mode = state.mode || '—';

  pill.classList.remove('ok','err');
  if (running) pill.classList.add('ok');
  else if (phase === 'error') pill.classList.add('err');

  txt.textContent = `running=${running} phase=${phase} adapter=${adapter} band=${band} mode=${mode}`;
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
  return parts.length ? parts.join('/') : '—';
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
      hint.innerHTML = "<span class='pill-warn'>No 6 GHz-capable AP adapter detected</span>";
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

function applyVrProfile(){
  document.getElementById('band_preference').value = '5ghz';
  document.getElementById('ap_security').value = 'wpa2';
  document.getElementById('optimized_no_virt').checked = false;
  document.getElementById('enable_internet').checked = true;
  document.getElementById('wifi_power_save_disable').checked = true;
  document.getElementById('usb_autosuspend_disable').checked = true;
  document.getElementById('cpu_governor_performance').checked = true;
  document.getElementById('sysctl_tuning').checked = true;
  document.getElementById('telemetry_enable').checked = true;
  document.getElementById('telemetry_interval_s').value = '2.0';
  document.getElementById('watchdog_enable').checked = true;
  document.getElementById('watchdog_interval_s').value = '2.0';
  document.getElementById('qos_preset').value = 'vr';
  document.getElementById('nat_accel').checked = true;
  document.getElementById('bridge_mode').checked = false;
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
    enable_internet: document.getElementById('enable_internet').checked,
    wifi_power_save_disable: document.getElementById('wifi_power_save_disable').checked,
    usb_autosuspend_disable: document.getElementById('usb_autosuspend_disable').checked,
    cpu_governor_performance: document.getElementById('cpu_governor_performance').checked,
    sysctl_tuning: document.getElementById('sysctl_tuning').checked,
    telemetry_enable: document.getElementById('telemetry_enable').checked,
    telemetry_interval_s: parseFloat(document.getElementById('telemetry_interval_s').value || '2.0'),
    watchdog_enable: document.getElementById('watchdog_enable').checked,
    watchdog_interval_s: parseFloat(document.getElementById('watchdog_interval_s').value || '2.0'),
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

  const gw = (document.getElementById('lan_gateway_ip').value || '').trim();
  if (gw) out.lan_gateway_ip = gw;

  const dhcpStart = (document.getElementById('dhcp_start_ip').value || '').trim();
  if (dhcpStart) out.dhcp_start_ip = dhcpStart;

  const dhcpEnd = (document.getElementById('dhcp_end_ip').value || '').trim();
  if (dhcpEnd) out.dhcp_end_ip = dhcpEnd;

  const dhcpDns = (document.getElementById('dhcp_dns').value || '').trim();
  if (dhcpDns) out.dhcp_dns = dhcpDns;

  out.cpu_affinity = (document.getElementById('cpu_affinity').value || '').trim();

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
  document.getElementById('lan_gateway_ip').value = (cfg.lan_gateway_ip || '192.168.68.1');
  document.getElementById('dhcp_start_ip').value = (cfg.dhcp_start_ip || '192.168.68.10');
  document.getElementById('dhcp_end_ip').value = (cfg.dhcp_end_ip || '192.168.68.250');
  document.getElementById('dhcp_dns').value = (cfg.dhcp_dns || 'gateway');
  document.getElementById('enable_internet').checked = (cfg.enable_internet !== false);
  document.getElementById('wifi_power_save_disable').checked = !!cfg.wifi_power_save_disable;
  document.getElementById('usb_autosuspend_disable').checked = !!cfg.usb_autosuspend_disable;
  document.getElementById('cpu_governor_performance').checked = !!cfg.cpu_governor_performance;
  document.getElementById('sysctl_tuning').checked = !!cfg.sysctl_tuning;
  document.getElementById('telemetry_enable').checked = (cfg.telemetry_enable !== false);
  document.getElementById('telemetry_interval_s').value = (cfg.telemetry_interval_s ?? 2.0);
  document.getElementById('watchdog_enable').checked = (cfg.watchdog_enable !== false);
  document.getElementById('watchdog_interval_s').value = (cfg.watchdog_interval_s ?? 2.0);
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
    const reg = a.regdom && a.regdom.country ? a.regdom.country : '—';
    const star = (a.ifname === rec) ? '★ ' : '';

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
    `last_op=${s.last_op || '—'} · ${fmtTs(s.last_op_ts)} · cid=${s.last_correlation_id || '—'}`;

  document.getElementById('rawStatus').textContent = JSON.stringify(st.json, null, 2);

  const eng = (s.engine || {});
  const out = (eng.stdout_tail || []).join('
');
  const err = (eng.stderr_tail || []).join('
');
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
  setMsg('Starting…');
  const r = await api('/v1/start', {method:'POST'});
  setMsg(r.json ? ('Start: ' + r.json.result_code) : ('Start failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnStartOverrides').addEventListener('click', async () => {
  const overrides = getForm();
  setMsg('Starting (use form)…');
  const r = await api('/v1/start', {method:'POST', body: JSON.stringify({overrides})});
  setMsg(r.json ? ('Start: ' + r.json.result_code) : ('Start failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnStop').addEventListener('click', async () => {
  setMsg('Stopping…');
  const r = await api('/v1/stop', {method:'POST'});
  setMsg(r.json ? ('Stop: ' + r.json.result_code) : ('Stop failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnRepair').addEventListener('click', async () => {
  setMsg('Repairing…');
  const r = await api('/v1/repair', {method:'POST'});
  setMsg(r.json ? ('Repair: ' + r.json.result_code) : ('Repair failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnRestart').addEventListener('click', async () => {
  setMsg('Restarting…');
  const r = await api('/v1/restart', {method:'POST'});
  setMsg(r.json ? ('Restart: ' + r.json.result_code) : ('Restart failed: HTTP ' + r.status), r.ok ? '' : 'dangerText');
  await refresh();
});

document.getElementById('btnSaveConfig').addEventListener('click', async () => {
  const cfg = getForm();
  setMsg('Saving config…');
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
  applyVrProfile();
  setMsg('VR profile applied (not saved).');
});

document.getElementById('btnSaveRestart').addEventListener('click', async () => {
  const cfg = getForm();
  setMsg('Saving & restarting…');
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

  const auto = (STORE.getItem(LS.auto) || '0') === '1';
  document.getElementById('autoRefresh').checked = auto;

  const every = STORE.getItem(LS.every) || '2000';
  document.getElementById('refreshEvery').value = every;

  wireDirtyTracking();
  enforceBandRules();

  // Load adapters first so the adapter select is populated before applying config.
  loadAdapters().then(refresh).then(applyAutoRefresh);
})();
</script>
</body>
</html>
"""


class APIHandler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION

    def log_message(self, format, *args):
        return

    def _parse_url(self) -> Tuple[str, Dict[str, str]]:
        s = urlsplit(self.path)
        qs_raw = parse_qs(s.query or "", keep_blank_values=True)
        qs: Dict[str, str] = {}
        for k, vals in qs_raw.items():
            if not vals:
                continue
            qs[k] = vals[0]
        return s.path or "/", qs

    def _qbool(self, qs: Dict[str, str], key: str, default: bool = False) -> bool:
        v = (qs.get(key) or "").strip().lower()
        if not v:
            return default
        return v in ("1", "true", "yes", "on", "y")

    def _env_token(self) -> str:
        return (os.environ.get("VR_HOTSPOTD_API_TOKEN") or "").strip()

    def _get_req_token(self) -> str:
        t = (self.headers.get("X-Api-Token") or "").strip()
        if t:
            return t
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return ""

    def _is_authorized(self) -> bool:
        tok = self._env_token()
        if not tok:
            return True
        return self._get_req_token() == tok

    def _require_auth(self, cid: str) -> bool:
        if self._is_authorized():
            return True
        self._respond(
            401,
            self._envelope(
                correlation_id=cid,
                result_code="unauthorized",
                warnings=["missing_or_invalid_token"],
                data={"hint": "Set X-Api-Token header (or Authorization: Bearer <token>)"},
            ),
        )
        return False

    def _send_common_headers(self, content_type: str, length: int):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _respond_raw(self, code: int, raw: bytes, content_type: str = "application/octet-stream"):
        self.send_response(code)
        self._send_common_headers(content_type, len(raw))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            return


    def _respond(self, code: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self._respond_raw(code, raw, "application/json; charset=utf-8")

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _envelope(self, *, correlation_id: str, result_code: str = "ok", data=None, warnings=None):
        return {
            "correlation_id": correlation_id,
            "result_code": result_code,
            "warnings": warnings or [],
            "data": data or {},
        }

    def _cid(self) -> str:
        cid = self.headers.get("X-Correlation-Id")
        return cid.strip() if cid and cid.strip() else str(uuid.uuid4())

    def _read_json_body(self) -> Tuple[Dict[str, Any], list[str]]:
        warnings: list[str] = []
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            length = 0

        if length <= 0:
            return {}, warnings

        if length > 256_000:
            warnings.append("body_too_large")
            return {}, warnings

        try:
            raw = self.rfile.read(length)
        except Exception:
            warnings.append("body_read_failed")
            return {}, warnings

        if not raw:
            return {}, warnings

        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(data, dict):
                return data, warnings
            warnings.append("body_not_object")
            return {}, warnings
        except Exception:
            warnings.append("body_json_parse_failed")
            return {}, warnings

    def _filter_keys(self, data: Dict[str, Any], allow: set) -> Tuple[Dict[str, Any], list[str]]:
        out: Dict[str, Any] = {}
        ignored: list[str] = []
        for k, v in (data or {}).items():
            if k in allow:
                out[k] = v
            else:
                ignored.append(k)
        warnings: list[str] = []
        if ignored:
            warnings.append("ignored_keys:" + ",".join(sorted(ignored)))
        return out, warnings

    def _apply_compat_aliases(self, cfg_in: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accept older/shorter keys from clients and map them to canonical keys.
        """
        if not isinstance(cfg_in, dict):
            return {}

        d = dict(cfg_in)

        alias_map = {
            "forward": "firewalld_enable_forward",
            "masquerade": "firewalld_enable_masquerade",
            "cleanup_on_stop": "firewalld_cleanup_on_stop",
            "firewalld": "firewalld_enabled",
            "adapter": "ap_adapter",
            # NEW:
            "security": "ap_security",
            "channel6g": "channel_6g",
            "channel_6ghz": "channel_6g",
            "qos": "qos_preset",
            "bridge": "bridge_mode",
        }
        for src, dst in alias_map.items():
            if src in d and dst not in d:
                d[dst] = d.pop(src)

        return d

    def _normalize_band(self, v: Any) -> Optional[str]:
        if not isinstance(v, str):
            return None
        s = v.strip().lower()
        if s in ("2", "2g", "2ghz", "2.4", "2.4ghz"):
            return "2.4ghz"
        if s in ("5", "5g", "5ghz"):
            return "5ghz"
        if s in ("6", "6g", "6ghz", "6e", "6ghz_only"):
            return "6ghz"
        return None

    def _normalize_security(self, v: Any) -> Optional[str]:
        if not isinstance(v, str):
            return None
        s = v.strip().lower()
        if s in ("wpa2", "psk", "wpa2_psk", "wpa2-psk"):
            return "wpa2"
        if s in ("wpa3", "sae", "wpa3_sae", "wpa3-sae"):
            return "wpa3_sae"
        return None

    def _normalize_wifi6(self, v: Any) -> Optional[object]:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s == "auto":
                return "auto"
            if s in ("1", "true", "yes", "on", "y"):
                return True
            if s in ("0", "false", "no", "off", "n"):
                return False
        return None

    def _coerce_config_types(self, d: Dict[str, Any]) -> Tuple[Dict[str, Any], list[str]]:
        """
        Coerce common string/number representations into the expected types
        to avoid downstream truthiness bugs.
        """
        out: Dict[str, Any] = dict(d)
        warnings: list[str] = []

        def to_bool(v: Any) -> Optional[bool]:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "true", "yes", "on", "y"):
                    return True
                if s in ("0", "false", "no", "off", "n"):
                    return False
            return None

        for k in list(out.keys()):
            v = out.get(k)

            if k in _BOOL_KEYS:
                b = to_bool(v)
                if b is None and v is not None:
                    warnings.append(f"type_coerce_failed:{k}")
                elif b is not None:
                    out[k] = b

            if k in _INT_KEYS:
                try:
                    if isinstance(v, str):
                        out[k] = int(v.strip(), 10)
                    elif isinstance(v, (int, float)):
                        out[k] = int(v)
                except Exception:
                    warnings.append(f"type_coerce_failed:{k}")

            if k in _FLOAT_KEYS:
                try:
                    if isinstance(v, str):
                        out[k] = float(v.strip())
                    elif isinstance(v, (int, float)):
                        out[k] = float(v)
                except Exception:
                    warnings.append(f"type_coerce_failed:{k}")

            if k == "country":
                if isinstance(v, str):
                    out[k] = v.strip().upper()
                else:
                    warnings.append("invalid_country_type")
                    out.pop(k, None)

            if k == "band_preference":
                nb = self._normalize_band(v)
                if nb:
                    out[k] = nb
                else:
                    warnings.append("invalid_band_preference")
                    out.pop(k, None)

            if k == "ap_security":
                ns = self._normalize_security(v)
                if ns:
                    out[k] = ns
                else:
                    warnings.append("invalid_ap_security")
                    out.pop(k, None)

            if k == "wifi6":
                nv = self._normalize_wifi6(v)
                if nv is None:
                    warnings.append("invalid_wifi6")
                    out.pop(k, None)
                else:
                    out[k] = nv

            if k == "cpu_affinity":
                if isinstance(v, (int, float)):
                    out[k] = str(int(v))
                elif isinstance(v, str):
                    s = v.strip()
                    if not s:
                        out[k] = ""
                    elif s.lower() == "auto":
                        out[k] = "auto"
                    elif not re.match(r"^[0-9,\-\s]+$", s):
                        warnings.append("invalid_cpu_affinity")
                        out.pop(k, None)
                    else:
                        out[k] = s
                elif v is not None:
                    warnings.append("invalid_cpu_affinity")
                    out.pop(k, None)

            if k == "qos_preset":
                if isinstance(v, str):
                    s = v.strip().lower()
                    if s in _ALLOWED_QOS:
                        out[k] = s
                    else:
                        warnings.append("invalid_qos_preset")
                        out.pop(k, None)
                elif v is not None:
                    warnings.append("invalid_qos_preset")
                    out.pop(k, None)

            if k in ("bridge_name", "bridge_uplink"):
                if isinstance(v, str):
                    s = v.strip()
                    if not s:
                        out[k] = ""
                    elif len(s) > 15 or not re.match(r"^[a-zA-Z0-9_.:-]+$", s):
                        warnings.append(f"invalid_{k}")
                        out.pop(k, None)
                    else:
                        out[k] = s
                elif v is not None:
                    warnings.append(f"invalid_{k}")
                    out.pop(k, None)

            if k in _IP_KEYS:
                if isinstance(v, str):
                    s = v.strip()
                elif isinstance(v, (int, float)):
                    s = str(v)
                else:
                    if v is not None:
                        warnings.append(f"invalid_ip:{k}")
                    out.pop(k, None)
                    continue
                if not s:
                    out.pop(k, None)
                else:
                    try:
                        ipaddress.IPv4Address(s)
                        out[k] = s
                    except Exception:
                        warnings.append(f"invalid_ip:{k}")
                        out.pop(k, None)

            if k == "dhcp_dns":
                normalized = None
                if isinstance(v, list):
                    tokens = [str(x).strip() for x in v if str(x).strip()]
                    v = ",".join(tokens) if tokens else ""
                if isinstance(v, str):
                    s = v.strip()
                    if s:
                        low = s.lower()
                        if low in ("gateway", "gw"):
                            normalized = "gateway"
                        elif low in ("no", "none", "off", "false"):
                            normalized = "no"
                        else:
                            ips = [p.strip() for p in s.split(",") if p.strip()]
                            bad = False
                            for ip in ips:
                                try:
                                    ipaddress.IPv4Address(ip)
                                except Exception:
                                    bad = True
                                    break
                            if bad or not ips:
                                warnings.append("invalid_dhcp_dns")
                            else:
                                normalized = ",".join(ips)
                if normalized is None:
                    out.pop(k, None)
                else:
                    out[k] = normalized

        # Validate country format if provided
        if "country" in out:
            cc = out.get("country")
            if not isinstance(cc, str) or not _COUNTRY_RE.match(cc):
                warnings.append("invalid_country_format")
                out.pop("country", None)

        # Validate channel ranges (best-effort)
        if "fallback_channel_2g" in out:
            try:
                ch2 = int(out.get("fallback_channel_2g"))
                if ch2 < 1 or ch2 > 13:
                    warnings.append("fallback_channel_2g_out_of_range")
            except Exception:
                pass

        if "channel_6g" in out:
            try:
                ch6 = int(out.get("channel_6g"))
                if ch6 < 1 or ch6 > 233:
                    warnings.append("channel_6g_out_of_range")
            except Exception:
                pass

        # Validate DHCP range if gateway is provided in this payload.
        gw = out.get("lan_gateway_ip")
        dhcp_start = out.get("dhcp_start_ip")
        dhcp_end = out.get("dhcp_end_ip")
        if gw and dhcp_start and dhcp_end:
            try:
                gw_ip = ipaddress.IPv4Address(gw)
                start_ip = ipaddress.IPv4Address(dhcp_start)
                end_ip = ipaddress.IPv4Address(dhcp_end)
                if (int(start_ip) >= int(end_ip)):
                    warnings.append("dhcp_range_invalid")
                    out.pop("dhcp_start_ip", None)
                    out.pop("dhcp_end_ip", None)
                elif (gw_ip.packed[:3] != start_ip.packed[:3]) or (gw_ip.packed[:3] != end_ip.packed[:3]):
                    warnings.append("dhcp_range_not_in_gateway_subnet")
                    out.pop("dhcp_start_ip", None)
                    out.pop("dhcp_end_ip", None)
            except Exception:
                pass

        # Enforce 6 GHz security invariants at config time (removes a common start failure)
        if out.get("band_preference") == "6ghz":
            if out.get("ap_security") != "wpa3_sae":
                out["ap_security"] = "wpa3_sae"
                warnings.append("auto_set_ap_security_wpa3_sae_for_6ghz")

        return out, warnings

    def _redact_cmd_list(self, cmd: Any) -> Any:
        if not isinstance(cmd, list):
            return cmd
        out = []
        redact_next = False
        for item in cmd:
            s = str(item)
            if redact_next:
                out.append("********")
                redact_next = False
                continue
            if s in ("-p", "--passphrase", "--password", "--psk", "--sae_password", "--sae-passphrase"):
                out.append(s)
                redact_next = True
                continue
            out.append(s)
        return out

    def _redact_lines(self, lines: Any, secrets: list[str]) -> Any:
        if not isinstance(lines, list):
            return []
        out: list[str] = []
        for line in lines:
            s = str(line)
            for sec in secrets:
                if sec:
                    s = s.replace(sec, "********")
            out.append(s)
        return out

    def _status_view(self, *, include_logs: bool) -> Dict[str, Any]:
        reconcile_state_with_engine()
        st = load_state()
        cfg = load_config()

        secrets: list[str] = []
        pw = cfg.get("wpa2_passphrase")
        if isinstance(pw, str) and pw:
            secrets.append(pw)

        out = copy.deepcopy(st)
        telemetry_enabled = bool(cfg.get("telemetry_enable", True))
        if telemetry_enabled:
            interval = cfg.get("telemetry_interval_s", 2.0)
            if out.get("running"):
                out["telemetry"] = telemetry.get_snapshot(
                    adapter_ifname=out.get("adapter"),
                    enabled=True,
                    interval_s=float(interval) if interval is not None else 2.0,
                )
            else:
                out["telemetry"] = {
                    "enabled": True,
                    "clients": [],
                    "summary": {"client_count": 0},
                    "warnings": ["not_running"],
                }
        else:
            out["telemetry"] = {"enabled": False}
        eng = out.get("engine") if isinstance(out, dict) else None
        if isinstance(eng, dict):
            eng["cmd"] = self._redact_cmd_list(eng.get("cmd"))
            if include_logs:
                eng["stdout_tail"] = self._redact_lines(eng.get("stdout_tail"), secrets)
                eng["stderr_tail"] = self._redact_lines(eng.get("stderr_tail"), secrets)
            else:
                eng["stdout_tail"] = []
                eng["stderr_tail"] = []
            if "ap_logs_tail" in eng:
                eng["ap_logs_tail"] = self._redact_lines(eng.get("ap_logs_tail"), secrets)
        return out

    def _config_view(self, *, include_secrets: bool) -> Dict[str, Any]:
        cfg = load_config()
        out = copy.deepcopy(cfg)
        redacted = False
        if not include_secrets:
            for k in _SENSITIVE_CONFIG_KEYS:
                if k in out:
                    out[k] = ""
                    redacted = True
            out["_wpa2_passphrase_redacted"] = redacted
        else:
            out["_wpa2_passphrase_redacted"] = False
        return out

    def _handle_config_update(self, cid: str, body: Dict[str, Any], body_warnings: list[str]):
        if not self._require_auth(cid):
            return

        if isinstance(body.get("config"), dict):
            cfg_in = body.get("config")  # type: ignore[assignment]
        elif isinstance(body.get("data"), dict):
            cfg_in = body.get("data")
        else:
            cfg_in = body

        if not isinstance(cfg_in, dict):
            cfg_in = {}

        cfg_in = self._apply_compat_aliases(cfg_in)

        filtered, warnings = self._filter_keys(cfg_in or {}, _CONFIG_MUTABLE_KEYS)
        warnings = body_warnings + warnings

        filtered, w_coerce = self._coerce_config_types(filtered)
        warnings += w_coerce

        # If passphrase is present but empty/whitespace, ignore it (treat as "no change").
        if "wpa2_passphrase" in filtered:
            pw = filtered.get("wpa2_passphrase")
            if isinstance(pw, str) and not pw.strip():
                filtered.pop("wpa2_passphrase", None)
                warnings.append("ignored_empty_passphrase")

        if not filtered:
            self._respond(
                400,
                self._envelope(
                    correlation_id=cid,
                    result_code="invalid_request",
                    warnings=warnings + ["no_mutable_keys_provided"],
                    data={"allowed_keys": sorted(_CONFIG_MUTABLE_KEYS)},
                ),
            )
            return

        if "wpa2_passphrase" in filtered:
            pw = filtered.get("wpa2_passphrase")
            if not isinstance(pw, str) or len(pw) < 8:
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_passphrase_min_length_8",
                        warnings=warnings,
                    ),
                )
                return

        try:
            merged = write_config_file(filtered)
            merged_view = self._config_view(include_secrets=False)
            for k, v in merged.items():
                if k not in _SENSITIVE_CONFIG_KEYS:
                    merged_view[k] = v
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code="config_saved",
                    data=merged_view,
                    warnings=warnings,
                ),
            )
        except Exception as e:
            self._respond(
                500,
                self._envelope(
                    correlation_id=cid,
                    result_code="config_write_failed",
                    warnings=warnings + [str(e)],
                ),
            )

    def do_GET(self):
        cid = self._cid()
        path, qs = self._parse_url()

        if path not in ("/healthz", "/favicon.ico"):
            log.info("request", extra={"correlation_id": cid, "method": "GET", "path": self.path})

        if path in ("/", "/ui"):
            if path == "/":
                self._redirect("/ui")
                return
            self._respond_raw(200, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/favicon.ico":
            self._respond_raw(204, b"", "text/plain; charset=utf-8")
            return

        if path == "/healthz":
            self._respond_raw(200, b"ok\n", "text/plain; charset=utf-8")
            return

        if path == "/v1/status":
            include_logs = self._qbool(qs, "include_logs", False)
            if not self._require_auth(cid):
                return
            st = self._status_view(include_logs=include_logs)
            self._respond(200, self._envelope(correlation_id=cid, data=st))
            return

        if path == "/v1/adapters":
            if not self._require_auth(cid):
                return
            self._respond(200, self._envelope(correlation_id=cid, data=get_adapters()))
            return

        if path == "/v1/config":
            if not self._require_auth(cid):
                return
            include_secrets = self._qbool(qs, "include_secrets", False)
            cfg = self._config_view(include_secrets=include_secrets)
            self._respond(200, self._envelope(correlation_id=cid, data=cfg))
            return

        if path == "/v1/info":
            if not self._require_auth(cid):
                return
            data = {
                "server_version": SERVER_VERSION,
                "ts": int(time.time()),
                "pid": os.getpid(),
                "bind_host": os.environ.get("VR_HOTSPOTD_HOST", ""),
                "bind_port": os.environ.get("VR_HOTSPOTD_PORT", ""),
                "token_configured": bool(self._env_token()),
            }
            self._respond(200, self._envelope(correlation_id=cid, data=data))
            return

        if path == "/v1/diagnostics/clients":
            if not self._require_auth(cid):
                return
            st = load_state()
            ap_ifname = st.get("adapter")
            snapshot = get_clients_snapshot(ap_ifname if ap_ifname else None)
            self._respond(200, self._envelope(correlation_id=cid, data=snapshot))
            return

        self._respond(
            404,
            self._envelope(
                correlation_id=cid,
                result_code="not_found",
                warnings=["unknown_endpoint"],
            ),
        )

    def do_POST(self):
        cid = self._cid()
        path, _qs = self._parse_url()
        log.info("request", extra={"correlation_id": cid, "method": "POST", "path": self.path})

        if not self._require_auth(cid):
            return

        body, body_warnings = self._read_json_body()

        if path == "/v1/start":
            overrides_raw: Optional[Dict[str, Any]] = None
            if isinstance(body.get("overrides"), dict):
                overrides_raw = body.get("overrides")  # type: ignore[assignment]
            elif body:
                overrides_raw = body

            if isinstance(overrides_raw, dict):
                overrides_raw = self._apply_compat_aliases(overrides_raw)

                # Ignore empty passphrase (treat as "no change")
                if "wpa2_passphrase" in overrides_raw:
                    pw = overrides_raw.get("wpa2_passphrase")
                    if isinstance(pw, str) and not pw.strip():
                        overrides_raw = dict(overrides_raw)
                        overrides_raw.pop("wpa2_passphrase", None)

            overrides, warnings = self._filter_keys(overrides_raw or {}, _START_OVERRIDE_KEYS)
            warnings = body_warnings + warnings
            overrides, w_coerce = self._coerce_config_types(overrides)
            warnings += w_coerce

            res = start_hotspot(correlation_id=cid, overrides=overrides if overrides else None)
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code=res.code,
                    data=self._status_view(include_logs=False),
                    warnings=warnings,
                ),
            )
            return

        if path == "/v1/stop":
            res = stop_hotspot(correlation_id=cid)
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code=res.code,
                    data=self._status_view(include_logs=False),
                    warnings=body_warnings,
                ),
            )
            return

        if path == "/v1/repair":
            repair(correlation_id=cid)
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code="repaired",
                    data=self._status_view(include_logs=False),
                    warnings=body_warnings,
                ),
            )
            return

        if path == "/v1/restart":
            warnings = list(body_warnings)

            try:
                stop_hotspot(correlation_id=cid + ":stop")
            except Exception:
                warnings.append("stop_failed_ignored")

            try:
                repair(correlation_id=cid + ":repair")
            except Exception:
                warnings.append("repair_failed_ignored")

            overrides_raw: Optional[Dict[str, Any]] = None
            if isinstance(body.get("overrides"), dict):
                overrides_raw = body.get("overrides")  # type: ignore[assignment]
            elif body:
                overrides_raw = body

            if isinstance(overrides_raw, dict):
                overrides_raw = self._apply_compat_aliases(overrides_raw)

                if "wpa2_passphrase" in overrides_raw:
                    pw = overrides_raw.get("wpa2_passphrase")
                    if isinstance(pw, str) and not pw.strip():
                        overrides_raw = dict(overrides_raw)
                        overrides_raw.pop("wpa2_passphrase", None)

            overrides, w2 = self._filter_keys(overrides_raw or {}, _START_OVERRIDE_KEYS)
            warnings += w2
            overrides, w_coerce = self._coerce_config_types(overrides)
            warnings += w_coerce

            res = start_hotspot(correlation_id=cid + ":start", overrides=overrides if overrides else None)
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code="restarted:" + res.code,
                    data=self._status_view(include_logs=False),
                    warnings=warnings,
                ),
            )
            return

        if path == "/v1/diagnostics/ping_under_load":
            warnings = list(body_warnings)
            if not isinstance(body, dict):
                body = {}

            target_ip = str(body.get("target_ip") or "").strip()
            load_cfg = body.get("load") if isinstance(body.get("load"), dict) else {}

            try:
                duration_s = _clamp_int(
                    body.get("duration_s"),
                    default=10,
                    min_val=3,
                    max_val=20,
                    warnings=warnings,
                    name="duration_s",
                )
                interval_ms = _clamp_int(
                    body.get("interval_ms"),
                    default=20,
                    min_val=10,
                    max_val=200,
                    warnings=warnings,
                    name="interval_ms",
                )
            except ValueError:
                data = {
                    "target_ip": target_ip,
                    "duration_s": 10,
                    "interval_ms": 20,
                    "load": {
                        "method": "none",
                        "requested_mbps": 0.0,
                        "effective_mbps": 0.0,
                        "notes": [],
                        "started": False,
                    },
                    "ping": {"error": {"code": "invalid_params", "message": "invalid duration/interval"}},
                    "classification": {"grade": "unusable", "reason": "invalid_params"},
                    "error": {"code": "invalid_params", "message": "invalid duration/interval"},
                }
                self._respond(400, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            try:
                ipaddress.IPv4Address(target_ip)
            except Exception:
                data = {
                    "target_ip": target_ip,
                    "duration_s": duration_s,
                    "interval_ms": interval_ms,
                    "load": {
                        "method": "none",
                        "requested_mbps": 0.0,
                        "effective_mbps": 0.0,
                        "notes": [],
                        "started": False,
                    },
                    "ping": {"error": {"code": "invalid_ip", "message": "invalid IPv4 address"}},
                    "classification": {"grade": "unusable", "reason": "invalid_ip"},
                    "error": {"code": "invalid_ip", "message": "invalid IPv4 address"},
                }
                self._respond(400, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            method = str(load_cfg.get("method") or "curl").strip().lower()
            if method not in ("curl", "iperf3"):
                data = {
                    "target_ip": target_ip,
                    "duration_s": duration_s,
                    "interval_ms": interval_ms,
                    "load": {
                        "method": "none",
                        "requested_mbps": 0.0,
                        "effective_mbps": 0.0,
                        "notes": [],
                        "started": False,
                    },
                    "ping": {"error": {"code": "invalid_params", "message": "invalid load method"}},
                    "classification": {"grade": "unusable", "reason": "invalid_params"},
                    "error": {"code": "invalid_params", "message": "invalid load method"},
                }
                self._respond(400, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            try:
                mbps = _clamp_float(
                    load_cfg.get("mbps"),
                    default=150.0,
                    min_val=10.0,
                    max_val=400.0,
                    warnings=warnings,
                    name="mbps",
                )
            except ValueError:
                data = {
                    "target_ip": target_ip,
                    "duration_s": duration_s,
                    "interval_ms": interval_ms,
                    "load": {
                        "method": "none",
                        "requested_mbps": 0.0,
                        "effective_mbps": 0.0,
                        "notes": [],
                        "started": False,
                    },
                    "ping": {"error": {"code": "invalid_params", "message": "invalid mbps"}},
                    "classification": {"grade": "unusable", "reason": "invalid_params"},
                    "error": {"code": "invalid_params", "message": "invalid mbps"},
                }
                self._respond(400, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            url = str(load_cfg.get("url") or "").strip()
            iperf3_host = str(load_cfg.get("iperf3_host") or "").strip()
            try:
                iperf3_port = int(load_cfg.get("iperf3_port") or 5201)
            except Exception:
                data = {
                    "target_ip": target_ip,
                    "duration_s": duration_s,
                    "interval_ms": interval_ms,
                    "load": {
                        "method": "none",
                        "requested_mbps": 0.0,
                        "effective_mbps": 0.0,
                        "notes": [],
                        "started": False,
                    },
                    "ping": {"error": {"code": "invalid_params", "message": "invalid iperf3_port"}},
                    "classification": {"grade": "unusable", "reason": "invalid_params"},
                    "error": {"code": "invalid_params", "message": "invalid iperf3_port"},
                }
                self._respond(400, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            if not ping_available():
                ping_result = {"error": {"code": "ping_not_found", "message": "ping not found in PATH"}}
                data = {
                    "target_ip": target_ip,
                    "duration_s": duration_s,
                    "interval_ms": interval_ms,
                    "load": {
                        "method": "none",
                        "requested_mbps": float(mbps),
                        "effective_mbps": 0.0,
                        "notes": ["ping_not_available"],
                        "started": False,
                    },
                    "ping": ping_result,
                    "classification": _classify_ping(ping_result),
                    "error": {"code": "ping_failed", "message": "ping not found in PATH"},
                }
                self._respond(200, self._envelope(correlation_id=cid, result_code="error", data=data, warnings=warnings))
                return

            load_gen = LoadGenerator(
                method=method,
                mbps=mbps,
                duration_s=duration_s,
                url=url,
                iperf3_host=iperf3_host,
                iperf3_port=iperf3_port,
            )

            ping_result: dict
            error_obj = None
            try:
                load_gen.start()
                ping_result = run_ping(
                    target_ip=target_ip,
                    duration_s=duration_s,
                    interval_ms=interval_ms,
                )

                if ping_result.get("error"):
                    error_obj = {"code": "ping_failed", "message": ping_result["error"].get("message", "ping failed")}
                else:
                    loss = ping_result.get("packet_loss_pct")
                    if isinstance(loss, (int, float)) and loss > 5:
                        load_gen.stop()
                        warnings.append("load_aborted_due_to_loss")
            finally:
                load_gen.stop()

            load_info = load_gen.info()
            if not load_info.get("started"):
                warnings.append("load_not_started")
                if not error_obj:
                    error_obj = {"code": "load_unavailable", "message": "load generator not started"}

            classification = _classify_ping(ping_result)
            result_code = "ok" if not error_obj or error_obj.get("code") == "load_unavailable" else "error"

            data = {
                "target_ip": target_ip,
                "duration_s": duration_s,
                "interval_ms": interval_ms,
                "load": load_info,
                "ping": ping_result,
                "classification": classification,
                "error": error_obj,
            }
            self._respond(200, self._envelope(correlation_id=cid, result_code=result_code, data=data, warnings=warnings))
            return

        if path == "/v1/config":
            self._handle_config_update(cid, body, body_warnings)
            return

        if path == "/v1/diagnostics/ping":
            target_ip = (body.get("target_ip") or "").strip() if isinstance(body, dict) else ""
            duration_s = body.get("duration_s") if isinstance(body, dict) else None
            interval_ms = body.get("interval_ms") if isinstance(body, dict) else None
            timeout_s = body.get("timeout_s") if isinstance(body, dict) else None

            try:
                ipaddress.IPv4Address(target_ip)
            except Exception:
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_request",
                        warnings=body_warnings + ["invalid_target_ip"],
                    ),
                )
                return

            try:
                duration_s = int(duration_s) if duration_s is not None else 10
            except Exception:
                duration_s = 10
            try:
                interval_ms = int(interval_ms) if interval_ms is not None else 20
            except Exception:
                interval_ms = 20
            try:
                timeout_s = int(timeout_s) if timeout_s is not None else 2
            except Exception:
                timeout_s = 2

            res = run_ping(
                target_ip=target_ip,
                duration_s=duration_s,
                interval_ms=interval_ms,
                timeout_s=timeout_s,
            )
            self._respond(200, self._envelope(correlation_id=cid, data=res))
            return

        self._respond(
            404,
            self._envelope(
                correlation_id=cid,
                result_code="not_found",
                warnings=["unknown_endpoint"],
            ),
        )

    def do_PUT(self):
        cid = self._cid()
        path, _qs = self._parse_url()
        log.info("request", extra={"correlation_id": cid, "method": "PUT", "path": self.path})

        if not self._require_auth(cid):
            return

        body, body_warnings = self._read_json_body()

        if path == "/v1/config":
            self._handle_config_update(cid, body, body_warnings)
            return

        self._respond(
            404,
            self._envelope(
                correlation_id=cid,
                result_code="not_found",
                warnings=["unknown_endpoint"],
            ),
        )
