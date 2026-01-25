import copy
import hashlib
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
from vr_hotspotd.lifecycle import (
    repair,
    start_hotspot,
    stop_hotspot,
    reconcile_state_with_engine,
    collect_capture_logs,
)
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
    "channel_5g",    # int (optional)
    "channel_width",  # "auto" | "20" | "40" | "80" | "160"
    "beacon_interval",  # int (TU, default 50)
    "dtim_period",  # int (1-255, default 1)
    "short_guard_interval",  # bool
    "tx_power",  # int (dBm) or None for auto
    "channel_auto_select",  # bool
    "allow_fallback_40mhz",  # bool (Pro Mode)
    "allow_dfs_channels",  # bool
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
    "channel_5g",
    "channel_width",
    "beacon_interval",
    "dtim_period",
    "short_guard_interval",
    "tx_power",
    "channel_auto_select",
    "allow_fallback_40mhz",
    "allow_dfs_channels",
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
_REDACTED_PASSPHRASE_VALUES = {
    "********",
    "<redacted>",
    "<hidden>",
}

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
    "allow_fallback_40mhz",
    "allow_dfs_channels",
}
_INT_KEYS = {"fallback_channel_2g", "channel_6g", "channel_5g", "beacon_interval", "dtim_period", "tx_power"}
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
    api_file = os.path.abspath(__file__)
    # backend/vr_hotspotd/api.py -> backend/vr_hotspotd -> backend -> repo root
    backend_dir = os.path.dirname(os.path.dirname(api_file))
    repo_root = os.path.dirname(backend_dir)
    dev_path = os.path.join(repo_root, "assets", asset_name)
    if os.path.isfile(dev_path):
        return dev_path

    # Install path: /var/lib/vr-hotspot/app/assets/...
    install_path = os.path.join("/var/lib/vr-hotspot/app/assets", asset_name)
    if os.path.isfile(install_path):
        return install_path
    
    return None


def _inline_ui_css() -> str:
    asset_path = _resolve_asset_path("ui.css")
    if not asset_path:
        return ""
    try:
        with open(asset_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""



def _build_ui_html() -> str:
    # Read the template from assets/index.html
    html_path = _resolve_asset_path("index.html")
    if not html_path:
        return "Error: index.html not found."
    
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except OSError:
        return "Error: Could not read index.html."

    css = _inline_ui_css()
    if not css:
        return html_content.replace("<!-- INLINE_CSS -->", "")
    style_tag = f"<style id=\"ui-inline-css\">\n{css}\n</style>"
    return html_content.replace("<!-- INLINE_CSS -->", style_tag)

_ASSET_CONTENT_TYPES = {
    "favicon.svg": "image/svg+xml",
    "logo.png": "image/png",
    "ui.css": "text/css; charset=utf-8",
    "field_visibility.js": "application/javascript; charset=utf-8",
    "ui.js": "application/javascript; charset=utf-8",
    "qrcode.js": "application/javascript; charset=utf-8",
    "chart.js": "application/javascript; charset=utf-8",
    "index.html": "text/html; charset=utf-8",
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
            "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
            "style-src-attr 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
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
        cap = out.get("capture_dir")
        cap_s = str(cap) if cap else None
        out["capture_path"] = cap_s
        out["capture_id"] = os.path.basename(cap_s) if cap_s else None
        if "capture_dir" in out:
            out["capture_dir"] = cap_s
        telemetry_enabled = bool(cfg.get("telemetry_enable", True))
        if telemetry_enabled:
            interval = cfg.get("telemetry_interval_s", 2.0)
            if out.get("running"):
                out["telemetry"] = telemetry.get_snapshot(
                    adapter_ifname=out.get("adapter"),
                    ap_interface_hint=out.get("ap_interface"),
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

        if include_logs:
            capture_logs = collect_capture_logs(
                capture_dir=out.get("capture_dir"),
                lnxrouter_config_dir=out.get("lnxrouter_config_dir"),
            )
            out["capture_logs_tail"] = self._redact_lines(capture_logs, secrets)
        else:
            out["capture_logs_tail"] = []
        return out

    def _config_view(self, *, include_secrets: bool) -> Dict[str, Any]:
        cfg = load_config()
        out = copy.deepcopy(cfg)
        pw = out.pop("wpa2_passphrase", None)
        passphrase_set = isinstance(pw, str) and len(pw) > 0
        out["wpa2_passphrase_set"] = passphrase_set
        if passphrase_set:
            out["wpa2_passphrase_len"] = len(pw)
            if bool(cfg.get("debug")):
                fp = hashlib.sha256(pw.encode("utf-8")).hexdigest()[:8]
                out["wpa2_passphrase_fingerprint"] = fp

        if not include_secrets:
            for k in _SENSITIVE_CONFIG_KEYS:
                if k == "wpa2_passphrase":
                    continue
                if k in out:
                    out[k] = ""

        # Back-compat for older UI clients.
        out["_wpa2_passphrase_redacted"] = passphrase_set
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

        # If passphrase is empty/null or a redacted placeholder, ignore it (treat as "no change").
        if "wpa2_passphrase" in filtered:
            pw = filtered.get("wpa2_passphrase")
            if pw is None:
                filtered.pop("wpa2_passphrase", None)
                warnings.append("ignored_empty_passphrase")
            elif isinstance(pw, str):
                pw_trim = pw.strip()
                if not pw_trim:
                    filtered.pop("wpa2_passphrase", None)
                    warnings.append("ignored_empty_passphrase")
                elif pw_trim.lower() in _REDACTED_PASSPHRASE_VALUES:
                    filtered.pop("wpa2_passphrase", None)
                    warnings.append("ignored_redacted_passphrase")

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
            if not isinstance(pw, str):
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_passphrase_min_length_8",
                        warnings=warnings,
                    ),
                )
                return
            if len(pw) < 8:
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_passphrase_min_length_8",
                        warnings=warnings,
                    ),
                )
                return
            if len(pw) > 63:
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_passphrase_max_length_63",
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
                self.send_header("Cache-Control", "no-store")
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
            html = _build_ui_html().encode("utf-8")
            self._respond_raw(200, html, "text/html; charset=utf-8")
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
            snapshot = get_clients_snapshot(
                ap_ifname if ap_ifname else None,
                ap_interface_hint=st.get("ap_interface"),
            )
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

    def do_HEAD(self):
        path, _qs = self._parse_url()

        if path in ("/", "/ui"):
            raw = _build_ui_html().encode("utf-8")
            self.send_response(200)
            self._send_common_headers("text/html; charset=utf-8", len(raw))
            self.end_headers()
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self._send_common_headers("text/plain; charset=utf-8", 0)
            self.end_headers()
            return

        if path.startswith("/assets/"):
            name = path[len("/assets/"):]
            content_type = _ASSET_CONTENT_TYPES.get(name)
            if not content_type:
                self._respond_raw(404, b"Not Found", "text/plain")
                return
            asset_path = _resolve_asset_path(name)
            if asset_path and os.path.isfile(asset_path):
                length = os.path.getsize(asset_path)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self._respond_raw(404, b"Not Found", "text/plain")
            return

        if path == "/healthz":
            self.send_response(200)
            self._send_common_headers("text/plain; charset=utf-8", 3)
            self.end_headers()
            return

        self.send_response(404)
        self._send_common_headers("application/json; charset=utf-8", 0)
        self.end_headers()

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

                # Ignore empty/redacted passphrase (treat as "no change")
                if "wpa2_passphrase" in overrides_raw:
                    pw = overrides_raw.get("wpa2_passphrase")
                    if pw is None:
                        overrides_raw = dict(overrides_raw)
                        overrides_raw.pop("wpa2_passphrase", None)
                    elif isinstance(pw, str):
                        pw_trim = pw.strip()
                        if not pw_trim or pw_trim.lower() in _REDACTED_PASSPHRASE_VALUES:
                            overrides_raw = dict(overrides_raw)
                            overrides_raw.pop("wpa2_passphrase", None)

            overrides, warnings = self._filter_keys(overrides_raw or {}, _START_OVERRIDE_KEYS)
            warnings = body_warnings + warnings
            overrides, w_coerce = self._coerce_config_types(overrides)
            warnings += w_coerce

            # Extract basic_mode flag from request body
            basic_mode = False
            if isinstance(body, dict):
                bm = body.get("basic_mode")
                if bm is True or (isinstance(bm, str) and bm.lower() in ("true", "1", "yes")):
                    basic_mode = True

            res = start_hotspot(correlation_id=cid, overrides=overrides if overrides else None, basic_mode=basic_mode)
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
                    if pw is None:
                        overrides_raw = dict(overrides_raw)
                        overrides_raw.pop("wpa2_passphrase", None)
                    elif isinstance(pw, str):
                        pw_trim = pw.strip()
                        if not pw_trim or pw_trim.lower() in _REDACTED_PASSPHRASE_VALUES:
                            overrides_raw = dict(overrides_raw)
                            overrides_raw.pop("wpa2_passphrase", None)

            overrides, w2 = self._filter_keys(overrides_raw or {}, _START_OVERRIDE_KEYS)
            warnings += w2
            overrides, w_coerce = self._coerce_config_types(overrides)
            warnings += w_coerce

            # Extract basic_mode flag from request body
            basic_mode = False
            if isinstance(body, dict):
                bm = body.get("basic_mode")
                if bm is True or (isinstance(bm, str) and bm.lower() in ("true", "1", "yes")):
                    basic_mode = True

            res = start_hotspot(correlation_id=cid + ":start", overrides=overrides if overrides else None, basic_mode=basic_mode)
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

        if path == "/v1/config/reveal_passphrase":
            if not isinstance(body, dict) or body.get("confirm") is not True:
                self._respond(
                    400,
                    self._envelope(
                        correlation_id=cid,
                        result_code="invalid_request",
                        warnings=body_warnings + ["confirm_required"],
                    ),
                )
                return
            cfg = load_config()
            pw = cfg.get("wpa2_passphrase")
            if not isinstance(pw, str) or not pw:
                self._respond(
                    404,
                    self._envelope(
                        correlation_id=cid,
                        result_code="passphrase_not_set",
                        warnings=body_warnings,
                    ),
                )
                return
            self._respond(
                200,
                self._envelope(
                    correlation_id=cid,
                    result_code="ok",
                    data={"wpa2_passphrase": pw},
                    warnings=body_warnings,
                ),
            )
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
