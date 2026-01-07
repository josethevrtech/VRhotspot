import sys
from typing import List, Optional


def build_cmd_6ghz(
    *,
    ap_ifname: str,
    ssid: str,
    passphrase: str,
    country: Optional[str],
    channel: Optional[int],
    no_virt: bool,
    debug: bool,
    gateway_ip: Optional[str] = None,
    dhcp_start_ip: Optional[str] = None,
    dhcp_end_ip: Optional[str] = None,
    dhcp_dns: Optional[str] = None,
    enable_internet: bool = True,
) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        "-m",
        "vr_hotspotd.engine.hostapd6_engine",
        "--ap-ifname",
        ap_ifname,
        "--ssid",
        ssid,
        "--passphrase",
        passphrase,
    ]

    if country:
        cmd += ["--country", str(country)]

    if channel is not None:
        cmd += ["--channel", str(int(channel))]

    if no_virt:
        cmd += ["--no-virt"]

    if debug:
        cmd += ["--debug"]

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

    return cmd
