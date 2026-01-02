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

    return cmd
