import argparse
import hashlib
import ipaddress
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, List, Tuple

from vr_hotspotd import os_release

_CTRL_DIR_RE = re.compile(r"DIR=([^\s]+)")
_CMD_TIMEOUT_S = 4.0
_LNXROUTER_TMPDIR_ENV = "VR_HOTSPOT_LNXROUTER_TMPDIR"
_DEFAULT_LNXROUTER_TMPDIR = "/dev/shm/lnxrouter_tmp"


def _is_bazzite() -> bool:
    try:
        return os_release.is_bazzite()
    except Exception:
        return False


def _is_pop_os() -> bool:
    try:
        return os_release.is_pop_os()
    except Exception:
        return False


def _write_pidfile(path: Path, pid: int) -> None:
    try:
        path.write_text(f"{pid}\n", encoding="utf-8")
    except Exception as exc:
        print(f"pidfile_write_failed: {path} err={exc}")


def _remove_pidfile(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"pidfile_remove_failed: {path} err={exc}")


def _run(cmd: List[str], check: bool = True, timeout_s: float = _CMD_TIMEOUT_S) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        raise RuntimeError(f"cmd_timeout cmd={' '.join(cmd)} out={out.strip()}") from exc
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd_failed rc={p.returncode} cmd={' '.join(cmd)} out={out.strip()}")
    return p.returncode, out


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


def _resolve_lnxrouter_tmp_root() -> Path:
    override = (os.environ.get(_LNXROUTER_TMPDIR_ENV) or "").strip()
    if override:
        return Path(override)
    return Path(_DEFAULT_LNXROUTER_TMPDIR)


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


def _is_iface_name_conflict_text(text: object) -> bool:
    low = str(text or "").lower()
    return ("name not unique on network" in low) or ("file exists" in low)


def _is_iface_name_conflict_exc(exc: Exception) -> bool:
    return _is_iface_name_conflict_text(exc)


def _virt_name_candidates(parent_if: str, preferred: Optional[str] = None) -> List[str]:
    token = re.sub(r"[^A-Za-z0-9]", "", str(parent_if or ""))
    if not token:
        token = "ap"
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
    raw: List[str] = []
    if preferred:
        raw.append(preferred)
    raw.append(_mk_virt_name(parent_if))
    for i in range(0, 8):
        raw.append(f"x{digest[i]}{token}")
    for i in range(0, 10):
        raw.append(f"x{i}{token}")
    out: List[str] = []
    seen = set()
    for cand in raw:
        norm = re.sub(r"[^A-Za-z0-9_.-]", "", str(cand or ""))[:15]
        if len(norm) < 2:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _create_virtual_ap_iface_with_fallback(parent_if: str, preferred_if: Optional[str] = None) -> str:
    last_exc: Optional[Exception] = None
    tried: List[str] = []
    for virt_if in _virt_name_candidates(parent_if, preferred=preferred_if):
        tried.append(virt_if)
        # Best-effort stale cleanup before creation attempt.
        _delete_iface(virt_if)
        try:
            _create_virtual_ap_iface(parent_if, virt_if)
            return virt_if
        except Exception as exc:
            last_exc = exc
            if _is_iface_name_conflict_exc(exc):
                _delete_iface(virt_if)
                try:
                    _create_virtual_ap_iface(parent_if, virt_if)
                    return virt_if
                except Exception as exc_retry:
                    last_exc = exc_retry
                    if _is_iface_name_conflict_exc(exc_retry):
                        continue
                    raise
            if _is_iface_name_conflict_exc(exc):
                continue
            raise
    raise RuntimeError(
        f"virtual_iface_create_failed parent={parent_if} tried={','.join(tried)} err={last_exc}"
    )


def _create_virtual_ap_iface(parent_if: str, virt_if: str) -> None:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    _run([iw, "dev", parent_if, "interface", "add", virt_if, "type", "__ap"], check=True)


def _delete_iface(ifname: str) -> None:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    subprocess.run([iw, "dev", ifname, "del"], check=False, capture_output=True, text=True)


def _iface_up(ifname: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    _run([ip, "link", "set", ifname, "up"], check=True)


def _iface_disconnect(ifname: str) -> None:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    subprocess.run([iw, "dev", ifname, "disconnect"], check=False, capture_output=True, text=True)


def _remove_p2p_dev_ifaces(parent_if: str) -> List[str]:
    removed: List[str] = []
    if not parent_if:
        return removed
    iw = shutil.which("iw") or "/usr/sbin/iw"
    try:
        _, out = _run([iw, "dev"], check=False)
    except Exception:
        return removed

    candidates: List[str] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line.startswith("Interface "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ifname = parts[1].strip()
        if not ifname:
            continue
        if ifname == f"p2p-dev-{parent_if}" or (ifname.startswith("p2p-dev-") and ifname.endswith(parent_if)):
            candidates.append(ifname)

    for ifname in sorted(set(candidates)):
        p = subprocess.run([iw, "dev", ifname, "del"], check=False, capture_output=True, text=True)
        if p.returncode == 0:
            removed.append(ifname)
            print(f"p2p_iface_removed iface={ifname}")
        else:
            err = (p.stderr or p.stdout or "").strip()
            if err:
                print(f"p2p_iface_remove_failed iface={ifname} err={err}")
    return removed


def _set_iface_type_ap(ifname: str) -> bool:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    p = subprocess.run(
        [iw, "dev", ifname, "set", "type", "__ap"],
        check=False,
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        print(f"iface_type_set_ap iface={ifname}")
        return True
    err = (p.stderr or p.stdout or "").strip()
    if err:
        print(f"iface_type_set_ap_failed iface={ifname} err={err}")
    return False


def _set_iface_type_managed(ifname: str) -> bool:
    iw = shutil.which("iw") or "/usr/sbin/iw"
    p = subprocess.run(
        [iw, "dev", ifname, "set", "type", "managed"],
        check=False,
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        print(f"iface_type_set_managed iface={ifname}")
        return True
    err = (p.stderr or p.stdout or "").strip()
    if err:
        print(f"iface_type_set_managed_failed iface={ifname} err={err}")
    return False


def _iface_up_with_recovery(ifname: str, *, no_virt: bool = False) -> None:
    last_exc: Optional[Exception] = None
    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            _iface_up(ifname)
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= (max_attempts - 1):
                break
            # Best-effort release sequence for adapters still held by managed mode/supplicant.
            print(f"iface_up_retry iface={ifname} attempt={attempt + 1}/{max_attempts} reason={exc}")
            _rfkill_unblock_wifi()
            if _is_nm_running() and _nm_knows(ifname):
                _nm_disconnect(ifname)
                _nm_set_managed(ifname, False)
            _nm_disconnect(ifname)
            _remove_p2p_dev_ifaces(ifname)
            _iface_disconnect(ifname)
            _iface_down(ifname)
            if no_virt:
                _set_iface_type_managed(ifname)
                time.sleep(0.05)
                _set_iface_type_ap(ifname)
            _flush_ip(ifname)
            # Busy nl80211 transitions can take a moment to settle on Pop!/MT7921U.
            time.sleep(0.35 + (0.25 * attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"iface_up_failed:{ifname}")


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
) -> None:
    cc = (country or "").strip().upper()

    chwidth_map = {"20": 0, "40": 1, "80": 2, "160": 3, "auto": 2}
    chwidth = chwidth_map.get(channel_width.lower(), 2)
    mode = (mode or "full").strip().lower()
    if mode not in ("full", "reduced", "legacy"):
        mode = "full"
    compat = mode == "legacy"
    reduced = mode == "reduced"

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

    def _ht40_capab_5ghz(primary_channel: int) -> Optional[str]:
        plus = {
            36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157
        }
        minus = {
            40, 48, 56, 64, 104, 112, 120, 128, 136, 144, 153, 161
        }
        if primary_channel in plus:
            return "HT40+"
        if primary_channel in minus:
            return "HT40-"
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
        lines += ["hw_mode=g", f"channel={int(channel)}"]
        if not compat:
            lines.append("ieee80211n=1")
            if short_guard_interval:
                lines.append("ht_capab=[SHORT-GI-20][SHORT-GI-40]")
    elif band == "5ghz":
        lines += ["hw_mode=a", f"channel={int(channel)}"]
        if not compat:
            lines.append("ieee80211n=1")
            if not reduced:
                lines.append("ieee80211ac=1")
            if short_guard_interval:
                ht_caps = ["SHORT-GI-20", "SHORT-GI-40"]
                if (not reduced) and chwidth >= 2:
                    ht40 = _ht40_capab_5ghz(int(channel))
                    if ht40:
                        ht_caps.append(ht40)
                    lines.append("require_ht=1")
                lines.append(f"ht_capab=[{']['.join(ht_caps)}]")
                if (not reduced) and chwidth >= 2:
                    vht_caps = ["SHORT-GI-80"]
                    if chwidth >= 3:
                        vht_caps.append("SHORT-GI-160")
                    lines.append(f"vht_capab=[{']['.join(vht_caps)}]")
                    lines.append("require_vht=1")
            if (not reduced) and chwidth >= 2:
                seg0 = _vht_center_seg0_idx_5ghz(int(channel), chwidth)
                if seg0 is not None:
                    lines.append(f"vht_oper_chwidth={chwidth - 1}")
                    lines.append(f"vht_oper_centr_freq_seg0_idx={seg0}")
    else:
        raise RuntimeError("invalid_band")

    if wifi6 and not compat and not reduced:
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
    ap.add_argument("--strict-width", action="store_true")
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

    # Align with lifecycle.py expectations (with optional test override).
    base_tmp_path = _resolve_lnxrouter_tmp_root()
    base_tmp_path.mkdir(parents=True, exist_ok=True)
    if (_LNXROUTER_TMPDIR_ENV in os.environ) and base_tmp_path.exists():
        try:
            os.chmod(base_tmp_path, 0o700)
        except Exception:
            # Best-effort hardening for override paths used in tests/sandboxes.
            pass
    base_tmp = str(base_tmp_path)
    prefix = f"lnxrouter.{args.ap_ifname}.conf."
    tmpdir = tempfile.mkdtemp(prefix=prefix, dir=base_tmp)
    # Ensure correct permissions for the directory
    os.chmod(tmpdir, 0o755)

    hostapd_conf = os.path.join(tmpdir, "hostapd.conf")
    dnsmasq_conf = os.path.join(tmpdir, "dnsmasq.conf")
    bazzite = _is_bazzite()
    hostapd_pid_path = Path(tmpdir) / "hostapd.pid"
    dnsmasq_pid_path = Path(tmpdir) / "dnsmasq.pid"

    created_virt = False
    ap_iface = args.ap_ifname
    nm_marked_unmanaged: Optional[str] = None
    hostapd_p: Optional[subprocess.Popen] = None
    dnsmasq_p: Optional[subprocess.Popen] = None
    nat_rules: List[List[str]] = []
    early_rc: Optional[int] = None
    early_lines: List[str] = []

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

    try:
        _remove_p2p_dev_ifaces(args.ap_ifname)
        if not args.no_virt:
            virt = _create_virtual_ap_iface_with_fallback(args.ap_ifname, _mk_virt_name(args.ap_ifname))
            created_virt = True
            ap_iface = virt

        if _is_nm_running() and _nm_knows(ap_iface):
            _nm_disconnect(ap_iface)
            if _nm_set_managed(ap_iface, False):
                nm_marked_unmanaged = ap_iface

        _rfkill_unblock_wifi()

        def _prepare_ap_iface_for_start() -> None:
            nonlocal ap_iface, nm_marked_unmanaged
            _iface_down(ap_iface)
            _flush_ip(ap_iface)
            if args.no_virt:
                _set_iface_type_ap(ap_iface)
            try:
                _iface_up_with_recovery(ap_iface, no_virt=bool(args.no_virt))
            except Exception as exc:
                if created_virt and (not args.no_virt) and _is_iface_name_conflict_exc(exc):
                    print(f"virt_iface_name_conflict_recreate iface={ap_iface} err={exc}")
                    _delete_iface(ap_iface)
                    ap_iface = _create_virtual_ap_iface_with_fallback(args.ap_ifname)
                    if _is_nm_running() and _nm_knows(ap_iface):
                        _nm_disconnect(ap_iface)
                        if _nm_set_managed(ap_iface, False):
                            nm_marked_unmanaged = ap_iface
                    _iface_down(ap_iface)
                    _flush_ip(ap_iface)
                    _iface_up_with_recovery(ap_iface, no_virt=False)
                    return
                raise

        _prepare_ap_iface_for_start()

        channel = int(args.channel) if args.channel is not None else (6 if band == "2.4ghz" else 36)

        mode = "full"
        strict_width = bool(args.strict_width)

        while True:
            _write_hostapd_conf(
                path=hostapd_conf,
                ifname=ap_iface,
                ssid=args.ssid,
                passphrase=args.passphrase,
                country=args.country,
                band=band,
                channel=channel,
                ap_security=str(args.ap_security).strip().lower(),
                wifi6=bool(args.wifi6),
                channel_width=args.channel_width,
                beacon_interval=args.beacon_interval,
                dtim_period=args.dtim_period,
                short_guard_interval=args.short_guard_interval,
                tx_power=args.tx_power,
                mode=mode,
            )
            _ensure_ctrl_interface_dir(hostapd_conf)
            hostapd_cmd = [hostapd, hostapd_conf]

            if args.debug:
                hostapd_cmd = [hostapd, "-dd", hostapd_conf]

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
            early_lines = lines
            _emit_lines(lines)
            if strict_width:
                early_rc = hostapd_p.returncode or 1
                break

            if mode == "legacy" or not _should_retry_compat(lines):
                early_rc = hostapd_p.returncode or 1
                if not early_lines:
                    _emit_lines(
                        [
                            f"hostapd_start_failed strict_width=0 mode={mode} rc={early_rc}",
                            "hostapd_start_failed_reason=no_output",
                        ]
                    )
                else:
                    _emit_lines([f"hostapd_start_failed strict_width=0 mode={mode} rc={early_rc}"])
                break

            if mode == "full":
                print("hostapd_compat_retry: reduced config")
                mode = "reduced"
            else:
                print("hostapd_compat_retry: legacy config")
                mode = "legacy"
            _prepare_ap_iface_for_start()

        if bazzite and hostapd_p and hostapd_p.poll() is None:
            _write_pidfile(hostapd_pid_path, hostapd_p.pid)
            print(f"pidfile_written: {hostapd_pid_path}")

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGINT, _on_sigterm)
    except Exception:
        _stop_children()
        for p in (hostapd_p, dnsmasq_p):
            if not p:
                continue
            try:
                p.wait(timeout=0.5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        try:
            ip = shutil.which("ip") or "/usr/sbin/ip"
            subprocess.run([ip, "addr", "flush", "dev", ap_iface], check=False, capture_output=True, text=True)
        except Exception:
            pass
        if created_virt:
            _delete_iface(ap_iface)
        elif args.no_virt:
            _set_iface_type_managed(ap_iface)
        if nm_marked_unmanaged:
            _nm_set_managed(nm_marked_unmanaged, True)
        if bazzite:
            _remove_pidfile(hostapd_pid_path)
            _remove_pidfile(dnsmasq_pid_path)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        raise

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
            if bazzite and dnsmasq_p and dnsmasq_p.poll() is None:
                _write_pidfile(dnsmasq_pid_path, dnsmasq_p.pid)
                print(f"pidfile_written: {dnsmasq_pid_path}")

            while True:
                if hostapd_p.poll() is not None or (dnsmasq_p and dnsmasq_p.poll() is not None):
                    break

                streams = [p.stdout for p in (hostapd_p, dnsmasq_p) if p and p.stdout]
                if not streams:
                    time.sleep(0.1)
                    continue

                ready, _, _ = select.select(streams, [], [], 0.2)
                for stream in ready:
                    try:
                        line = stream.readline()
                    except Exception:
                        line = ""
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
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

        if created_virt:
            _delete_iface(ap_iface)
        elif args.no_virt:
            _set_iface_type_managed(ap_iface)

        if nm_marked_unmanaged:
            _nm_set_managed(nm_marked_unmanaged, True)

        if bazzite:
            _remove_pidfile(hostapd_pid_path)
            _remove_pidfile(dnsmasq_pid_path)

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
