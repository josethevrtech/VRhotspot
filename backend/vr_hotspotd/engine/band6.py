import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd.adapters.inventory import get_adapters


@dataclass
class Band6Plan:
    ap_adapter: str
    channel: int
    hostapd_conf: str
    hostapd_cmd: List[str]
    warnings: List[str]


class Band6Error(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _pick_channel(channels: List[int]) -> int:
    """
    Prefer a commonly-valid 6 GHz 20 MHz channel if present; otherwise pick the lowest.
    Channel 5 (5975 MHz) is a reasonable default when available.
    """
    if not channels:
        raise Band6Error("no_6ghz_channels", "No usable 6 GHz channels found for this adapter/phy.")
    if 5 in channels:
        return 5
    return sorted(channels)[0]


def _render_hostapd_6ghz(cfg: Dict[str, Any], *, ap_adapter: str, channel: int) -> str:
    """
    Minimal 6 GHz hostapd configuration using WPA3-SAE only.
    This assumes nl80211 + a hostapd build that supports 6 GHz.
    """
    ssid = (cfg.get("ssid") or "VR-Hotspot").strip()
    country = (cfg.get("country") or "").strip().upper()

    # SAE passphrase (reuses existing wpa2_passphrase field to avoid schema churn)
    pw = cfg.get("wpa2_passphrase")
    if not isinstance(pw, str) or len(pw.strip()) < 8:
        raise Band6Error(
            "wpa3_sae_passphrase_required",
            "6ghz requires WPA3-SAE. Set a passphrase (8–63 chars) before starting.",
        )
    pw = pw.strip()
    if len(pw) > 63:
        raise Band6Error(
            "invalid_passphrase_length",
            "Passphrase must be 8–63 characters for WPA3-SAE.",
        )

    if not country or country in ("00", "ZZ"):
        raise Band6Error(
            "country_required_for_6ghz",
            "6ghz requires an explicit country code (e.g., JP or AU). Set country before starting.",
        )

    # Note:
    # - op_class=131 is 6 GHz 20 MHz in many hostapd builds.
    # - ieee80211w=2 forces MFP required (good practice + aligned with SAE-only)
    # - sae_password sets the SAE password.
    # - ieee80211ax=1 enables 11ax HE, which is typical for 6 GHz.
    lines = [
        f"interface={ap_adapter}",
        "driver=nl80211",
        f"ssid={ssid}",
        f"country_code={country}",
        "ieee80211d=1",
        "ieee80211h=1",
        "hw_mode=a",
        f"channel={int(channel)}",
        "op_class=131",
        "wmm_enabled=1",
        "ieee80211ax=1",
        "",
        "auth_algs=1",
        "wpa=2",
        "wpa_key_mgmt=SAE",
        "rsn_pairwise=CCMP",
        "ieee80211w=2",
        "sae_require_mfp=1",
        f"sae_password={pw}",
        "",
        "# Optional hardening / compatibility knobs you can enable later:",
        "# sae_anti_clogging_threshold=5",
        "# okc=0",
        "# disable_pmksa_caching=1",
    ]
    return "\n".join(lines).strip() + "\n"


def plan_6ghz_start(
    *,
    cfg: Dict[str, Any],
    requested_adapter: Optional[str] = None,
) -> Band6Plan:
    """
    Select a 6 GHz-capable AP adapter + channel and build hostapd configuration.
    Raises Band6Error with a stable .code for user-facing error messages.
    """
    inv = get_adapters()
    adapters = inv.get("adapters") or []
    warnings: List[str] = []

    # Determine the chosen adapter
    chosen: Optional[Dict[str, Any]] = None

    if requested_adapter:
        for a in adapters:
            if a.get("ifname") == requested_adapter:
                chosen = a
                break
        if not chosen:
            raise Band6Error("adapter_not_found", f"Requested adapter '{requested_adapter}' not found.")
    else:
        rec6 = inv.get("recommended_6ghz")
        if rec6:
            for a in adapters:
                if a.get("ifname") == rec6:
                    chosen = a
                    break

    # Fallback: first usable 6 GHz adapter
    if not chosen:
        for a in adapters:
            if a.get("supports_6ghz"):
                chosen = a
                break

    if not chosen:
        raise Band6Error(
            "no_6ghz_adapter",
            "No 6 GHz-capable AP adapter detected. Ensure the adapter/driver supports 6 GHz and AP mode.",
        )

    if not chosen.get("supports_ap"):
        raise Band6Error(
            "adapter_no_ap_mode",
            f"Adapter '{chosen.get('ifname')}' does not support AP mode.",
        )

    if not chosen.get("supports_6ghz"):
        # Provide more informative error if hardware exists but channels are disabled
        if chosen.get("supports_6ghz_hw") and not chosen.get("supports_6ghz_reg_allowed"):
            raise Band6Error(
                "6ghz_disabled_by_regdom",
                f"Adapter '{chosen.get('ifname')}' advertises 6 GHz, but channels are disabled by regdom/driver. Set country and verify regdb/driver support.",
            )
        raise Band6Error(
            "adapter_not_6ghz",
            f"Adapter '{chosen.get('ifname')}' is not usable for 6 GHz AP based on current iw phy info.",
        )

    ap_adapter = str(chosen.get("ifname"))
    channels = list(chosen.get("channels_6ghz") or [])
    channel = _pick_channel(channels)

    hostapd_conf = _render_hostapd_6ghz(cfg, ap_adapter=ap_adapter, channel=channel)

    # Hostapd binary: allow override via env; otherwise assume it's on PATH (or your bundled bin resolves it).
    hostapd_bin = (os.environ.get("VR_HOTSPOTD_HOSTAPD_BIN") or "hostapd").strip()

    # The engine can write hostapd_conf to a temp file and run this cmd.
    # If you already have a runner that writes config files, just use hostapd_conf and ignore hostapd_cmd.
    conf_path = (os.environ.get("VR_HOTSPOTD_HOSTAPD_CONF_PATH") or "").strip()
    if conf_path:
        cmd = [hostapd_bin, "-c", conf_path, "-dd"]
        warnings.append("hostapd_conf_path_from_env")
    else:
        # Engine should write hostapd_conf to a file and replace "<CONF_PATH>".
        cmd = [hostapd_bin, "-c", "<CONF_PATH>", "-dd"]

    return Band6Plan(
        ap_adapter=ap_adapter,
        channel=channel,
        hostapd_conf=hostapd_conf,
        hostapd_cmd=cmd,
        warnings=warnings,
    )
