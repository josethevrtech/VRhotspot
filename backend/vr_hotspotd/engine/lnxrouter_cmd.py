import os
from typing import List, Optional

from vr_hotspotd.vendor_paths import resolve_vendor_exe, vendor_bin_dirs


def _lnxrouter_path() -> str:
    path, _, _ = resolve_vendor_exe("lnxrouter")
    if path:
        return path
    bins = vendor_bin_dirs()
    fallback = str(bins[-1]) if bins else "/var/lib/vr-hotspot/app/backend/vendor/bin"
    return os.path.join(fallback, "lnxrouter")


def build_cmd(
    *,
    ap_ifname: str,
    ssid: str,
    passphrase: str,
    band_preference: str = "5ghz",
    country: Optional[str] = None,
    channel: Optional[int] = None,
    no_virt: bool = False,
    wifi6: bool = True,
    channel_width: Optional[str] = None,
    center_channel: Optional[int] = None,
    gateway_ip: Optional[str] = None,
    dhcp_dns: Optional[str] = None,
    enable_internet: bool = True,
) -> List[str]:
    """
    Build a deterministic lnxrouter command for linux-router 0.8.1.

    Notes:
      - linux-router's --freq-band supports only: 2.4 or 5 (no 6 GHz).
      - linux-router generates WPA-PSK hostapd config (not SAE), so it is not suitable for WPA3-only 6 GHz.
      - We rely on supervisor.py to inject PATH so hostapd/dnsmasq are found.
    """
    if not ap_ifname:
        raise ValueError("ap_ifname is required")
    if not ssid:
        raise ValueError("ssid is required")
    if not passphrase or len(passphrase) < 8:
        raise ValueError("passphrase must be at least 8 characters")

    # Normalize band
    bp = str(band_preference or "").lower().strip()
    if bp in ("2ghz", "2.4", "2.4ghz"):
        bp = "2.4ghz"
    if bp in ("5", "5g", "5ghz"):
        bp = "5ghz"
    if bp in ("6", "6g", "6ghz", "6ghz_only", "6e"):
        raise ValueError("band_preference_6ghz_requires_hostapd6_engine")

    cmd: List[str] = [
        _lnxrouter_path(),
        "--ap",
        ap_ifname,
        ssid,
        "-p",
        passphrase,
    ]

    # Band
    if bp == "5ghz":
        cmd += ["--freq-band", "5"]
    elif bp == "2.4ghz":
        cmd += ["--freq-band", "2.4"]

    # Enable Wi-Fi 6 features only when effective (hostapd option via linux-router)
    if wifi6:
        cmd += ["--wifi6"]

    # Channel width (VHT/HE for 80MHz on 5GHz)
    width = str(channel_width).lower().strip() if channel_width else ""
    if bp == "5ghz" and width in ("40", "80", "160"):
        # Enable HT/VHT for wider channels when explicitly requested
        cmd += ["--wifi4"]
        if width in ("80", "160") or wifi6:
            cmd += ["--wifi5"]
        if width == "80":
            cmd += ["--vht-ch-width", "1"]
            if center_channel is not None:
                cmd += ["--vht-seg0-ch", str(int(center_channel))]
            if wifi6:
                cmd += ["--he-ch-width", "1"]
                if center_channel is not None:
                    cmd += ["--he-seg0-ch", str(int(center_channel))]

    # Fixed channel
    if channel is not None:
        cmd += ["-c", str(int(channel))]

    # Disable virtual interface
    if no_virt:
        cmd += ["--no-virt"]

    # Regulatory domain
    if country:
        cmd += ["--country", country]

    # Gateway IP (forces a stable /24 subnet)
    if gateway_ip:
        cmd += ["-g", gateway_ip]

    # DHCP DNS offer
    if dhcp_dns:
        cmd += ["--dhcp-dns", dhcp_dns]

    # Disable Internet/NAT
    if enable_internet is False:
        cmd += ["-n"]

    return cmd
