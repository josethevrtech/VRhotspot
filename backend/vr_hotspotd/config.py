import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("/var/lib/vr-hotspot/config.json")
CONFIG_TMP = Path("/var/lib/vr-hotspot/config.json.tmp")
CONFIG_SCHEMA_VERSION = 2

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": CONFIG_SCHEMA_VERSION,

    # Wi-Fi identity
    "ssid": "VR-Hotspot",
    "wpa2_passphrase": "change-me-please",

    # Preferred (optimized) behavior
    "band_preference": "5ghz",   # "5ghz" or "2.4ghz" or "6ghz"
    "country": "US",
    "wifi6": "auto",             # "auto" | true | false
    "ap_security": "wpa2",        # "wpa2" | "wpa3_sae"
    "channel_6g": None,          # optional int

    # Steam Deck / SteamOS stability:
    # False => allow lnxrouter to create a virtual AP interface (often best default).
    # True  => force --no-virt (can help some chipsets, but breaks others).
    "optimized_no_virt": False,

    # Optional hard override for which adapter to run AP on
    "ap_adapter": "",

    # Reliability / readiness controls
    "ap_ready_timeout_s": 6.0,

    # Safe fallback parameters (used only if optimized start stalls)
    "fallback_channel_2g": 6,

    # LAN / DHCP / DNS
    "lan_gateway_ip": "192.168.68.1",
    "dhcp_start_ip": "192.168.68.10",
    "dhcp_end_ip": "192.168.68.250",
    "dhcp_dns": "gateway",  # "gateway" | "no" | "8.8.8.8,1.1.1.1"
    "enable_internet": True,

    # System tuning (optional)
    "wifi_power_save_disable": False,
    "usb_autosuspend_disable": False,
    "cpu_governor_performance": False,
    "cpu_affinity": "",
    "sysctl_tuning": False,

    # Firewalld integration (SteamOS: firewalld owns nftables, so use firewall-cmd)
    "firewalld_enabled": True,
    "firewalld_zone": "trusted",
    "firewalld_enable_masquerade": True,
    "firewalld_enable_forward": True,
    "firewalld_cleanup_on_stop": True,

    # Debugging / diagnostics
    "debug": False,
}


def read_config_file() -> Dict[str, Any]:
    """
    Returns the raw JSON content on disk (or {} if missing/invalid).
    """
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_atomic(path: Path, tmp: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _apply_migrations(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg)
    if out.get("version") != CONFIG_SCHEMA_VERSION:
        out["version"] = CONFIG_SCHEMA_VERSION
    if "ap_security" not in out:
        out["ap_security"] = DEFAULT_CONFIG["ap_security"]
    if "channel_6g" not in out:
        out["channel_6g"] = DEFAULT_CONFIG["channel_6g"]
    if "lan_gateway_ip" not in out:
        out["lan_gateway_ip"] = DEFAULT_CONFIG["lan_gateway_ip"]
    if "dhcp_start_ip" not in out:
        out["dhcp_start_ip"] = DEFAULT_CONFIG["dhcp_start_ip"]
    if "dhcp_end_ip" not in out:
        out["dhcp_end_ip"] = DEFAULT_CONFIG["dhcp_end_ip"]
    if "dhcp_dns" not in out:
        out["dhcp_dns"] = DEFAULT_CONFIG["dhcp_dns"]
    if "enable_internet" not in out:
        out["enable_internet"] = DEFAULT_CONFIG["enable_internet"]
    if "wifi_power_save_disable" not in out:
        out["wifi_power_save_disable"] = DEFAULT_CONFIG["wifi_power_save_disable"]
    if "usb_autosuspend_disable" not in out:
        out["usb_autosuspend_disable"] = DEFAULT_CONFIG["usb_autosuspend_disable"]
    if "cpu_governor_performance" not in out:
        out["cpu_governor_performance"] = DEFAULT_CONFIG["cpu_governor_performance"]
    if "cpu_affinity" not in out:
        out["cpu_affinity"] = DEFAULT_CONFIG["cpu_affinity"]
    if "sysctl_tuning" not in out:
        out["sysctl_tuning"] = DEFAULT_CONFIG["sysctl_tuning"]
    return out


def load_config() -> Dict[str, Any]:
    """
    Returns DEFAULT_CONFIG merged with on-disk config.
    """
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(read_config_file())
    migrated = _apply_migrations(cfg)
    if migrated != cfg and CONFIG_PATH.exists():
        _write_atomic(CONFIG_PATH, CONFIG_TMP, json.dumps(migrated, indent=2))
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
    return migrated


def write_config_file(partial_updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persist a partial update to disk. Unknown keys are accepted here but will be
    filtered by API; callers should pass only approved keys.

    Returns the merged config after write.
    """
    if not isinstance(partial_updates, dict):
        partial_updates = {}

    existing = read_config_file()
    merged: Dict[str, Any] = DEFAULT_CONFIG.copy()
    merged.update(existing)
    merged.update(partial_updates)
    merged["version"] = CONFIG_SCHEMA_VERSION

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(CONFIG_PATH, CONFIG_TMP, json.dumps(merged, indent=2))
    # Keep it root-only by default (matches your current file perms)
    CONFIG_PATH.chmod(0o600)
    return merged


def ensure_config_file():
    if CONFIG_PATH.exists():
        return
    write_config_file({})
