import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Tuple

from vr_hotspotd import host_probes
from vr_hotspotd.config import ConfigValidationError, validate_network_config
from vr_hotspotd.engine.channel_geometry import (
    center_channel, hostapd_oper_width, ht40_capability, normalize_width,
    six_ghz_operating_class,
)
from vr_hotspotd.engine.secret_io import (
    add_passphrase_arguments,
    read_passphrase,
    write_protected_text,
)

_CTRL_DIR_RE = re.compile(r"DIR=([^\s]+)")


def _run(cmd: List[str], check: bool = True) -> Tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd_failed rc={p.returncode} cmd={' '.join(cmd)} out={out.strip()}")
    return p.returncode, out


def _resolve_binary(name: str, env_key: str) -> str:
    override = os.environ.get(env_key)
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name}_not_found")
    return p


def _parse_ctrl_interface_dir(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    m = _CTRL_DIR_RE.search(raw)
    if m:
        return m.group(1)
    return raw.split()[0]


def _ctrl_dir_from_conf(conf_path: str) -> Optional[str]:
    try:
        with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("ctrl_interface="):
                    value = s.split("=", 1)[1].strip()
                    return _parse_ctrl_interface_dir(value)
    except Exception:
        return None
    return None


def _ensure_ctrl_interface_dir(conf_path: str) -> None:
    ctrl_dir = _ctrl_dir_from_conf(conf_path)
    if not ctrl_dir:
        return
    try:
        os.makedirs(ctrl_dir, exist_ok=True)
        os.chmod(ctrl_dir, 0o755)
        print(f"hostapd_ctrl_dir_ready: {ctrl_dir}")
    except Exception as exc:
        print(f"hostapd_ctrl_dir_failed: {ctrl_dir} err={exc}")


def _default_uplink_iface() -> Optional[str]:
    return host_probes.probe_default_uplink(raise_on_execution_error=True)


def _maybe_set_regdom(country: Optional[str]) -> None:
    if not country:
        return
    cc = str(country).strip().upper()
    if len(cc) != 2:
        return
    iw = shutil.which("iw") or "/usr/sbin/iw"
    if not os.path.exists(iw):
        return
    subprocess.run([iw, "reg", "set", cc], check=False, capture_output=True, text=True)


def _mk_virt_name(base: str) -> str:
    base = base.strip()
    cand = f"x0{base}"
    return cand[:15]


def _create_virtual_ap_iface(parent_if: str, virt_if: str) -> None:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    _run([iw, "dev", parent_if, "interface", "add", virt_if, "type", "__ap"], check=True)


def _delete_iface(ifname: str) -> None:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    subprocess.run([iw, "dev", ifname, "del"], check=False, capture_output=True, text=True)


def _iface_up(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    _run([ip, "link", "set", ifname, "up"], check=True)


def _bridge_exists(name: str) -> bool:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    p = subprocess.run([ip, "link", "show", "dev", name], capture_output=True, text=True)
    return p.returncode == 0


def _create_bridge(name: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    _run([ip, "link", "add", name, "type", "bridge"], check=True)
    _run([ip, "link", "set", name, "up"], check=True)


def _delete_bridge(name: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    subprocess.run([ip, "link", "set", name, "down"], check=False, capture_output=True, text=True)
    subprocess.run([ip, "link", "del", name, "type", "bridge"], check=False, capture_output=True, text=True)


def _bridge_add_port(bridge: str, ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    _run([ip, "link", "set", ifname, "master", bridge], check=True)


def _bridge_del_port(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    subprocess.run([ip, "link", "set", ifname, "nomaster"], check=False, capture_output=True, text=True)


def _get_ipv4_addrs(ifname: str) -> List[str]:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    p = subprocess.run([ip, "-4", "-o", "addr", "show", "dev", ifname], capture_output=True, text=True)
    addrs: List[str] = []
    for line in (p.stdout or "").splitlines():
        parts = line.split()
        if "inet" in parts:
            idx = parts.index("inet")
            if idx + 1 < len(parts):
                addrs.append(parts[idx + 1])
    return addrs


def _move_ipv4_addrs(src: str, dst: str) -> List[str]:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    moved: List[str] = []
    for cidr in _get_ipv4_addrs(src):
        _run([ip, "addr", "add", cidr, "dev", dst], check=True)
        subprocess.run([ip, "addr", "del", cidr, "dev", src], check=False, capture_output=True, text=True)
        moved.append(cidr)
    return moved


def _restore_ipv4_addrs(dst: str, bridge: str, addrs: List[str]) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    for cidr in addrs:
        subprocess.run([ip, "addr", "del", cidr, "dev", bridge], check=False, capture_output=True, text=True)
        subprocess.run([ip, "addr", "add", cidr, "dev", dst], check=False, capture_output=True, text=True)


def _write_hostapd_conf(
    *,
    path: str,
    ifname: str,
    ssid: str,
    passphrase: str,
    country: Optional[str],
    band: str,
    channel: int,
    ap_security: str,
    wifi6: bool,
    bridge: str,
    channel_width: str = "auto",
    beacon_interval: int = 50,
    dtim_period: int = 1,
    short_guard_interval: bool = True,
    tx_power: Optional[int] = None,
) -> None:
    validation_errors = validate_network_config(
        {"ssid": ssid, "wpa2_passphrase": passphrase, "ap_adapter": ifname}
    )
    if validation_errors:
        raise ConfigValidationError(validation_errors)

    cc = (country or "").strip().upper()
    
    width = normalize_width(band, channel_width)
    seg0 = center_channel(band, int(channel), width)
    if seg0 is None:
        raise ValueError("invalid_channel_block")

    lines = [
        f"interface={ifname}",
        "driver=nl80211",
        f"bridge={bridge}",
        "ctrl_interface=/run/hostapd",
        "ctrl_interface_group=0",
        f"ssid={ssid}",
        f"beacon_int={beacon_interval}",
        f"dtim_period={dtim_period}",
        "wmm_enabled=1",
    ]

    if cc and len(cc) == 2:
        lines += [f"country_code={cc}", "ieee80211d=1"]

    if band in ("2.4ghz", "5ghz"):
        lines += [
            "hw_mode=g" if band == "2.4ghz" else "hw_mode=a",
            f"channel={int(channel)}", "ieee80211n=1",
        ]
        ht_caps = ["SHORT-GI-20", "SHORT-GI-40"] if short_guard_interval else []
        ht40 = ht40_capability(band, int(channel), width)
        if ht40:
            ht_caps.append(ht40)
        if ht_caps:
            lines.append(f"ht_capab=[{']['.join(ht_caps)}]")
        if band == "5ghz":
            lines.append("ieee80211ac=1")
            lines.append(f"vht_oper_chwidth={hostapd_oper_width(width)}")
            if width >= 80:
                lines.append(f"vht_oper_centr_freq_seg0_idx={seg0}")
                vht_caps = ["VHT160"] if width == 160 else []
                if short_guard_interval:
                    vht_caps.append("SHORT-GI-80")
                    if width == 160:
                        vht_caps.append("SHORT-GI-160")
                if vht_caps:
                    lines.append(f"vht_capab=[{']['.join(vht_caps)}]")
    elif band == "6ghz":
        lines += [
            "hw_mode=a", f"channel={int(channel)}",
            f"op_class={six_ghz_operating_class(width)}", "ieee80211ax=1",
            f"he_oper_chwidth={hostapd_oper_width(width)}",
            f"he_oper_centr_freq_seg0_idx={seg0}",
        ]
    else:
        raise RuntimeError("invalid_band")

    if wifi6 and band in ("2.4ghz", "5ghz"):
        lines.append("ieee80211ax=1")
        if band == "5ghz":
            lines.append(f"he_oper_chwidth={hostapd_oper_width(width)}")
            if width >= 80:
                lines.append(f"he_oper_centr_freq_seg0_idx={seg0}")
    if band == "6ghz" or wifi6:
        lines += [
            "he_su_beamformee=1", "he_su_beamformer=1", "he_mu_beamformer=1",
        ]
    # A-MSDU/A-MPDU negotiation is handled by the driver. amsdu_frames and
    # ampdu_density are not hostapd configuration keys.

    if ap_security == "wpa3_sae" or band == "6ghz":
        lines += [
            "wpa=2",
            "wpa_key_mgmt=SAE",
            "rsn_pairwise=CCMP",
            "ieee80211w=2",
            "sae_pwe=1" if band == "6ghz" else "sae_pwe=2",
            f"sae_password={passphrase}",
        ]
    else:
        lines += [
            "wpa=2",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
            f"wpa_passphrase={passphrase}",
        ]
    
    # hostapd has no tx_power directive. The shared runtime startup path applies
    # this setting through iw after the AP interface and channel are ready.

    write_protected_text(path, "\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ap-ifname", required=True)
    ap.add_argument("--ssid", required=True)
    add_passphrase_arguments(ap)
    ap.add_argument("--band", required=True)
    ap.add_argument("--ap-security", default="wpa2")
    ap.add_argument("--country", default=None)
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--no-virt", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--wifi6", action="store_true")
    ap.add_argument("--bridge-name", default="vrbr0")
    ap.add_argument("--bridge-uplink", default=None)
    ap.add_argument("--channel-width", default="auto")
    ap.add_argument("--beacon-interval", type=int, default=50)
    ap.add_argument("--dtim-period", type=int, default=1)
    ap.add_argument("--short-guard-interval", action="store_true", default=True)
    ap.add_argument("--tx-power", type=int, default=None)
    args = ap.parse_args()
    passphrase = read_passphrase(args)

    if len(passphrase) < 8:
        raise RuntimeError("invalid_passphrase_min_length_8")

    band = str(args.band).strip().lower()
    if band in ("2", "2g", "2ghz", "2.4", "2.4ghz"):
        band = "2.4ghz"
    elif band in ("5", "5g", "5ghz"):
        band = "5ghz"
    elif band in ("6", "6g", "6ghz", "6e"):
        band = "6ghz"
    else:
        raise RuntimeError("invalid_band")

    uplink = args.bridge_uplink or _default_uplink_iface()
    if not uplink:
        raise RuntimeError("bridge_uplink_not_found")

    if _bridge_exists(args.bridge_name):
        raise RuntimeError("bridge_already_exists")

    _maybe_set_regdom(args.country)

    if args.channel is not None:
        channel = int(args.channel)
    else:
        if band == "2.4ghz":
            channel = 6
        elif band == "5ghz":
            channel = 36
        else:
            channel = 1

    hostapd = _resolve_binary("hostapd", "HOSTAPD")
    tmpdir = tempfile.mkdtemp(prefix="vr-hotspotd-bridge-")
    hostapd_conf = os.path.join(tmpdir, "hostapd.conf")

    created_virt = False
    ap_iface = args.ap_ifname

    if not args.no_virt:
        virt = _mk_virt_name(args.ap_ifname)
        _create_virtual_ap_iface(args.ap_ifname, virt)
        created_virt = True
        ap_iface = virt

    _iface_up(ap_iface)

    moved_addrs: List[str] = []
    bridge_ready = False

    hostapd_p: Optional[subprocess.Popen] = None

    try:
        _create_bridge(args.bridge_name)
        bridge_ready = True
        _bridge_add_port(args.bridge_name, uplink)
        _iface_up(uplink)
        moved_addrs = _move_ipv4_addrs(uplink, args.bridge_name)

        _write_hostapd_conf(
            path=hostapd_conf,
            ifname=ap_iface,
            ssid=args.ssid,
            passphrase=passphrase,
            country=args.country,
            band=band,
            channel=channel,
            ap_security=str(args.ap_security).strip().lower(),
            wifi6=bool(args.wifi6),
            bridge=args.bridge_name,
            channel_width=args.channel_width,
            beacon_interval=args.beacon_interval,
            dtim_period=args.dtim_period,
            short_guard_interval=args.short_guard_interval,
            tx_power=args.tx_power,
        )
        _ensure_ctrl_interface_dir(hostapd_conf)

        hostapd_cmd = [hostapd, hostapd_conf]
        if args.debug:
            hostapd_cmd = [hostapd, "-dd", hostapd_conf]

        hostapd_p = subprocess.Popen(hostapd_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        stopping = False

        def _stop_child():
            nonlocal stopping
            if stopping:
                return
            stopping = True
            try:
                hostapd_p.terminate()
            except Exception:
                pass

        def _on_sigterm(_signum, _frame):
            _stop_child()

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)

        try:
            while True:
                if hostapd_p.poll() is not None:
                    break
                if hostapd_p.stdout:
                    line = hostapd_p.stdout.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                time.sleep(0.05)
        finally:
            _stop_child()
            try:
                hostapd_p.wait(timeout=2.0)
            except Exception:
                try:
                    hostapd_p.kill()
                except Exception:
                    pass
    finally:
        if bridge_ready:
            _bridge_del_port(uplink)
            if moved_addrs:
                _restore_ipv4_addrs(uplink, args.bridge_name, moved_addrs)
            _delete_bridge(args.bridge_name)

        if created_virt:
            _delete_iface(ap_iface)

        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    if hostapd_p and hostapd_p.returncode is not None:
        return hostapd_p.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
