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
<link rel="stylesheet" href="/assets/ui.css" />
</head>
<body>
<div class="wrap">
  <div class="row row-space-between">
    <div>
      <img class="brand-logo" src="/assets/logo.png" alt="VR Hotspot" />
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
      <div class="row mt-12 row-wrap gap-8">
        <button id="btnRefresh">Refresh</button>
        <label class="tog" title="Auto refresh">
          <input type="checkbox" id="autoRefresh" />
          Auto
        </label>
        <select id="refreshEvery" title="Auto refresh interval">
          <option value="2000">2s</option>
          <option value="3000">3s</option>
          <option value="5000">5s</option>
          <option value="10000">10s</option>
        </select>
      </div>
      
      <!-- Settings -->
      <div class="row mt-12 row-wrap gap-8 row-align-center">
        <label class="tog" title="Hide logs (recommended while streaming)">
          <input type="checkbox" id="privacyMode" checked />
          Privacy
        </label>
        <div class="token-field">
          <label for="apiToken">API token</label>
          <input id="apiToken" placeholder="Enter API token" />
          <div class="small mt-6">Saved locally in your browser.</div>
        </div>
      </div>
      
      <div id="msg" class="small mt-10"></div>
      <div id="dirty" class="small mt-6"></div>
      <div class="small mt-10">
        Polling will not overwrite unsaved edits. Save config to persist changes.
      </div>
    </div>

    <div class="card">
      <h2>Status</h2>
      <div id="statusPillContainer" class="mb-12">
        <div id="pill" class="pill"><span class="dot"></span><span id="pillTxt">Loading…</span></div>
      </div>
      <div class="small" id="statusMeta">—</div>
      <div class="mono mt-10" id="rawStatus"></div>
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
        <div class="row mt-8">
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

      <div id="sixgBox">
        <label for="channel_6g">6 GHz channel (optional)</label>
        <input id="channel_6g" type="number" step="1" min="1" max="233" placeholder="Leave blank for auto" />
        <div class="small mt-6">
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
        <div class="small mt-6">Wider channels = higher throughput but more interference sensitivity.</div>
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
      <div class="small mt-6">
        Lower beacon interval = faster association but more overhead. DTIM=1 ensures immediate frame delivery for VR.
      </div>

      <div>
        <label class="tog"><input type="checkbox" id="short_guard_interval" /> short_guard_interval (improves throughput)</label>
      </div>

      <div>
        <label for="tx_power">TX power (dBm)</label>
        <input id="tx_power" type="number" step="1" min="1" max="30" placeholder="Leave blank for auto/adapter default" />
        <div class="small mt-6">Auto-adjusts based on RSSI telemetry when left blank.</div>
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
        <div class="small mt-6">
          Use the country where the device is physically operating. Kernel enforces channel/power rules.
        </div>
      </div>

      <div>
        <label for="ap_adapter">AP adapter</label>
        <select id="ap_adapter"></select>
        <div class="row mt-8">
          <button id="btnUseRecommended">Use recommended</button>
          <button id="btnReloadAdapters">Reload adapters</button>
        </div>
        <div class="small mt-6" id="adapterHint"></div>
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
        <div class="small mt-6">/24 subnet is assumed for now.</div>
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
        <div class="small mt-6">Use "gateway" (default) or "no" to omit.</div>
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
        <div class="mt-8">
          <label for="cpu_affinity">cpu_affinity</label>
          <input id="cpu_affinity" placeholder="e.g. 2 or 2-3 or 2,4" />
        </div>
        <div class="mt-8">
          <label for="irq_affinity">irq_affinity (network IRQs)</label>
          <input id="irq_affinity" placeholder="e.g. 2 or 2-3 or 2,4" />
        </div>
        <div class="row mt-8">
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
        <div class="two mt-8">
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
        <div class="row mt-8">
          <label class="tog"><input type="checkbox" id="nat_accel" /> nat_accel</label>
        </div>
        <div class="small mt-6">DSCP marking is skipped when firewalld is managing rules.</div>
      </div>

      <div>
        <label>Bridge mode</label>
        <div class="row">
          <label class="tog"><input type="checkbox" id="bridge_mode" /> bridge_mode</label>
        </div>
        <div class="two mt-8">
          <div>
            <label for="bridge_name">bridge_name</label>
            <input id="bridge_name" placeholder="vrbr0" />
          </div>
          <div>
            <label for="bridge_uplink">bridge_uplink</label>
            <input id="bridge_uplink" placeholder="e.g. eth0" />
          </div>
        </div>
        <div class="small mt-6">Bridge mode bypasses NAT/DHCP; AP clients join your LAN.</div>
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

    <div class="row mt-12">
      <button id="btnApplyVrProfileUltra">Ultra Low Latency</button>
      <button id="btnApplyVrProfileHigh">High Throughput</button>
      <button id="btnApplyVrProfile">Balanced</button>
      <button id="btnApplyVrProfileStable">Stability</button>
      <button class="primary" id="btnSaveConfig">Save config</button>
      <button class="primary" id="btnSaveRestart">Save & Restart</button>
    </div>

    <div class="small mt-10">
      Security: API never returns passphrases in cleartext. To change passphrase, type a new one then Save.
    </div>
  </div>

  <div class="card">
    <h2>Telemetry</h2>
    <div class="small">RSSI, bitrate, retries, loss (from station stats).</div>
    <div class="small mt-6" id="telemetrySummary"></div>
    <div class="small muted mt-6" id="telemetryWarnings"></div>
    <table class="mt-10">
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
    <div class="mono mt-10" id="stdout"></div>
    <div class="mono mt-10" id="stderr"></div>
  </div>
</div>

<script defer src="/assets/ui.js"></script>
</body>
</html>
"""

_ASSET_CONTENT_TYPES = {
    "favicon.svg": "image/svg+xml",
    "logo.png": "image/png",
    "ui.css": "text/css; charset=utf-8",
    "ui.js": "application/javascript; charset=utf-8",
}


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
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
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

    def _serve_asset(self, name: str) -> None:
        content_type = _ASSET_CONTENT_TYPES.get(name)
        if not content_type:
            self._respond_raw(404, b"Not Found", "text/plain")
            return

        asset_path = _resolve_asset_path(name)
        if asset_path and os.path.isfile(asset_path):
            try:
                with open(asset_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self._respond_raw(500, b"Internal Server Error", "text/plain")
        else:
            self._respond_raw(404, b"Not Found", "text/plain")

    def do_GET(self):
        cid = self._cid()
        path, qs = self._parse_url()

        if path not in ("/healthz", "/favicon.ico") and not path.startswith("/assets/"):
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

        if path.startswith("/assets/"):
            self._serve_asset(path[len("/assets/"):])
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
