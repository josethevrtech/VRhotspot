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
from vr_hotspotd.diagnostics.udp_latency import run_udp_latency_test
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
    "channel_width",  # "auto" | "20" | "40" | "80" | "160"
    "beacon_interval",  # int (TU, default 50)
    "dtim_period",  # int (1-255, default 1)
    "short_guard_interval",  # bool
    "tx_power",  # int (dBm) or None for auto
    "channel_auto_select",  # bool
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
    "irq_affinity",  # IRQ affinity for network interfaces
    "interrupt_coalescing",  # bool
    "tcp_low_latency",  # bool
    "memory_tuning",  # bool
    "io_scheduler_optimize",  # bool
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
    "connection_quality_monitoring",  # bool
    "auto_channel_switch",  # bool
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
    "channel_width",
    "beacon_interval",
    "dtim_period",
    "short_guard_interval",
    "tx_power",
    "channel_auto_select",
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
    "irq_affinity",
    "interrupt_coalescing",
    "tcp_low_latency",
    "memory_tuning",
    "io_scheduler_optimize",
    "watchdog_enable",
    "watchdog_interval_s",
    "telemetry_enable",
    "telemetry_interval_s",
    "qos_preset",
    "nat_accel",
    "bridge_mode",
    "bridge_name",
    "bridge_uplink",
    "connection_quality_monitoring",
    "auto_channel_switch",
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
    "short_guard_interval",
    "channel_auto_select",
    "connection_quality_monitoring",
    "auto_channel_switch",
    "interrupt_coalescing",
    "tcp_low_latency",
    "memory_tuning",
    "io_scheduler_optimize",
}
_INT_KEYS = {"fallback_channel_2g", "channel_6g", "beacon_interval", "dtim_period", "tx_power"}
_FLOAT_KEYS = {"ap_ready_timeout_s", "watchdog_interval_s", "telemetry_interval_s"}
_IP_KEYS = {"lan_gateway_ip", "dhcp_start_ip", "dhcp_end_ip"}

# Country: ISO 3166-1 alpha-2 or "00".
_COUNTRY_RE = re.compile(r"^(00|[A-Z]{2})$")

# Allowed values (normalized)
_ALLOWED_BANDS = {"2.4ghz", "5ghz", "6ghz"}
_ALLOWED_SECURITY = {"wpa2", "wpa3_sae"}
_ALLOWED_QOS = {"off", "vr", "balanced", "ultra_low_latency", "high_throughput"}

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

def _resolve_asset_path(asset_name: str) -> Optional[str]:
    """Resolve asset file path, trying install path first, then dev path."""
    # Install path: /var/lib/vr-hotspot/app/assets/...
    install_path = os.path.join("/var/lib/vr-hotspot/app/assets", asset_name)
    if os.path.isfile(install_path):
        return install_path
    # Dev path: resolve relative to backend/vr_hotspotd/api.py -> repo root/assets/...
    api_file = os.path.abspath(__file__)
    # backend/vr_hotspotd/api.py -> backend/vr_hotspotd -> backend -> repo root
    backend_dir = os.path.dirname(os.path.dirname(api_file))
    repo_root = os.path.dirname(backend_dir)
    dev_path = os.path.join(repo_root, "assets", asset_name)
    if os.path.isfile(dev_path):
        return dev_path
    return None


# A compact UI focused on correctness and “sticky” edits.
UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<title>VR Hotspot</title>
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg" />
<meta name="theme-color" content="#000000" />
<style>
  :root { color-scheme: dark;
          --bg0: #000000; --bg1: #050505; --panel: rgba(13, 17, 23, 0.85);
          --text: rgba(255,255,255,.95); --muted: rgba(255,255,255,.75);
          --border: rgba(0, 217, 255, 0.2); --accent: #00d9ff; --accent2: #ffb020;
          --good: #00ff88; --bad: #ff4444;
          --shadow-sm: 0 2px 8px rgba(0,0,0,.3); --shadow-md: 0 4px 16px rgba(0,0,0,.4);
          --shadow-glow: 0 0 12px rgba(0, 217, 255, 0.4);
          --transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
          --transition-fast: all 0.15s cubic-bezier(0.4, 0, 0.2, 1); }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; 
         background: #000000;
         color:var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 16px 12px; }
  @media (min-width: 768px) { .wrap { padding: 24px 20px; } }
  @media (max-width: 480px) { 
    .wrap { padding: 12px 10px; }
    h1 { font-size: 18px; }
    .row { gap: 8px; }
    .card { padding: 14px; margin-top: 12px; }
    button { padding: 10px 14px; font-size: 13px; min-height: 44px; }
  }
  .row { display:flex; gap:10px; flex-wrap: wrap; align-items: center; }
  @media (max-width: 480px) {
    .row { gap: 8px; }
    .row[style*="justify-content: space-between"] { flex-direction: column; align-items: flex-start; gap: 16px; }
    .row[style*="justify-content:flex-end"] { flex-direction: column; align-items: stretch; gap: 12px; }
    .row[style*="justify-content:flex-end"] > * { width: 100%; }
    .row[style*="justify-content:flex-end"] button,
    .row[style*="justify-content:flex-end"] select { width: 100%; }
    .row[style*="justify-content:flex-end"] > div[style*="min-width"] { min-width: 100% !important; }
    .row[style*="flex-wrap: wrap"] { flex-direction: column; align-items: stretch; }
    .row[style*="flex-wrap: wrap"] button,
    .row[style*="flex-wrap: wrap"] select,
    .row[style*="flex-wrap: wrap"] label { width: 100%; }
    .row[style*="flex-wrap: wrap"] > div[style*="min-width"] { min-width: 100% !important; max-width: 100% !important; }
    .card .row button { width: 100%; }
    h1 { margin-bottom: 8px; }
  }
  .card { border:1px solid var(--border); background: var(--panel); border-radius: 2px; padding: 16px; margin-top: 16px;
          box-shadow: var(--shadow-sm); transition: var(--transition); position: relative;
          background-image: linear-gradient(to bottom, rgba(0, 217, 255, 0.02), transparent); }
  .card::before { content: ''; position: absolute; top: 0; left: 0; width: 8px; height: 8px;
                  border-top: 1px solid var(--accent); border-left: 1px solid var(--accent); opacity: 0.6; }
  .card::after { content: ''; position: absolute; bottom: 0; right: 0; width: 8px; height: 8px;
                 border-bottom: 1px solid var(--accent); border-right: 1px solid var(--accent); opacity: 0.6; }
  .card:hover { box-shadow: var(--shadow-md), var(--shadow-glow); border-color: var(--accent); }
  h1 { font-size: 20px; margin:0 0 4px 0; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em; }
  @media (min-width: 768px) { h1 { font-size: 24px; } }
  .brand-logo { height: 52px; width: auto; margin: 0 0 4px 0; display: block; }
  @media (min-width: 768px) { .brand-logo { height: 60px; } }
  h2 { font-size: 15px; margin:0 0 12px 0; color: var(--text); font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
  label { font-size: 12px; color: var(--muted); display:block; margin-bottom: 6px; font-weight: 500; transition: var(--transition-fast); }
  .field:focus-within > label, .field:focus-within label { color: var(--accent); }
  input, select { width:100%; padding:10px 12px; border-radius: 2px; border:1px solid var(--border); 
                  background: rgba(0,0,0,.4); color: var(--text); font-size: 14px;
                  transition: var(--transition); }
  input:focus, select:focus { outline: none; border-color: var(--accent); background: rgba(0,0,0,.5);
                               box-shadow: 0 0 0 2px rgba(0, 217, 255, 0.3); }
  input::placeholder { color: rgba(255,255,255,.35); }
  button { padding:10px 16px; border-radius: 2px; border:2px solid var(--border); 
           background: rgba(0, 0, 0, 0.3); color: var(--text);
           cursor:pointer; font-weight: 600; min-height: 44px; 
           display:inline-flex; align-items:center; justify-content:center; white-space: nowrap;
           transition: var(--transition); font-size: 14px; position: relative; }
  button:hover:not(:disabled) { background: rgba(0, 217, 255, 0.1); border-color: var(--accent);
                                  box-shadow: var(--shadow-glow); transform: translateY(-1px); }
  button:active:not(:disabled) { transform: translateY(0); box-shadow: 0 0 6px rgba(0, 217, 255, 0.3); }
  button:focus-visible { outline: none; box-shadow: 0 0 0 2px rgba(0, 217, 255, 0.5); }
  button.primary { border-color: var(--accent); background: rgba(0, 217, 255, 0.1); }
  button.primary:hover:not(:disabled) { background: rgba(0, 217, 255, 0.15); border-color: var(--accent);
                                        box-shadow: var(--shadow-glow); }
  button.danger  { border-color: var(--bad); background: rgba(255, 68, 68, 0.1); }
  button.danger:hover:not(:disabled) { background: rgba(255, 68, 68, 0.15); border-color: var(--bad);
                                       box-shadow: 0 0 12px rgba(255, 68, 68, 0.4); }
  button:disabled{ opacity:.5; cursor:not-allowed; }
  .pill { display:inline-flex; gap:8px; align-items:center; padding:10px 16px; border-radius: 2px; 
          border:1px solid var(--border); background: rgba(0, 0, 0, 0.3); color: var(--muted); 
          max-width:100%; box-shadow: var(--shadow-sm); transition: var(--transition);
          font-size: 13px; font-weight: 500; }
  @media (max-width: 767px) {
    .pill { padding: 8px 12px; font-size: 12px; }
    .card .row[style*="flex-wrap: wrap"] { flex-wrap: wrap; }
    .card .row[style*="flex-wrap: wrap"] > div[style*="min-width"] { min-width: 100% !important; max-width: 100% !important; }
    #apiToken { width: 100% !important; }
  }
  .dot { width:10px; height:10px; border-radius:999px; background: var(--accent2); 
         box-shadow: 0 0 0 3px rgba(255, 176, 32, 0.2); animation: pulse 2.5s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.6; transform: scale(0.95); } }
  .pill.ok { border-color: var(--good); background: rgba(0, 255, 136, 0.1); color: var(--good); }
  .pill.ok .dot  { background: var(--good); box-shadow: 0 0 0 3px rgba(0, 255, 136, 0.25);
                   animation: pulse 2s ease-in-out infinite; }
  .pill.err { border-color: var(--bad); background: rgba(255, 68, 68, 0.1); color: var(--bad); }
  .pill.err .dot { background: var(--bad); box-shadow: 0 0 0 3px rgba(255, 68, 68, 0.25);
                   animation: pulse 1.5s ease-in-out infinite; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media(max-width: 900px){ .grid{ grid-template-columns: 1fr; } }
  .controlsGrid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }
  @media (max-width: 900px) { .controlsGrid { grid-template-columns: 1fr; } }
  .controlsLeft, .controlsRight { display: flex; flex-direction: column; }
  .controlsRight h3 { font-size: 13px; margin: 0 0 12px 0; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text); }
  .topbar { padding: 12px 0; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  @media (max-width: 900px) { .topbar { padding: 8px 0; } }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
          font-size: 12px; white-space: pre-wrap; overflow-wrap:anywhere; word-break: break-word;
          background: rgba(0,0,0,.4); border:1px solid var(--border); border-radius: 2px; padding: 12px; 
          max-height: 260px; overflow:auto; line-height: 1.6; }
  .mono::-webkit-scrollbar { width: 8px; height: 8px; }
  .mono::-webkit-scrollbar-track { background: rgba(0,0,0,.3); border-radius: 2px; }
  .mono::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .mono::-webkit-scrollbar-thumb:hover { background: var(--accent); }
  .small { font-size: 12px; color: var(--muted); }
  .tog { display:inline-flex; align-items:center; gap:8px; color: var(--muted); font-size: 13px; user-select:none; }
  .tog input { width:auto; }
  .dangerText { color: var(--bad); }
  .hint { margin-top:8px; padding:10px 12px; border-radius: 2px; border:1px solid var(--border); 
          background: rgba(0, 217, 255, 0.05); color: var(--muted); line-height: 1.5; }
  .hint strong { color: var(--text); font-weight: 600; }
  .two { display:grid; grid-template-columns: 1fr 220px; gap: 10px; }
  @media(max-width: 900px){ .two{ grid-template-columns: 1fr; } }
  .pillWarn { display:inline-flex; gap:8px; align-items:center; padding:6px 12px; border-radius: 2px; 
              border:1px solid var(--accent2); background: rgba(255, 176, 32, 0.1); 
              color: var(--accent2); font-weight: 500; }
  table { width:100%; border-collapse: collapse; }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--border); font-size:12px; }
  th { color: var(--muted); font-weight:600; background: rgba(0,0,0,.25); position: sticky; top: 0;
       border-bottom: 2px solid var(--accent); }
  tbody tr { transition: var(--transition-fast); }
  tbody tr:hover { background: rgba(0, 217, 255, 0.05); }
  tbody tr:last-child td { border-bottom: none; }
  .muted { color: var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <div class="row" style="justify-content: space-between;">
    <div>
      <img class="brand-logo" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAlgAAADICAIAAAC7/QjhAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAEzGlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPD94cGFja2V0IGJlZ2luPSfvu78nIGlkPSdXNU0wTXBDZWhpSHpyZVN6TlRjemtjOWQnPz4KPHg6eG1wbWV0YSB4bWxuczp4PSdhZG9iZTpuczptZXRhLyc+CjxyZGY6UkRGIHhtbG5zOnJkZj0naHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyc+CgogPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9JycKICB4bWxuczpBdHRyaWI9J2h0dHA6Ly9ucy5hdHRyaWJ1dGlvbi5jb20vYWRzLzEuMC8nPgogIDxBdHRyaWI6QWRzPgogICA8cmRmOlNlcT4KICAgIDxyZGY6bGkgcmRmOnBhcnNlVHlwZT0nUmVzb3VyY2UnPgogICAgIDxBdHRyaWI6Q3JlYXRlZD4yMDI2LTAxLTA4PC9BdHRyaWI6Q3JlYXRlZD4KICAgICA8QXR0cmliOkV4dElkPmJjZjAxYzk3LWYyNDMtNGNiMC1hMWRjLTM5MDhhOGFiYTExNzwvQXR0cmliOkV4dElkPgogICAgIDxBdHRyaWI6RmJJZD41MjUyNjU5MTQxNzk1ODA8L0F0dHJpYjpGYklkPgogICAgIDxBdHRyaWI6VG91Y2hUeXBlPjI8L0F0dHJpYjpUb3VjaFR5cGU+CiAgICA8L3JkZjpsaT4KICAgPC9yZGY6U2VxPgogIDwvQXR0cmliOkFkcz4KIDwvcmRmOkRlc2NyaXB0aW9uPgoKIDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PScnCiAgeG1sbnM6ZGM9J2h0dHA6Ly9wdXJsLm9yZy9kYy9lbGVtZW50cy8xLjEvJz4KICA8ZGM6dGl0bGU+CiAgIDxyZGY6QWx0PgogICAgPHJkZjpsaSB4bWw6bGFuZz0neC1kZWZhdWx0Jz5VbnRpdGxlZCBkZXNpZ24gLSAxPC9yZGY6bGk+CiAgIDwvcmRmOkFsdD4KICA8L2RjOnRpdGxlPgogPC9yZGY6RGVzY3JpcHRpb24+CgogPHJkZjpEZXNjcmlwdGlv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" alt="VR Hotspot" />
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Controls</h2>
      
      <!-- Action Buttons -->
      <div class="row">
        <button class="primary" id="btnStart">Start</button>
        <button id="btnStartOverrides">Start (use form)</button>
        <button class="danger" id="btnStop">Stop</button>
        <button id="btnRestart">Restart</button>
        <button id="btnRepair">Repair</button>
      </div>
      
      <!-- Refresh Controls -->
      <div class="row" style="margin-top:12px; flex-wrap: wrap; gap: 8px;">
        <button id="btnRefresh">Refresh</button>
        <label class="tog" title="Auto refresh">
          <input type="checkbox" id="autoRefresh" />
          Auto
        </label>
        <select id="refreshEvery" title="Auto refresh interval" style="width:auto; min-width: 70px;">
          <option value="2000">2s</option>
          <option value="3000">3s</option>
          <option value="5000">5s</option>
          <option value="10000">10s</option>
        </select>
      </div>
      
      <!-- Settings -->
      <div class="row" style="margin-top:12px; flex-wrap: wrap; gap: 8px; align-items: center;">
        <label class="tog" title="Hide logs (recommended while streaming)">
          <input type="checkbox" id="privacyMode" checked />
          Privacy
        </label>
        <div style="min-width:260px; flex: 1; max-width: 100%;">
          <label for="apiToken">API token</label>
          <input id="apiToken" placeholder="Enter API token" style="width: 100%;" />
          <div class="small" style="margin-top:6px;">Saved locally in your browser.</div>
        </div>
      </div>
      
      <div id="msg" class="small" style="margin-top:10px;"></div>
      <div id="dirty" class="small" style="margin-top:6px;"></div>
      <div class="small" style="margin-top:10px;">
        Polling will not overwrite unsaved edits. Save config to persist changes.
      </div>
    </div>

    <div class="card">
      <h2>Status</h2>
      <div id="statusPillContainer" style="margin-bottom: 12px;">
        <div id="pill" class="pill" style="display: none;"><span class="dot"></span><span id="pillTxt">Loading…</span></div>
      </div>
      <div class="small" id="statusMeta">—</div>
      <div class="mono" id="rawStatus" style="margin-top:10px;"></div>
    </div>
  </div>

  <div class="card">
    <h2>Config</h2>

    <div class="grid">
      <div>
        <label for="ssid">SSID</label>
        <input id="ssid" />
      </div>

      <div>
        <label for="wpa2_passphrase">Passphrase (8–63 chars)</label>
        <input id="wpa2_passphrase" type="password" placeholder="Type a new passphrase to change it" />
        <div class="row" style="margin-top:8px;">
          <label class="tog"><input type="checkbox" id="showPass" /> show</label>
          <div class="small" id="passHint"></div>
        </div>
      </div>

      <div>
        <label for="band_preference">Band preference</label>
        <select id="band_preference">
          <option value="6ghz">6ghz (Wi-Fi 6E)</option>
          <option value="5ghz">5ghz</option>
          <option value="2.4ghz">2.4ghz</option>
        </select>
        <div class="hint" id="bandHint"></div>
      </div>

      <div>
        <label for="ap_security">Security</label>
        <select id="ap_security">
          <option value="wpa2">WPA2 (PSK)</option>
          <option value="wpa3_sae">WPA3 (SAE)</option>
        </select>
        <div class="hint" id="secHint"></div>
      </div>

      <div id="sixgBox" style="display:none;">
        <label for="channel_6g">6 GHz channel (optional)</label>
        <input id="channel_6g" type="number" step="1" min="1" max="233" placeholder="Leave blank for auto" />
        <div class="small" style="margin-top:6px;">
          If your driver is strict, you may need to set Country above (JP/AU/US etc.) for 6 GHz channels to be available.
        </div>
      </div>

      <div>
        <label for="channel_width">Channel width</label>
        <select id="channel_width">
          <option value="auto">auto (select best)</option>
          <option value="20">20 MHz</option>
          <option value="40">40 MHz</option>
          <option value="80">80 MHz (recommended for VR)</option>
          <option value="160">160 MHz (maximum throughput)</option>
        </select>
        <div class="small" style="margin-top:6px;">Wider channels = higher throughput but more interference sensitivity.</div>
      </div>

      <div class="two">
        <div>
          <label for="beacon_interval">Beacon interval (TU)</label>
          <input id="beacon_interval" type="number" step="1" min="20" max="1000" placeholder="50" />
        </div>
        <div>
          <label for="dtim_period">DTIM period</label>
          <input id="dtim_period" type="number" step="1" min="1" max="255" placeholder="1" />
        </div>
      </div>
      <div class="small" style="margin-top:6px;">
        Lower beacon interval = faster association but more overhead. DTIM=1 ensures immediate frame delivery for VR.
      </div>

      <div>
        <label class="tog"><input type="checkbox" id="short_guard_interval" /> short_guard_interval (improves throughput)</label>
      </div>

      <div>
        <label for="tx_power">TX power (dBm)</label>
        <input id="tx_power" type="number" step="1" min="1" max="30" placeholder="Leave blank for auto/adapter default" />
        <div class="small" style="margin-top:6px;">Auto-adjusts based on RSSI telemetry when left blank.</div>
      </div>

      <div>
        <label class="tog"><input type="checkbox" id="channel_auto_select" /> channel_auto_select (scan for interference)</label>
      </div>

      <div>
        <label>Country (regulatory domain)</label>
        <div class="two">
          <div>
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
          </div>
          <div>
            <input id="country" placeholder="US" maxlength="2" title="ISO alpha-2 or 00" />
          </div>
        </div>
        <div class="small" style="margin-top:6px;">
          Use the country where the device is physically operating. Kernel enforces channel/power rules.
        </div>
      </div>

      <div>
        <label for="ap_adapter">AP adapter</label>
        <select id="ap_adapter"></select>
        <div class="row" style="margin-top:8px;">
          <button id="btnUseRecommended">Use recommended</button>
          <button id="btnReloadAdapters">Reload adapters</button>
        </div>
        <div class="small" id="adapterHint" style="margin-top:6px;"></div>
      </div>

      <div>
        <label for="ap_ready_timeout_s">AP ready timeout (s)</label>
        <input id="ap_ready_timeout_s" type="number" step="0.1" min="1" />
      </div>

      <div>
        <label for="fallback_channel_2g">Fallback 2.4GHz channel (1–13)</label>
        <input id="fallback_channel_2g" type="number" step="1" min="1" max="13" />
      </div>

      <div>
        <label for="lan_gateway_ip">LAN gateway IP</label>
        <input id="lan_gateway_ip" placeholder="192.168.68.1" />
        <div class="small" style="margin-top:6px;">/24 subnet is assumed for now.</div>
      </div>

      <div>
        <label for="dhcp_start_ip">DHCP start IP</label>
        <input id="dhcp_start_ip" placeholder="192.168.68.10" />
      </div>

      <div>
        <label for="dhcp_end_ip">DHCP end IP</label>
        <input id="dhcp_end_ip" placeholder="192.168.68.250" />
      </div>

      <div>
        <label for="dhcp_dns">DHCP DNS</label>
        <input id="dhcp_dns" placeholder="gateway or 1.1.1.1,8.8.8.8" />
        <div class="small" style="margin-top:6px;">Use "gateway" (default) or "no" to omit.</div>
      </div>

      <div>
        <label>Flags</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="optimized_no_virt" /> optimized_no_virt</label>
          <label class="tog"><input type="checkbox" id="enable_internet" /> enable_internet</label>
          <label class="tog"><input type="checkbox" id="debug" /> debug</label>
        </div>
      </div>

      <div>
        <label>System tuning</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="wifi_power_save_disable" /> wifi_power_save_disable</label>
          <label class="tog"><input type="checkbox" id="usb_autosuspend_disable" /> usb_autosuspend_disable</label>
          <label class="tog"><input type="checkbox" id="cpu_governor_performance" /> cpu_governor_performance</label>
          <label class="tog"><input type="checkbox" id="sysctl_tuning" /> sysctl_tuning</label>
        </div>
        <div style="margin-top:8px;">
          <label for="cpu_affinity">cpu_affinity</label>
          <input id="cpu_affinity" placeholder="e.g. 2 or 2-3 or 2,4" />
        </div>
        <div style="margin-top:8px;">
          <label for="irq_affinity">irq_affinity (network IRQs)</label>
          <input id="irq_affinity" placeholder="e.g. 2 or 2-3 or 2,4" />
        </div>
        <div class="row" style="margin-top:8px;">
          <label class="tog"><input type="checkbox" id="interrupt_coalescing" /> interrupt_coalescing</label>
          <label class="tog"><input type="checkbox" id="tcp_low_latency" /> tcp_low_latency</label>
          <label class="tog"><input type="checkbox" id="memory_tuning" /> memory_tuning</label>
          <label class="tog"><input type="checkbox" id="io_scheduler_optimize" /> io_scheduler_optimize</label>
        </div>
      </div>

      <div>
        <label>Telemetry & watchdog</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="telemetry_enable" /> telemetry_enable</label>
          <label class="tog"><input type="checkbox" id="watchdog_enable" /> watchdog_enable</label>
          <label class="tog"><input type="checkbox" id="connection_quality_monitoring" /> connection_quality_monitoring</label>
          <label class="tog"><input type="checkbox" id="auto_channel_switch" /> auto_channel_switch</label>
        </div>
        <div class="two" style="margin-top:8px;">
          <div>
            <label for="telemetry_interval_s">telemetry_interval_s</label>
            <input id="telemetry_interval_s" type="number" step="0.5" min="0.5" />
          </div>
          <div>
            <label for="watchdog_interval_s">watchdog_interval_s</label>
            <input id="watchdog_interval_s" type="number" step="0.5" min="0.5" />
          </div>
        </div>
      </div>

      <div>
        <label for="qos_preset">QoS preset</label>
        <select id="qos_preset">
          <option value="off">off</option>
          <option value="ultra_low_latency">ultra_low_latency (strict priority + UDP priority)</option>
          <option value="vr">vr (DSCP CS5 + cake)</option>
          <option value="high_throughput">high_throughput (DSCP AF42 + cake)</option>
          <option value="balanced">balanced (DSCP AF41 + fq_codel)</option>
        </select>
        <div class="row" style="margin-top:8px;">
          <label class="tog"><input type="checkbox" id="nat_accel" /> nat_accel</label>
        </div>
        <div class="small" style="margin-top:6px;">DSCP marking is skipped when firewalld is managing rules.</div>
      </div>

      <div>
        <label>Bridge mode</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="bridge_mode" /> bridge_mode</label>
        </div>
        <div class="two" style="margin-top:8px;">
          <div>
            <label for="bridge_name">bridge_name</label>
            <input id="bridge_name" placeholder="vrbr0" />
          </div>
          <div>
            <label for="bridge_uplink">bridge_uplink</label>
            <input id="bridge_uplink" placeholder="e.g. eth0" />
          </div>
        </div>
        <div class="small" style="margin-top:6px;">Bridge mode bypasses NAT/DHCP; AP clients join your LAN.</div>
      </div>

      <div>
        <label>Firewall (firewalld)</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="firewalld_enabled" /> enabled</label>
          <label class="tog"><input type="checkbox" id="firewalld_enable_masquerade" /> masquerade</label>
          <label class="tog"><input type="checkbox" id="firewalld_enable_forward" /> forward</label>
          <label class="tog"><input type="checkbox" id="firewalld_cleanup_on_stop" /> cleanup_on_stop</label>
        </div>
      </div>
    </div>

    <div class="row" style="margin-top:12px;">
      <button id="btnApplyVrProfileUltra">Ultra Low Latency</button>
      <button id="btnApplyVrProfileHigh">High Throughput</button>
      <button id="btnApplyVrProfile">Balanced</button>
      <button id="btnApplyVrProfileStable">Stability</button>
      <button class="primary" id="btnSaveConfig">Save config</button>
      <button class="primary" id="btnSaveRestart">Save & Restart</button>
    </div>

    <div class="small" style="margin-top:10px;">
      Security: API never returns passphrases in cleartext. To change passphrase, type a new one then Save.
    </div>
  </div>

  <div class="card">
    <h2>Telemetry</h2>
    <div class="small">RSSI, bitrate, retries, loss (from station stats).</div>
    <div class="small" id="telemetrySummary" style="margin-top:6px;"></div>
    <div class="small muted" id="telemetryWarnings" style="margin-top:6px;"></div>
    <table style="margin-top:10px;">
      <thead>
        <tr>
          <th>Client</th>
          <th>RSSI</th>
          <th>TX Mbps</th>
          <th>RX Mbps</th>
          <th>Quality</th>
          <th>Retries %</th>
          <th>Loss %</th>
        </tr>
      </thead>
      <tbody id="telemetryBody"></tbody>
    </table>
  </div>

  <div class="card">
    <h2>Engine logs</h2>
    <div class="small">Logs are hidden while Privacy is ON.</div>
    <div class="mono" id="stdout" style="margin-top:10px;"></div>
    <div class="mono" id="stderr" style="margin-top:10px;"></div>
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
    `quality_avg=${fmtNum(summary.quality_score_avg, 0)} ` +
    `loss_avg=${fmtPct(summary.loss_pct_avg)}%`;

  const warns = (t.warnings || []).join(' · ');
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
    const id = (c.mac || '—') + (c.ip ? ` (${c.ip})` : '');
    const qualityScore = (c.quality_score !== null && c.quality_score !== undefined) ? fmtNum(c.quality_score, 0) : '—';
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
  el.className = 'small ' + (kind || '');
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
  
  if (phase && phase !== '—' && phase !== 'stopped' && phase !== 'error' && !(phase === 'stopped' && !running)) {
    statusParts.push(phase.charAt(0).toUpperCase() + phase.slice(1));
  }
  
  if (adapter && adapter !== '—') {
    statusParts.push(adapter);
  }
  
  if (band && band !== '—') {
    statusParts.push(band);
  }
  
  if (mode && mode !== '—' && mode !== 'nat') {
    statusParts.push(mode);
  }

  if (statusParts.length === 0) {
    txt.textContent = 'Loading…';
  } else {
    txt.textContent = statusParts.join(' · ');
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

        if path not in ("/healthz", "/favicon.ico", "/assets/favicon.svg", "/assets/logo.png"):
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

        if path == "/assets/favicon.svg":
            asset_path = _resolve_asset_path("favicon.svg")
            if asset_path and os.path.isfile(asset_path):
                try:
                    with open(asset_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/svg+xml")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self._respond_raw(500, b"Internal Server Error", "text/plain")
            else:
                self._respond_raw(404, b"Not Found", "text/plain")
            return

        if path == "/assets/logo.png":
            asset_path = _resolve_asset_path("logo.png")
            if asset_path and os.path.isfile(asset_path):
                try:
                    with open(asset_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self._respond_raw(500, b"Internal Server Error", "text/plain")
            else:
                self._respond_raw(404, b"Not Found", "text/plain")
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

        if path == "/v1/diagnostics/udp_latency":
            target_ip = (body.get("target_ip") or "").strip() if isinstance(body, dict) else ""
            duration_s = body.get("duration_s") if isinstance(body, dict) else None
            interval_ms = body.get("interval_ms") if isinstance(body, dict) else None
            target_port = body.get("target_port") if isinstance(body, dict) else None
            packet_size = body.get("packet_size") if isinstance(body, dict) else None

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
                target_port = int(target_port) if target_port is not None else 12345
            except Exception:
                target_port = 12345
            try:
                packet_size = int(packet_size) if packet_size is not None else 64
            except Exception:
                packet_size = 64

            res = run_udp_latency_test(
                target_ip=target_ip,
                target_port=target_port,
                duration_s=duration_s,
                interval_ms=interval_ms,
                packet_size=packet_size,
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
