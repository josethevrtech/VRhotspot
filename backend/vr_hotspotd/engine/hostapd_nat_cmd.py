import sys
from typing import List, Optional


def build_cmd_nat(
    *,
    ap_ifname: str,
    ssid: str,
    passphrase: str,
    band: str,
    ap_security: str,
    country: Optional[str],
    channel: Optional[int],
    no_virt: bool,
    debug: bool,
    wifi6: bool,
    gateway_ip: Optional[str] = None,
    dhcp_start_ip: Optional[str] = None,
    dhcp_end_ip: Optional[str] = None,
    dhcp_dns: Optional[str] = None,
    enable_internet: bool = True,
    channel_width: str = "auto",
    beacon_interval: int = 50,
    dtim_period: int = 1,
    short_guard_interval: bool = True,
    tx_power: Optional[int] = None,
    strict_width: bool = False,
) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        "-m",
        "vr_hotspotd.engine.hostapd_nat_engine",
        "--ap-ifname",
        ap_ifname,
        "--ssid",
        ssid,
        "--passphrase",
        passphrase,
        "--band",
        band,
        "--ap-security",
        ap_security,
    ]

    if country:
        cmd += ["--country", str(country)]

    if channel is not None:
        cmd += ["--channel", str(int(channel))]

    if no_virt:
        cmd += ["--no-virt"]

    if debug:
        cmd += ["--debug"]

    if wifi6:
        cmd += ["--wifi6"]

    if gateway_ip:
        cmd += ["--gateway-ip", gateway_ip]

    if dhcp_start_ip:
        cmd += ["--dhcp-start", dhcp_start_ip]

    if dhcp_end_ip:
        cmd += ["--dhcp-end", dhcp_end_ip]

    if dhcp_dns:
        cmd += ["--dhcp-dns", dhcp_dns]

    if enable_internet is False:
        cmd += ["--no-internet"]

    cmd += ["--channel-width", str(channel_width)]
    cmd += ["--beacon-interval", str(beacon_interval)]
    cmd += ["--dtim-period", str(dtim_period)]
    if short_guard_interval:
        cmd += ["--short-guard-interval"]
    if tx_power is not None:
        cmd += ["--tx-power", str(tx_power)]

    if strict_width:
        cmd += ["--strict-width"]

    return cmd
