import json
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("/var/lib/vr-hotspot/config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,

    # Wi-Fi identity
    "ssid": "VR-Hotspot",
    "wpa2_passphrase": "change-me-please",

    # Preferred (optimized) behavior
    "band_preference": "5ghz",   # "5ghz" or "2.4ghz"
    "country": "US",

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


def load_config() -> Dict[str, Any]:
    """
    Returns DEFAULT_CONFIG merged with on-disk config.
    """
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(read_config_file())
    return cfg


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

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2))
    # Keep it root-only by default (matches your current file perms)
    CONFIG_PATH.chmod(0o600)
    return merged


def ensure_config_file():
    if CONFIG_PATH.exists():
        return
    write_config_file({})
