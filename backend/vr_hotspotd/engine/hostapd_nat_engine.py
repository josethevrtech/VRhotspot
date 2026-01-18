import argparse
import ipaddress
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional, List, Tuple, Dict

_CTRL_DIR_RE = re.compile(r"DIR=([^\s]+)")


def _run(cmd: List[str], check: bool = True) -> Tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd_failed rc={p.returncode} cmd={' '.join(cmd)} out={out.strip()}")
    return p.returncode, out


def _run_capture(cmd: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout or "", p.stderr or ""


def _which_or_die(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name}_not_found")
    return p


def _resolve_binary(name: str, env_key: str) -> str:
    override = os.environ.get(env_key)
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name}_not_found")
    return p


def _iw_path() -> str:
    return shutil.which("iw") or "/usr/sbin/iw"


def _iface_driver_name(ifname: str) -> Optional[str]:
    paths = (
        f"/sys/class/net/{ifname}/device/driver/module",
        f"/sys/class/net/{ifname}/device/driver",
    )
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            target = os.path.realpath(path)
        except Exception:
            continue
        base = os.path.basename(target)
        if base:
            return base
    try:
        with open(f"/sys/class/net/{ifname}/device/uevent", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DRIVER="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def _iface_uses_driver(ifname: str, driver: str) -> bool:
    name = _iface_driver_name(ifname)
    return bool(name and name == driver)


def _is_firewalld_active() -> bool:
    firewall_cmd = shutil.which("firewall-cmd")
    if not firewall_cmd:
        return False
    p = subprocess.run([firewall_cmd, "--state"], capture_output=True, text=True)
    return p.returncode == 0 and (p.stdout or "").strip() == "running"


def _default_uplink_iface() -> Optional[str]:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    p = subprocess.run([ip, "route", "show", "default"], capture_output=True, text=True)
    for raw in (p.stdout or "").splitlines():
        parts = raw.strip().split()
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return None


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


_VALID_CHANNEL_WIDTHS = {"auto", "20", "40", "80", "160"}


def _normalize_channel_width(band: str, requested: Optional[str], channel: Optional[int]) -> str:
    requested_raw = str(requested or "auto").strip().lower()
    requested_norm = requested_raw if requested_raw in _VALID_CHANNEL_WIDTHS else "auto"
    if band == "2.4ghz":
        normalized = "20"
    else:
        normalized = "80" if requested_norm == "auto" else requested_norm
    if normalized != requested_raw:
        print(
            "WARNING hostapd_channel_width_clamped "
            f"band={band} requested={requested_raw} normalized={normalized}"
        )
    return normalized


def _one_line(value: str) -> str:
    return value.replace("\r", "\\r").replace("\n", "\\n").strip()


def _format_cmd_result(cmd: List[str], rc: int, stdout: str, stderr: str) -> str:
    stdout_s = _one_line(stdout) or "none"
    stderr_s = _one_line(stderr) or "none"
    return f"cmd={' '.join(cmd)} rc={rc} stdout={stdout_s} stderr={stderr_s}"


def _parse_wiphy_from_iw_dev(output: str, ifname: str) -> Optional[str]:
    current_phy: Optional[str] = None
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("phy#"):
            current_phy = s.split("#", 1)[1]
            continue
        if s.startswith("Interface "):
            parts = s.split()
            if len(parts) > 1 and parts[1] == ifname:
                return current_phy
    return None


def _iw_wiphy_for_interface(ifname: str) -> Optional[str]:
    iw = _iw_path()
    rc, out, _ = _run_capture([iw, "dev", ifname, "info"])
    if rc == 0:
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("wiphy "):
                parts = s.split()
                if len(parts) > 1:
                    return parts[1]
    rc, out, _ = _run_capture([iw, "dev"])
    if rc != 0:
        return None
    return _parse_wiphy_from_iw_dev(out, ifname)


def _iw_interface_exists(ifname: str) -> bool:
    iw = _iw_path()
    rc, out, _ = _run_capture([iw, "dev"])
    if rc != 0:
        return False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Interface "):
            parts = s.split()
            if len(parts) > 1 and parts[1] == ifname:
                return True
    return False


def _create_virtual_ap_iface(parent_if: str, virt_if: str) -> Tuple[bool, str]:
    iw = _iw_path()
    attempts: List[str] = []
    wiphy = _iw_wiphy_for_interface(parent_if)
    if wiphy:
        cmd = [iw, "phy", wiphy, "interface", "add", virt_if, "type", "__ap"]
        rc, out, err = _run_capture(cmd)
        attempts.append(_format_cmd_result(cmd, rc, out, err))
        if rc == 0:
            return True, " | ".join(attempts)
    cmd = [iw, "dev", parent_if, "interface", "add", virt_if, "type", "__ap"]
    rc, out, err = _run_capture(cmd)
    attempts.append(_format_cmd_result(cmd, rc, out, err))
    return rc == 0, " | ".join(attempts)


def _delete_iface(ifname: str) -> None:
    iw = _iw_path()
    subprocess.run([iw, "dev", ifname, "del"], check=False, capture_output=True, text=True)


def _iface_up(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    _run([ip, "link", "set", ifname, "up"], check=True)


def _iface_down(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    subprocess.run([ip, "link", "set", ifname, "down"], check=False, capture_output=True, text=True)


def _flush_ip(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    subprocess.run([ip, "addr", "flush", "dev", ifname], check=False, capture_output=True, text=True)


def _assign_ip(ifname: str, cidr: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    subprocess.run([ip, "addr", "flush", "dev", ifname], check=False, capture_output=True, text=True)
    _run([ip, "addr", "add", cidr, "dev", ifname], check=True)


def _sysctl_ip_forward(enable: bool = True) -> None:
    val = "1" if enable else "0"
    subprocess.run(["sysctl", "-w", f"net.ipv4.ip_forward={val}"], check=False, capture_output=True, text=True)


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


def _nmcli_path() -> Optional[str]:
    return shutil.which("nmcli")


def _is_nm_running() -> bool:
    nmcli = _nmcli_path()
    if not nmcli:
        return False
    p = subprocess.run([nmcli, "-t", "-f", "RUNNING", "g"], capture_output=True, text=True)
    return p.returncode == 0 and (p.stdout or "").strip() == "running"


def _nm_knows(ifname: str) -> bool:
    nmcli = _nmcli_path()
    if not nmcli:
        return False
    p = subprocess.run([nmcli, "dev", "show", ifname], capture_output=True, text=True)
    return p.returncode == 0


def _nm_disconnect(ifname: str) -> None:
    nmcli = _nmcli_path()
    if not nmcli:
        return
    subprocess.run([nmcli, "dev", "disconnect", ifname], check=False, capture_output=True, text=True)


def _nm_set_managed(ifname: str, managed: bool) -> bool:
    nmcli = _nmcli_path()
    if not nmcli:
        return False
    state = "yes" if managed else "no"
    p = subprocess.run([nmcli, "dev", "set", ifname, "managed", state], capture_output=True, text=True)
    ok = p.returncode == 0
    if ok:
        print(f"nmcli_set_managed iface={ifname} managed={state}")
    else:
        err = (p.stderr or p.stdout or "").strip()
        print(f"nmcli_set_managed_failed iface={ifname} managed={state} err={err}")
    return ok


def _nm_managed_state(ifname: str) -> Optional[bool]:
    nmcli = _nmcli_path()
    if not nmcli:
        return None
    p = subprocess.run(
        [nmcli, "-t", "-f", "GENERAL.MANAGED", "dev", "show", ifname],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    for line in (p.stdout or "").splitlines():
        if line.startswith("GENERAL.MANAGED:"):
            value = line.split(":", 1)[1].strip().lower()
            if value == "yes":
                return True
            if value == "no":
                return False
    return None


def _nm_isolate_iface(ifname: str) -> bool:
    nmcli = _nmcli_path()
    marked_unmanaged = False
    if nmcli and _is_nm_running():
        if _nm_knows(ifname):
            rc, out, err = _run_capture([nmcli, "dev", "disconnect", ifname])
            if rc != 0:
                err_s = _one_line(err or out) or "unknown"
                print(f"WARNING nmcli_disconnect_failed iface={ifname} err={err_s}")
            rc, out, err = _run_capture([nmcli, "dev", "set", ifname, "managed", "no"])
            if rc != 0:
                err_s = _one_line(err or out) or "unknown"
                print(f"WARNING nmcli_set_managed_failed iface={ifname} err={err_s}")
            else:
                marked_unmanaged = True
        else:
            print(f"WARNING nmcli_iface_unknown iface={ifname}")
    elif nmcli:
        print(f"WARNING nmcli_not_running iface={ifname}")

    _iface_down(ifname)
    _flush_ip(ifname)
    _iface_up(ifname)

    managed = _nm_managed_state(ifname)
    if managed is None:
        print(f"nmcli_managed_state iface={ifname} managed=unknown")
    else:
        print(f"nmcli_managed_state iface={ifname} managed={'yes' if managed else 'no'}")

    return marked_unmanaged


def _rfkill_unblock_wifi() -> None:
    rfkill = shutil.which("rfkill")
    if not rfkill:
        return
    subprocess.run([rfkill, "unblock", "wifi"], check=False, capture_output=True, text=True)


def _collect_proc_output(proc: subprocess.Popen) -> List[str]:
    if not proc.stdout:
        return []
    try:
        out = proc.stdout.read()
    except Exception:
        return []
    if not out:
        return []
    return [line for line in out.splitlines() if line]


def _emit_lines(lines: List[str]) -> None:
    for line in lines:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _select_ap_iface(parent_ifname: str, no_virt: bool) -> Tuple[str, Optional[str], bool]:
    if no_virt:
        return parent_ifname, None, False
    virt_ifname = _mk_virt_name(parent_ifname)
    if _iface_uses_driver(parent_ifname, "mt7921u"):
        print(
            "INFO hostapd_virt_skip "
            f"iface={parent_ifname} virt={virt_ifname} driver=mt7921u"
        )
        return parent_ifname, virt_ifname, False
    created, details = _create_virtual_ap_iface(parent_ifname, virt_ifname)
    if _iw_interface_exists(virt_ifname):
        return virt_ifname, virt_ifname, created
    warn_details = details or "none"
    print(
        "WARNING hostapd_virt_iface_missing "
        f"parent={parent_ifname} virt={virt_ifname} details={warn_details}"
    )
    return parent_ifname, virt_ifname, False


_COMPAT_ERROR_PATTERNS = (
    "Failed to set beacon parameters",
    "Could not set channel for kernel driver",
    "Could not connect to kernel driver",
    "Interface initialization failed",
    "Unable to setup interface",
    "nl80211: Failed to set beacon",
    "nl80211: Failed to set interface",
)


def _should_retry_compat(lines: List[str]) -> bool:
    for line in lines:
        for pattern in _COMPAT_ERROR_PATTERNS:
            if pattern in line:
                return True
    return False


def _iptables_add_unique(rule: List[str]) -> None:
    ipt = _which_or_die("iptables")
    check_rule = rule[:]
    check_rule.insert(1, "-C")
    p = subprocess.run([ipt] + check_rule, capture_output=True, text=True)
    if p.returncode == 0:
        return
    add_rule = rule[:]
    add_rule.insert(1, "-A")
    _run([ipt] + add_rule, check=True)


def _iptables_del(rule: List[str]) -> None:
    ipt = shutil.which("iptables")
    if not ipt:
        return
    del_rule = rule[:]
    del_rule.insert(1, "-D")
    subprocess.run([ipt] + del_rule, check=False, capture_output=True, text=True)


def _nat_up(ap_if: str, uplink_if: str) -> List[List[str]]:
    rules: List[List[str]] = []

    r1 = ["-t", "nat", "POSTROUTING", "-o", uplink_if, "-j", "MASQUERADE"]
    _iptables_add_unique(r1)
    rules.append(r1)

    r2 = [
        "FORWARD",
        "-i",
        uplink_if,
        "-o",
        ap_if,
        "-m",
        "state",
        "--state",
        "RELATED,ESTABLISHED",
        "-j",
        "ACCEPT",
    ]
    _iptables_add_unique(r2)
    rules.append(r2)

    r3 = ["FORWARD", "-i", ap_if, "-o", uplink_if, "-j", "ACCEPT"]
    _iptables_add_unique(r3)
    rules.append(r3)

    return rules


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
    channel_width: str = "auto",
    beacon_interval: int = 50,
    dtim_period: int = 1,
    short_guard_interval: bool = True,
    tx_power: Optional[int] = None,
    mode: str = "full",
) -> Dict[str, Optional[int]]:
    cc = (country or "").strip().upper()

    chwidth_map = {"20": 0, "40": 1, "80": 2, "160": 3, "auto": 2}
    chwidth = chwidth_map.get(channel_width.lower(), 2)
    width_mhz_map = {0: 20, 1: 40, 2: 80, 3: 160}
    channel_width_mhz = width_mhz_map.get(chwidth, 80)
    mode = (mode or "full").strip().lower()
    if mode not in ("full", "reduced", "legacy"):
        mode = "full"
    compat = mode == "legacy"
    reduced = mode == "reduced"

    secondary_channel: Optional[int] = None
    ieee80211n = 0
    ieee80211ac = 0
    ieee80211ax = 0
    vht_oper_chwidth: Optional[int] = None
    vht_oper_centr_freq_seg0_idx: Optional[int] = None

    def _secondary_channel_5ghz(primary_channel: int) -> int:
        if primary_channel in {36, 44, 149, 157}:
            return 1
        if primary_channel in {40, 48, 153, 161}:
            return -1
        return 1

    def _vht_center_seg0_idx_5ghz(primary_channel: int, width: int) -> Optional[int]:
        if width < 2:
            return None
        if width == 2:
            blocks = (
                (36, 48, 42),
                (52, 64, 58),
                (100, 112, 106),
                (116, 128, 122),
                (132, 144, 138),
                (149, 161, 155),
            )
        else:
            blocks = (
                (36, 64, 50),
                (100, 128, 114),
                (149, 177, 163),
            )
        for start, end, center in blocks:
            if start <= primary_channel <= end:
                return center
        return None

    if compat:
        beacon_interval = 100
        dtim_period = 2

    lines = [
        f"interface={ifname}",
        "driver=nl80211",
        "ctrl_interface=/run/hostapd",
        "ctrl_interface_group=0",
        f"ssid={ssid}",
        f"beacon_int={beacon_interval}",
        f"dtim_period={dtim_period}",
        f"wmm_enabled={0 if compat else 1}",
    ]

    if cc and len(cc) == 2:
        lines += [f"country_code={cc}", "ieee80211d=1"]

    if band == "2.4ghz":
        channel_width_mhz = 20
        lines += ["hw_mode=g", f"channel={int(channel)}"]
        if not compat:
            ieee80211n = 1
            lines.append("ieee80211n=1")
            if short_guard_interval:
                lines.append("ht_capab=[SHORT-GI-20][SHORT-GI-40]")
    elif band == "5ghz":
        lines += ["hw_mode=a", f"channel={int(channel)}"]
        if not compat:
            ieee80211n = 1
            lines.append("ieee80211n=1")
            ht_caps = []
            if short_guard_interval:
                ht_caps.extend(["SHORT-GI-20", "SHORT-GI-40"])
            if channel_width_mhz >= 40:
                secondary_channel = _secondary_channel_5ghz(int(channel))
                ht_caps.append("HT40+" if secondary_channel == 1 else "HT40-")
            if ht_caps:
                lines.append(f"ht_capab=[{']['.join(ht_caps)}]")

            if not reduced:
                ieee80211ac = 1
                lines.append("ieee80211ac=1")
                if channel_width_mhz >= 80:
                    vht_caps = ["SHORT-GI-80", "RXLDPC", "TX-STBC-2BY1", "RX-STBC-1"]
                    if channel_width_mhz >= 160:
                        vht_caps.append("SHORT-GI-160")
                    lines.append(f"vht_capab=[{']['.join(vht_caps)}]")
                    vht_oper_chwidth = 1 if channel_width_mhz == 80 else 2
                    lines.append(f"vht_oper_chwidth={vht_oper_chwidth}")
                    seg0 = _vht_center_seg0_idx_5ghz(int(channel), chwidth)
                    if seg0 is not None:
                        vht_oper_centr_freq_seg0_idx = seg0
                        lines.append(f"vht_oper_centr_freq_seg0_idx={seg0}")
    else:
        raise RuntimeError("invalid_band")

    if wifi6 and not compat and not reduced:
        ieee80211ax = 1
        lines.append("ieee80211ax=1")
        lines += [
            "he_su_beamformee=1",
            "he_su_beamformer=1",
            "he_mu_beamformer=1",
        ]

    if ap_security == "wpa3_sae":
        lines += [
            "wpa=2",
            "wpa_key_mgmt=SAE",
            "rsn_pairwise=CCMP",
            "ieee80211w=2",
            "sae_pwe=2",
            f"sae_password={passphrase}",
        ]
    else:
        lines += [
            "wpa=2",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
            f"wpa_passphrase={passphrase}",
        ]

    if tx_power is not None:
        lines.append(f"tx_power={tx_power}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return {
        "channel": int(channel),
        "channel_width_mhz": channel_width_mhz,
        "secondary_channel": secondary_channel,
        "ieee80211n": ieee80211n,
        "ieee80211ac": ieee80211ac,
        "ieee80211ax": ieee80211ax,
        "vht_oper_chwidth": vht_oper_chwidth,
        "vht_oper_centr_freq_seg0_idx": vht_oper_centr_freq_seg0_idx,
    }


def _emit_hostapd_launch_info(info: Dict[str, Optional[int]]) -> None:
    secondary = info["secondary_channel"]
    parts = [
        "INFO",
        "hostapd_nat_config",
        f"channel={info['channel']}",
        f"channel_width_mhz={info['channel_width_mhz']}",
        f"secondary_channel={'none' if secondary is None else secondary}",
        f"ieee80211n={info['ieee80211n']}",
        f"ieee80211ac={info['ieee80211ac']}",
        f"ieee80211ax={info['ieee80211ax']}",
    ]
    vht_oper_chwidth = info["vht_oper_chwidth"]
    if vht_oper_chwidth is not None:
        parts.append(f"vht_oper_chwidth={vht_oper_chwidth}")
    vht_seg0 = info["vht_oper_centr_freq_seg0_idx"]
    if vht_seg0 is not None:
        parts.append(f"vht_oper_centr_freq_seg0_idx={vht_seg0}")
    print(" ".join(parts))


def _write_dnsmasq_conf(
    path: str,
    ap_if: str,
    gw_ip: str,
    dhcp_start: str,
    dhcp_end: str,
    dhcp_dns: str,
) -> None:
    lines = [
        "bind-interfaces",
        f"interface={ap_if}",
        "except-interface=lo",
        "dhcp-authoritative",
        f"dhcp-range={dhcp_start},{dhcp_end},255.255.255.0,12h",
        f"dhcp-option=option:router,{gw_ip}",
        "domain-needed",
        "bogus-priv",
        "log-dhcp",
        "log-facility=-",
    ]
    if dhcp_dns and dhcp_dns != "no":
        dns_offer = gw_ip if dhcp_dns == "gateway" else dhcp_dns
        lines.append(f"dhcp-option=option:dns-server,{dns_offer}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ap-ifname", required=True)
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--passphrase", required=True)
    ap.add_argument("--band", required=True)
    ap.add_argument("--ap-security", default="wpa2")
    ap.add_argument("--country", default=None)
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--no-virt", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--wifi6", action="store_true")
    ap.add_argument("--gateway-ip", default=None)
    ap.add_argument("--dhcp-start", default=None)
    ap.add_argument("--dhcp-end", default=None)
    ap.add_argument("--dhcp-dns", default=None)
    ap.add_argument("--no-internet", action="store_true")
    ap.add_argument("--channel-width", default="auto")
    ap.add_argument("--beacon-interval", type=int, default=50)
    ap.add_argument("--dtim-period", type=int, default=1)
    ap.add_argument("--short-guard-interval", action="store_true", default=True)
    ap.add_argument("--tx-power", type=int, default=None)
    args = ap.parse_args()

    if len(args.passphrase) < 8:
        raise RuntimeError("invalid_passphrase_min_length_8")

    band = str(args.band).strip().lower()
    if band in ("2", "2g", "2ghz", "2.4", "2.4ghz"):
        band = "2.4ghz"
    elif band in ("5", "5g", "5ghz"):
        band = "5ghz"
    elif band in ("6", "6g", "6ghz", "6e"):
        raise RuntimeError("band_6ghz_requires_hostapd6_engine")
    else:
        raise RuntimeError("invalid_band")

    _maybe_set_regdom(args.country)

    gw_ip = (args.gateway_ip or "192.168.68.1").strip()
    try:
        gw_addr = ipaddress.IPv4Address(gw_ip)
    except Exception as exc:
        raise RuntimeError("invalid_gateway_ip") from exc
    subnet_prefix = ".".join(gw_ip.split(".")[:3])
    dhcp_start = (args.dhcp_start or f"{subnet_prefix}.10").strip()
    dhcp_end = (args.dhcp_end or f"{subnet_prefix}.250").strip()
    try:
        start_addr = ipaddress.IPv4Address(dhcp_start)
        end_addr = ipaddress.IPv4Address(dhcp_end)
    except Exception as exc:
        raise RuntimeError("invalid_dhcp_range") from exc
    if int(start_addr) >= int(end_addr):
        raise RuntimeError("invalid_dhcp_range")
    if (gw_addr.packed[:3] != start_addr.packed[:3]) or (gw_addr.packed[:3] != end_addr.packed[:3]):
        raise RuntimeError("invalid_dhcp_range_subnet")
    cidr = f"{gw_ip}/24"
    dhcp_dns = (args.dhcp_dns or "gateway").strip().lower()
    if not dhcp_dns:
        dhcp_dns = "gateway"
    if dhcp_dns not in ("gateway", "no"):
        ips = [p.strip() for p in dhcp_dns.split(",") if p.strip()]
        if not ips:
            raise RuntimeError("invalid_dhcp_dns")
        try:
            for ip in ips:
                ipaddress.IPv4Address(ip)
        except Exception as exc:
            raise RuntimeError("invalid_dhcp_dns") from exc
        dhcp_dns = ",".join(ips)

    hostapd = _resolve_binary("hostapd", "HOSTAPD")
    dnsmasq = _resolve_binary("dnsmasq", "DNSMASQ")

    tmpdir = tempfile.mkdtemp(prefix="vr-hotspotd-nat-")
    hostapd_conf = os.path.join(tmpdir, "hostapd.conf")
    hostapd_log = os.path.join(tmpdir, "hostapd.log")
    dnsmasq_conf = os.path.join(tmpdir, "dnsmasq.conf")

    ap_iface, virt_iface, created_virt = _select_ap_iface(args.ap_ifname, args.no_virt)

    _rfkill_unblock_wifi()
    nm_marked_unmanaged: Optional[str] = None

    channel = int(args.channel) if args.channel is not None else (6 if band == "2.4ghz" else 36)
    requested_channel_width = str(args.channel_width or "auto").lower()
    normalized_channel_width = _normalize_channel_width(band, requested_channel_width, channel)

    mode = "full"
    hostapd_p: Optional[subprocess.Popen] = None
    early_rc: Optional[int] = None

    while True:
        hostapd_info = _write_hostapd_conf(
            path=hostapd_conf,
            ifname=ap_iface,
            ssid=args.ssid,
            passphrase=args.passphrase,
            country=args.country,
            band=band,
            channel=channel,
            ap_security=str(args.ap_security).strip().lower(),
            wifi6=bool(args.wifi6),
            channel_width=normalized_channel_width,
            beacon_interval=args.beacon_interval,
            dtim_period=args.dtim_period,
            short_guard_interval=args.short_guard_interval,
            tx_power=args.tx_power,
            mode=mode,
        )
        secondary_channel = hostapd_info["secondary_channel"]
        vht_oper_chwidth = hostapd_info["vht_oper_chwidth"]
        vht_seg0 = hostapd_info["vht_oper_centr_freq_seg0_idx"]
        channel_width_mhz = hostapd_info["channel_width_mhz"]
        reduced = mode == "reduced"
        parts = [
            "INFO",
            "hostapd_write_conf",
            f"mode={mode}",
            f"band={band}",
            f"primary_channel={channel}",
            f"requested_width_mhz={channel_width_mhz}",
            f"reduced={'true' if reduced else 'false'}",
            f"secondary_channel={'none' if secondary_channel is None else secondary_channel}",
            f"ieee80211ac={hostapd_info['ieee80211ac']}",
            f"ieee80211ax={hostapd_info['ieee80211ax']}",
            f"vht_oper_chwidth={'none' if vht_oper_chwidth is None else vht_oper_chwidth}",
            f"vht_oper_centr_freq_seg0_idx={'none' if vht_seg0 is None else vht_seg0}",
        ]
        print(" ".join(parts))
        if channel_width_mhz >= 80 or args.debug:
            print(f"hostapd_conf_path={hostapd_conf}")
            print(f"hostapd_log_path={hostapd_log}")
            print(
                "hostapd_conf_key "
                f"channel={channel} "
                f"width_mhz={channel_width_mhz} "
                f"ieee80211ac={hostapd_info['ieee80211ac']} "
                f"vht_oper_chwidth={'none' if vht_oper_chwidth is None else vht_oper_chwidth} "
                f"vht_oper_centr_freq_seg0_idx={'none' if vht_seg0 is None else vht_seg0}"
            )
            with open(hostapd_conf, "r", encoding="utf-8") as f:
                for line in f:
                    print(f"hostapd_conf: {line.rstrip()}")
        _ensure_ctrl_interface_dir(hostapd_conf)
        if _nm_isolate_iface(ap_iface) and nm_marked_unmanaged is None:
            nm_marked_unmanaged = ap_iface
        os.makedirs(tmpdir, exist_ok=True)
        try:
            with open(hostapd_log, "a", encoding="utf-8"):
                pass
        except Exception as exc:
            print(f"hostapd_log_touch_failed: {hostapd_log} err={exc}")
        hostapd_cmd = [hostapd, "-f", hostapd_log, hostapd_conf]

        if args.debug:
            hostapd_cmd = [hostapd, "-dd", "-f", hostapd_log, hostapd_conf]

        _emit_hostapd_launch_info(hostapd_info)
        hostapd_p = subprocess.Popen(
            hostapd_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(0.6)
        if hostapd_p.poll() is None:
            break

        lines = _collect_proc_output(hostapd_p)
        _emit_lines(lines)
        if mode == "legacy" or not _should_retry_compat(lines):
            early_rc = hostapd_p.returncode or 1
            break

        if mode == "full":
            print("hostapd_compat_retry: reduced config")
            mode = "reduced"
        else:
            print("hostapd_compat_retry: legacy config")
            mode = "legacy"
        _iface_down(ap_iface)
        _flush_ip(ap_iface)
        _iface_up(ap_iface)
    dnsmasq_p: Optional[subprocess.Popen] = None
    nat_rules: List[List[str]] = []

    stopping = False

    def _stop_children():
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for p in (hostapd_p, dnsmasq_p):
            if not p:
                continue
            try:
                p.terminate()
            except Exception:
                pass

    def _on_sigterm(_signum, _frame):
        _stop_children()

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    try:
        if hostapd_p is None:
            raise RuntimeError("hostapd_start_failed")

        if early_rc is None:
            _assign_ip(ap_iface, cidr)

            uplink = _default_uplink_iface()
            if not args.no_internet:
                _sysctl_ip_forward(True)
                if uplink and not _is_firewalld_active():
                    try:
                        nat_rules = _nat_up(ap_iface, uplink)
                    except Exception:
                        nat_rules = []

            _write_dnsmasq_conf(dnsmasq_conf, ap_iface, gw_ip, dhcp_start, dhcp_end, dhcp_dns)
            dnsmasq_cmd = [dnsmasq, "--no-daemon", f"--conf-file={dnsmasq_conf}"]
            dnsmasq_p = subprocess.Popen(
                dnsmasq_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )

            while True:
                if hostapd_p.poll() is not None or (dnsmasq_p and dnsmasq_p.poll() is not None):
                    break

                for p in (hostapd_p, dnsmasq_p):
                    if not p or not p.stdout:
                        continue
                    line = p.stdout.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()

                time.sleep(0.05)
    finally:
        _stop_children()
        for p in (hostapd_p, dnsmasq_p):
            if not p:
                continue
            try:
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        if hostapd_p:
            _emit_lines(_collect_proc_output(hostapd_p))
        if dnsmasq_p:
            _emit_lines(_collect_proc_output(dnsmasq_p))

        for r in reversed(nat_rules):
            _iptables_del(r)

        try:
            ip = shutil.which("ip") or "/usr/sbin/ip"
            subprocess.run([ip, "addr", "flush", "dev", ap_iface], check=False, capture_output=True, text=True)
        except Exception:
            pass

        if created_virt and virt_iface:
            _delete_iface(virt_iface)

        if nm_marked_unmanaged:
            _nm_set_managed(nm_marked_unmanaged, True)

        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    if early_rc is not None:
        return early_rc

    rc_h = hostapd_p.returncode if hostapd_p and hostapd_p.returncode is not None else 0
    rc_d = 0
    if dnsmasq_p and dnsmasq_p.returncode is not None:
        rc_d = dnsmasq_p.returncode
    return rc_h if rc_h != 0 else rc_d


if __name__ == "__main__":
    raise SystemExit(main())
