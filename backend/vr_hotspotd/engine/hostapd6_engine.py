import argparse
import ipaddress
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional, List, Tuple


def _run(cmd: List[str], check: bool = True) -> Tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
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
    # ip route show default -> "default via X.X.X.X dev eth0 proto ..."
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
    # Best-effort; some systems/drivers will ignore.
    subprocess.run([iw, "reg", "set", cc], check=False, capture_output=True, text=True)


def _mk_virt_name(base: str) -> str:
    # linux-router commonly uses x0wlan1 style; we keep x0 prefix.
    # Ensure <= 15 chars for ifname.
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


def _assign_ip(ifname: str, cidr: str) -> None:
    ip = shutil.which("ip") or "/usr/sbin/ip"
    # Flush first to avoid duplicates
    subprocess.run([ip, "addr", "flush", "dev", ifname], check=False, capture_output=True, text=True)
    _run([ip, "addr", "add", cidr, "dev", ifname], check=True)


def _sysctl_ip_forward(enable: bool = True) -> None:
    val = "1" if enable else "0"
    subprocess.run(["sysctl", "-w", f"net.ipv4.ip_forward={val}"], check=False, capture_output=True, text=True)


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
    """
    Returns list of rule specs (without -A/-D) to delete later.
    """
    rules: List[List[str]] = []

    # NAT masquerade
    r1 = ["-t", "nat", "POSTROUTING", "-o", uplink_if, "-j", "MASQUERADE"]
    _iptables_add_unique(r1)
    rules.append(r1)

    # Forwarding
    r2 = ["FORWARD", "-i", uplink_if, "-o", ap_if, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"]
    _iptables_add_unique(r2)
    rules.append(r2)

    r3 = ["FORWARD", "-i", ap_if, "-o", uplink_if, "-j", "ACCEPT"]
    _iptables_add_unique(r3)
    rules.append(r3)

    return rules


def _write_hostapd_6ghz_conf(
    *,
    path: str,
    ifname: str,
    ssid: str,
    passphrase: str,
    country: Optional[str],
    channel: int,
) -> None:
    """
    Minimal 6 GHz + WPA3-SAE hostapd config.
    Key points (6 GHz / WPA3):
      - wpa_key_mgmt=SAE
      - ieee80211w=2 (PMF required)
      - sae_pwe=2 (H2E-only is recommended/expected for 6 GHz in many deployments)
      - op_class=131 is commonly used for 6 GHz 20 MHz operation
    """
    cc = (country or "").strip().upper()
    lines = [
        f"interface={ifname}",
        "driver=nl80211",
        "ctrl_interface=/run/hostapd",
        "ctrl_interface_group=0",
        f"ssid={ssid}",
        "hw_mode=a",
        f"channel={int(channel)}",
        "op_class=131",
        "ieee80211ax=1",
        "wmm_enabled=1",
        # 6 GHz HE operating params (20 MHz)
        "he_oper_chwidth=0",
        f"he_oper_centr_freq_seg0_idx={int(channel)}",
        # Security: WPA3-SAE only
        "wpa=2",
        "wpa_key_mgmt=SAE",
        "rsn_pairwise=CCMP",
        "ieee80211w=2",
        "sae_pwe=2",
        f"sae_password={passphrase}",
    ]
    if cc and len(cc) == 2:
        lines += [
            f"country_code={cc}",
            "ieee80211d=1",
        ]

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
        # Keep dnsmasq minimal; upstream DNS is handled by the host
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
    ap.add_argument("--country", default=None)
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--no-virt", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--gateway-ip", default=None)
    ap.add_argument("--dhcp-start", default=None)
    ap.add_argument("--dhcp-end", default=None)
    ap.add_argument("--dhcp-dns", default=None)
    ap.add_argument("--no-internet", action="store_true")
    args = ap.parse_args()

    if len(args.passphrase) < 8:
        raise RuntimeError("invalid_passphrase_min_length_8")

    _maybe_set_regdom(args.country)

    # Network plan
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

    tmpdir = tempfile.mkdtemp(prefix="vr-hotspotd-6ghz-")
    hostapd_conf = os.path.join(tmpdir, "hostapd.conf")
    dnsmasq_conf = os.path.join(tmpdir, "dnsmasq.conf")

    created_virt = False
    ap_iface = args.ap_ifname

    # Create virtual AP iface unless no-virt
    if not args.no_virt:
        virt = _mk_virt_name(args.ap_ifname)
        _create_virtual_ap_iface(args.ap_ifname, virt)
        created_virt = True
        ap_iface = virt

    # Bring up + address
    _iface_up(ap_iface)
    _assign_ip(ap_iface, cidr)

    # NAT
    uplink = _default_uplink_iface()
    nat_rules: List[List[str]] = []
    if not args.no_internet:
        _sysctl_ip_forward(True)
        if uplink and not _is_firewalld_active():
            try:
                nat_rules = _nat_up(ap_iface, uplink)
            except Exception:
                nat_rules = []

    # Write configs
    os.makedirs("/run/hostapd", exist_ok=True)
    _write_hostapd_6ghz_conf(
        path=hostapd_conf,
        ifname=ap_iface,
        ssid=args.ssid,
        passphrase=args.passphrase,
        country=args.country,
        channel=int(args.channel),
    )
    _write_dnsmasq_conf(dnsmasq_conf, ap_iface, gw_ip, dhcp_start, dhcp_end, dhcp_dns)

    # Start processes
    hostapd_cmd = [hostapd, hostapd_conf]
    dnsmasq_cmd = [dnsmasq, "--no-daemon", f"--conf-file={dnsmasq_conf}"]

    if args.debug:
        hostapd_cmd = [hostapd, "-dd", hostapd_conf]

    hostapd_p = subprocess.Popen(hostapd_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    dnsmasq_p = subprocess.Popen(dnsmasq_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    stopping = False

    def _stop_children():
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for p in (dnsmasq_p, hostapd_p):
            try:
                p.terminate()
            except Exception:
                pass

    def _on_sigterm(_signum, _frame):
        _stop_children()

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    # Simple pump: forward child output to our stdout so supervisor tail captures it
    try:
        while True:
            # If either dies, exit (supervisor will mark failure)
            if hostapd_p.poll() is not None or dnsmasq_p.poll() is not None:
                break

            # Drain a little output to keep logs flowing
            for p in (hostapd_p, dnsmasq_p):
                if p.stdout:
                    line = p.stdout.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()

            time.sleep(0.05)
    finally:
        _stop_children()
        for p in (dnsmasq_p, hostapd_p):
            try:
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

        # Cleanup NAT rules
        for r in reversed(nat_rules):
            _iptables_del(r)

        # Best-effort cleanup iface + addresses
        try:
            ip = shutil.which("ip") or "/usr/sbin/ip"
            subprocess.run([ip, "addr", "flush", "dev", ap_iface], check=False, capture_output=True, text=True)
        except Exception:
            pass

        if created_virt:
            _delete_iface(ap_iface)

        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    # Exit code: prefer hostapd
    rc_h = hostapd_p.returncode if hostapd_p.returncode is not None else 0
    rc_d = dnsmasq_p.returncode if dnsmasq_p.returncode is not None else 0
    return rc_h if rc_h != 0 else rc_d


if __name__ == "__main__":
    raise SystemExit(main())
