import sys
from typing import List, Optional


def build_cmd_bridge(
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
    bridge_name: Optional[str],
    bridge_uplink: Optional[str],
) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        "-m",
        "vr_hotspotd.engine.hostapd_bridge_engine",
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

    if bridge_name:
        cmd += ["--bridge-name", bridge_name]

    if bridge_uplink:
        cmd += ["--bridge-uplink", bridge_uplink]

    return cmd
