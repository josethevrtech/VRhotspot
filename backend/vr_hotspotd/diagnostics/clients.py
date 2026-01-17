from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd.config import load_config
from vr_hotspotd.engine import lnxrouter_conf


LNXROUTER_TMP = lnxrouter_conf.DEFAULT_LNXROUTER_TMP


@dataclass(frozen=True)
class Client:
    mac: str
    ip: Optional[str] = None
    hostname: Optional[str] = None
    authorized: Optional[bool] = None
    authenticated: Optional[bool] = None
    associated: Optional[bool] = None
    signal_dbm: Optional[int] = None
    signal_avg_dbm: Optional[int] = None
    tx_bitrate_mbps: Optional[float] = None
    rx_bitrate_mbps: Optional[float] = None
    inactive_ms: Optional[int] = None
    connected_time_s: Optional[int] = None
    tx_retries: Optional[int] = None
    tx_failed: Optional[int] = None
    tx_packets: Optional[int] = None
    rx_packets: Optional[int] = None
    tx_bytes: Optional[int] = None
    rx_bytes: Optional[int] = None
    source: str = "unknown"  # hostapd_cli | iw | neigh | leases


_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
_KNOWN_NEIGH_STATES = {
    "INCOMPLETE",
    "REACHABLE",
    "STALE",
    "DELAY",
    "PROBE",
    "FAILED",
    "NOARP",
    "PERMANENT",
}


def _is_mac(s: str) -> bool:
    return bool(_MAC_RE.match(s.strip()))


def _run(cmd: List[str], timeout_s: float) -> Tuple[int, str, str]:
    """
    Run a command with a hard timeout. Never allow blocking indefinitely.
    Returns (returncode, stdout, stderr).
    """
    try:
        p = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "").strip() if isinstance(e.stdout, str) else ""
        err = (e.stderr or "").strip() if isinstance(e.stderr, str) else ""
        return 124, out, err
    except Exception as e:
        return 127, "", f"{type(e).__name__}: {e}"


def _vendor_bin() -> Path:
    # backend/vr_hotspotd/diagnostics/clients.py -> backend/vr_hotspotd -> backend
    here = Path(__file__).resolve()
    backend_dir = here.parents[2]
    return backend_dir / "vendor" / "bin"


def _hostapd_cli_path() -> Optional[str]:
    vendor = _vendor_bin() / "hostapd_cli"
    if vendor.exists() and os.access(vendor, os.X_OK):
        return str(vendor)
    hostapd = _vendor_bin() / "hostapd"
    if hostapd.exists() and os.access(hostapd, os.X_OK):
        bundled = hostapd.parent / "hostapd_cli"
        if bundled.exists() and os.access(bundled, os.X_OK):
            return str(bundled)
    return None


def _get_config_ssid() -> Optional[str]:
    try:
        cfg = load_config()
    except Exception:
        return None
    if isinstance(cfg, dict):
        ssid = cfg.get("ssid")
        if isinstance(ssid, str) and ssid.strip():
            return ssid.strip()
    return None


def _iw_dev_ifaces() -> Tuple[List[Dict[str, Optional[str]]], str]:
    rc, stdout, stderr = _run(["iw", "dev"], timeout_s=0.8)
    if rc != 0:
        return [], f"iw_dev_failed(rc={rc}):{stderr[:120]}"

    interfaces: List[Dict[str, Optional[str]]] = []
    cur: Optional[Dict[str, Optional[str]]] = None

    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("Interface "):
            if cur:
                interfaces.append(cur)
            parts = line.split()
            cur = {"ifname": parts[1] if len(parts) > 1 else None, "ssid": None, "type": None}
            continue
        if not cur:
            continue
        if line.startswith("type "):
            parts = line.split()
            cur["type"] = parts[1] if len(parts) > 1 else None
        elif line.startswith("ssid "):
            cur["ssid"] = line.split(" ", 1)[1].strip() if " " in line else None

    if cur:
        interfaces.append(cur)

    return interfaces, ""


def _iw_dev_ap_ifaces() -> Tuple[List[Dict[str, Optional[str]]], str]:
    interfaces, warn = _iw_dev_ifaces()
    if warn:
        return [], warn
    ap_ifaces = [
        i for i in interfaces if (i.get("type") or "").upper().startswith("AP") and i.get("ifname")
    ]
    return ap_ifaces, ""


def _matches_ap_adapter(ifname: str, ap_adapter: Optional[str]) -> bool:
    if not ap_adapter:
        return False
    return ifname.startswith("x") and ifname.endswith(ap_adapter)


def _select_ap_interface(
    adapter_ifname: Optional[str],
    ap_interface_hint: Optional[str] = None,
) -> Tuple[Optional[str], List[str], List[str]]:
    warnings: List[str] = []
    interfaces, warn = _iw_dev_ifaces()
    if warn:
        warnings.append(warn)
    ap_ifaces = [
        i for i in interfaces if (i.get("type") or "").upper().startswith("AP") and i.get("ifname")
    ]
    ap_ifnames = [i.get("ifname") for i in ap_ifaces if i.get("ifname")]

    if ap_interface_hint:
        hint = ap_interface_hint.strip()
        if hint:
            for iface in interfaces:
                if iface.get("ifname") == hint:
                    if iface.get("type") and not str(iface.get("type")).upper().startswith("AP"):
                        warnings.append("ap_interface_hint_not_ap")
                    return hint, ap_ifnames, warnings
            warnings.append("ap_interface_hint_missing")
            if not ap_ifaces:
                return hint, ap_ifnames, warnings

    if not ap_ifaces:
        return None, ap_ifnames, warnings

    ssid = _get_config_ssid()
    if ssid:
        matches = [i for i in ap_ifaces if i.get("ssid") == ssid and i.get("ifname")]
        if matches:
            if adapter_ifname:
                for iface in matches:
                    name = iface.get("ifname") or ""
                    if _matches_ap_adapter(name, adapter_ifname):
                        return name, ap_ifnames, warnings
            return matches[0].get("ifname"), ap_ifnames, warnings

    if adapter_ifname:
        for iface in ap_ifaces:
            name = iface.get("ifname") or ""
            if _matches_ap_adapter(name, adapter_ifname):
                return name, ap_ifnames, warnings

    return ap_ifnames[0], ap_ifnames, warnings


def _derive_adapter_from_ap(ap_interface: Optional[str]) -> Optional[str]:
    if not ap_interface:
        return None
    m = re.match(r"^x\d+(.+)$", ap_interface)
    if m:
        return m.group(1)
    return ap_interface


def _candidate_conf_dirs(adapter_ifname: Optional[str]) -> List[Path]:
    return lnxrouter_conf.candidate_conf_dirs(adapter_ifname, tmp_dir=LNXROUTER_TMP)


def _find_latest_conf_dir(adapter_ifname: Optional[str]) -> Optional[Path]:
    return lnxrouter_conf.find_latest_conf_dir(adapter_ifname, tmp_dir=LNXROUTER_TMP)


def _select_conf_dir(
    adapter_ifname: Optional[str],
    ap_interface: Optional[str],
) -> Optional[Path]:
    if not ap_interface:
        return None
    adapter_for_glob = _derive_adapter_from_ap(adapter_ifname) if adapter_ifname else None
    if not adapter_for_glob:
        adapter_for_glob = _derive_adapter_from_ap(ap_interface)
    candidates = _candidate_conf_dirs(adapter_for_glob)
    if not candidates:
        return None

    matches: List[Path] = []
    for cand in candidates:
        if _conf_dir_active(cand, ap_interface):
            matches.append(cand)
    if not matches:
        return None

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def _find_ctrl_dir(conf_dir: Optional[Path], ap_interface: str) -> Optional[Path]:
    return lnxrouter_conf.find_ctrl_dir(conf_dir, ap_interface)


def _hostapd_pid_running(conf_dir: Path) -> bool:
    pid = lnxrouter_conf.read_pid_file(conf_dir / "hostapd.pid")
    if pid is None:
        return False
    return lnxrouter_conf.pid_running(pid)


def _conf_dir_active(conf_dir: Path, ap_interface: str) -> bool:
    if lnxrouter_conf.read_hostapd_conf_interface(conf_dir) != ap_interface:
        return False
    if lnxrouter_conf.find_ctrl_dir(conf_dir, ap_interface):
        return True
    return _hostapd_pid_running(conf_dir)


def _ap_interface_from_conf_dir(conf_dir: Path) -> Optional[str]:
    for reader in (
        lnxrouter_conf.read_subn_iface,
        lnxrouter_conf.read_dnsmasq_conf_interface,
        lnxrouter_conf.read_hostapd_conf_interface,
    ):
        ap_if = reader(conf_dir)
        if ap_if:
            return ap_if
    return None


def _dnsmasq_leases(conf_dir: Path) -> Dict[str, Tuple[str, Optional[str]]]:
    """
    Returns mac -> (ip, hostname?)
    dnsmasq.leases format: <expiry> <mac> <ip> <hostname> <clientid>
    """
    out: Dict[str, Tuple[str, Optional[str]]] = {}
    leases = conf_dir / "dnsmasq.leases"
    if not leases.exists():
        return out
    for line in leases.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mac = parts[1].lower()
        ip = parts[2]
        hostname = parts[3] if parts[3] != "*" else None
        if _is_mac(mac):
            out[mac] = (ip, hostname)
    return out


def _parse_leases(path: Path) -> Dict[str, str]:
    leases: Dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return leases

    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _expiry, mac, _ip, hostname = parts[:4]
        if not mac or not hostname or hostname == "*":
            continue
        leases[mac.lower()] = hostname
    return leases


def _find_leases_file(cfg: dict) -> Optional[Path]:
    keys = (
        "dnsmasq_leases_file",
        "dnsmasq_leases_path",
        "dnsmasq_lease_file",
        "dnsmasq_lease_path",
    )
    for key in keys:
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            p = Path(val)
            if p.exists():
                return p

    defaults = [
        "/var/lib/misc/dnsmasq.leases",
        "/var/lib/dnsmasq/dnsmasq.leases",
        "/var/run/dnsmasq.leases",
        "/run/dnsmasq.leases",
    ]
    for cand in defaults:
        p = Path(cand)
        if p.exists():
            return p

    try:
        for cand in Path("/var/lib/NetworkManager").glob("dnsmasq-*.leases"):
            if cand.exists():
                return cand
    except Exception:
        pass

    return None


def _parse_ip_neigh(text: str) -> List[Dict[str, Optional[str]]]:
    entries: List[Dict[str, Optional[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue

        ip = parts[0]
        dev = None
        mac = None
        state = None

        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                dev = parts[idx + 1]

        if "lladdr" in parts:
            idx = parts.index("lladdr")
            if idx + 1 < len(parts):
                mac = parts[idx + 1].lower()

        for tok in reversed(parts):
            if tok in _KNOWN_NEIGH_STATES:
                state = tok
                break

        entries.append({"ip": ip, "dev": dev, "mac": mac, "state": state})
    return entries


def _ip_neigh(ap_if: str) -> Dict[str, str]:
    """
    Returns mac -> ip from `ip neigh show dev <ap_if>`.
    """
    rc, stdout, _ = _run(["ip", "neigh", "show", "dev", ap_if], timeout_s=0.8)
    if rc != 0:
        return {}
    mapping: Dict[str, str] = {}
    for entry in _parse_ip_neigh(stdout):
        dev = entry.get("dev")
        if dev and dev != ap_if:
            continue
        ip = entry.get("ip")
        mac = entry.get("mac")
        if ip and mac and _is_mac(mac):
            mapping[mac] = ip
    return mapping


def _hostapd_cli_list_stas(ctrl_dir: str, ap_if: str) -> Tuple[Optional[List[str]], str]:
    """
    Try `hostapd_cli list_sta`. Return (macs or None, warning_string_if_any).
    """
    binpath = _hostapd_cli_path()
    if not binpath:
        return None, "hostapd_cli_not_found"

    # Some hostapd_cli builds can hang; hard timeout always.
    cmd = [binpath, "-p", ctrl_dir, "-i", ap_if, "list_sta"]
    rc, stdout, stderr = _run(cmd, timeout_s=0.8)
    if rc != 0:
        return None, f"hostapd_cli_failed(rc={rc}):{stderr[:120]}"
    macs = [ln.strip().lower() for ln in stdout.splitlines() if _is_mac(ln.strip())]
    if not macs:
        return [], ""
    return sorted(set(macs)), ""


def _hostapd_cli_ping(ctrl_dir: str, ap_if: str) -> Tuple[bool, str]:
    binpath = _hostapd_cli_path()
    if not binpath:
        return False, "hostapd_cli_not_found"

    rc, stdout, stderr = _run([binpath, "-p", ctrl_dir, "-i", ap_if, "ping"], timeout_s=0.3)
    if rc != 0:
        reason = stderr.strip() if stderr else ("timeout" if rc == 124 else "")
        return False, f"hostapd_cli_ping_failed(rc={rc}):{reason[:120]}"

    if "PONG" not in stdout.upper():
        return False, "hostapd_cli_ping_failed:unexpected_response"

    return True, ""


def _iw_station_dump(ap_if: str) -> Tuple[Optional[List[Client]], str]:
    """
    Parse `iw dev <ap_if> station dump`.
    """
    rc, stdout, stderr = _run(["iw", "dev", ap_if, "station", "dump"], timeout_s=1.2)
    if rc != 0:
        return None, f"iw_station_dump_failed(rc={rc}):{stderr[:120]}"
    if not stdout.strip():
        time.sleep(0.2)
        rc, stdout, stderr = _run(["iw", "dev", ap_if, "station", "dump"], timeout_s=1.2)
        if rc != 0:
            return None, f"iw_station_dump_failed(rc={rc}):{stderr[:120]}"
        if not stdout.strip():
            return [], ""

    clients: List[Client] = []
    # Blocks start with: Station <MAC> (on <ifname>)
    cur: Dict[str, Any] = {}
    cur_mac: Optional[str] = None

    def flush():
        nonlocal cur, cur_mac
        if cur_mac and _is_mac(cur_mac):
            clients.append(
                Client(
                    mac=cur_mac.lower(),
                    authorized=cur.get("authorized"),
                    authenticated=cur.get("authenticated"),
                    associated=cur.get("associated"),
                    signal_dbm=cur.get("signal_dbm"),
                    signal_avg_dbm=cur.get("signal_avg_dbm"),
                    tx_bitrate_mbps=cur.get("tx_bitrate_mbps"),
                    rx_bitrate_mbps=cur.get("rx_bitrate_mbps"),
                    inactive_ms=cur.get("inactive_ms"),
                    connected_time_s=cur.get("connected_time_s"),
                    tx_retries=cur.get("tx_retries"),
                    tx_failed=cur.get("tx_failed"),
                    tx_packets=cur.get("tx_packets"),
                    rx_packets=cur.get("rx_packets"),
                    tx_bytes=cur.get("tx_bytes"),
                    rx_bytes=cur.get("rx_bytes"),
                    source="iw",
                )
            )
        cur = {}
        cur_mac = None

    for line in stdout.splitlines():
        line = line.rstrip()
        if line.startswith("Station "):
            flush()
            parts = line.split()
            if len(parts) >= 2 and _is_mac(parts[1]):
                cur_mac = parts[1]
            continue

        # Example lines:
        #   inactive time:  40 ms
        #   signal:         -44 dBm
        #   signal avg:     -45 dBm
        #   tx bitrate:     600.0 MBit/s
        #   rx bitrate:     433.3 MBit/s
        #   authorized:     yes
        #   authenticated: yes
        #   associated:    yes
        #   connected time: 123 seconds
        #   tx retries:    0
        #   tx failed:     0
        #   tx packets:    1234
        #   rx packets:    5678
        #   tx bytes:      999
        #   rx bytes:      888
        s = line.strip().lower()
        if s.startswith("inactive time:"):
            m = re.search(r"(\d+)\s*ms", s)
            if m:
                cur["inactive_ms"] = int(m.group(1))
        elif s.startswith("signal avg:"):
            m = re.match(r"^signal avg:\s*(-?\d+)", s)
            if m:
                cur["signal_avg_dbm"] = int(m.group(1))
        elif s.startswith("signal:"):
            m = re.match(r"^signal:\s*(-?\d+)", s)
            if m:
                cur["signal_dbm"] = int(m.group(1))
        elif s.startswith("tx bitrate:"):
            m = re.search(r"([\d.]+)\s*mbit/s", s)
            if m:
                cur["tx_bitrate_mbps"] = float(m.group(1))
        elif s.startswith("rx bitrate:"):
            m = re.search(r"([\d.]+)\s*mbit/s", s)
            if m:
                cur["rx_bitrate_mbps"] = float(m.group(1))
        elif s.startswith("authorized:"):
            cur["authorized"] = "yes" in s
        elif s.startswith("authenticated:"):
            cur["authenticated"] = "yes" in s
        elif s.startswith("associated:"):
            cur["associated"] = "yes" in s
        elif s.startswith("connected time:"):
            m = re.search(r"(\d+)\s*seconds", s)
            if m:
                cur["connected_time_s"] = int(m.group(1))
        elif s.startswith("tx retries:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["tx_retries"] = int(m.group(1))
        elif s.startswith("tx failed:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["tx_failed"] = int(m.group(1))
        elif s.startswith("tx packets:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["tx_packets"] = int(m.group(1))
        elif s.startswith("rx packets:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["rx_packets"] = int(m.group(1))
        elif s.startswith("tx bytes:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["tx_bytes"] = int(m.group(1))
        elif s.startswith("rx bytes:"):
            m = re.search(r"(\d+)", s)
            if m:
                cur["rx_bytes"] = int(m.group(1))

    flush()
    return clients, ""


def _append_debug_warnings(
    warnings: List[str],
    conf_dir: Optional[Path],
    ap_interface: Optional[str],
    iw_ap_ifaces: List[str],
) -> None:
    warnings.append(f"selected_conf_dir={str(conf_dir) if conf_dir else None}")
    warnings.append(f"selected_ap_interface={ap_interface}")
    if iw_ap_ifaces and ap_interface and ap_interface not in iw_ap_ifaces:
        warnings.append(f"iw_ap_ifaces={','.join(iw_ap_ifaces)}")


def _warn_hostapd_cli_unreliable(warnings: List[str]) -> None:
    if "hostapd_cli_unreliable" not in warnings:
        warnings.append("hostapd_cli_unreliable")


def get_clients_snapshot(
    adapter_ifname: Optional[str] = None,
    ap_interface_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a dict:
      {
        "conf_dir": "...",
        "ap_interface": "x0wlan1",
        "clients": [ ... ],
        "warnings": [ ... ],
        "sources": { "primary": "...", "enrichment": ["..."] }
      }
    Never raises.
    """
    warnings: List[str] = []
    ap_if, iw_ap_ifaces, iw_warns = _select_ap_interface(adapter_ifname, ap_interface_hint=ap_interface_hint)
    warnings.extend(iw_warns)

    if not ap_if:
        warnings.append("no_active_ap_interface")
        return {
            "conf_dir": None,
            "ap_interface": None,
            "clients": [],
            "warnings": warnings,
            "sources": {"primary": None, "enrichment": []},
        }

    conf_dir = _select_conf_dir(adapter_ifname, ap_if)
    if conf_dir is None:
        warnings.append("conf_dir_unavailable")

    ctrl_dir = _find_ctrl_dir(conf_dir, ap_if)
    if ctrl_dir is None:
        warnings.append("hostapd_ctrl_socket_missing")

    if iw_ap_ifaces and ap_if not in iw_ap_ifaces:
        warnings.append("iw_ap_interface_mismatch")

    leases: Dict[str, Tuple[str, Optional[str]]] = _dnsmasq_leases(conf_dir) if conf_dir else {}
    mac_to_ip = _ip_neigh(ap_if)

    clients: List[Client] = []
    primary = None

    iw_clients, warn = _iw_station_dump(ap_if)
    if warn and "no such device" in warn.lower():
        retry_ap_if, retry_ap_ifaces, retry_warns = _select_ap_interface(
            adapter_ifname,
            ap_interface_hint=ap_interface_hint,
        )
        warnings.extend(retry_warns)
        if not retry_ap_if:
            warnings.append("no_active_ap_interface")
            return {
                "conf_dir": None,
                "ap_interface": None,
                "clients": [],
                "warnings": warnings,
                "sources": {"primary": None, "enrichment": []},
            }
        if retry_ap_if != ap_if:
            ap_if = retry_ap_if
            iw_ap_ifaces = retry_ap_ifaces
            conf_dir = _select_conf_dir(adapter_ifname, ap_if)
            if conf_dir is None and "conf_dir_unavailable" not in warnings:
                warnings.append("conf_dir_unavailable")
            ctrl_dir = _find_ctrl_dir(conf_dir, ap_if)
            if ctrl_dir is None and "hostapd_ctrl_socket_missing" not in warnings:
                warnings.append("hostapd_ctrl_socket_missing")
            leases = _dnsmasq_leases(conf_dir) if conf_dir else {}
            mac_to_ip = _ip_neigh(ap_if)
        iw_clients, warn = _iw_station_dump(ap_if)
    if warn:
        warnings.append(warn)
    if iw_clients is not None:
        primary = "iw"
        clients = iw_clients
        if len(iw_clients) == 0:
            warnings.append("no_connected_stations")

    attempt_hostapd_cli = iw_clients is None or (iw_clients is not None and len(iw_clients) > 0)
    allow_hostapd_results = iw_clients is None

    # Secondary attempt: hostapd_cli list_sta, only if socket exists and ping succeeds.
    hostapd_cli_unreliable = False
    if ctrl_dir and attempt_hostapd_cli:
        ping_ok, _ping_warn = _hostapd_cli_ping(str(ctrl_dir), ap_if)
        if not ping_ok:
            hostapd_cli_unreliable = True
            _warn_hostapd_cli_unreliable(warnings)

    if ctrl_dir and attempt_hostapd_cli and not hostapd_cli_unreliable:
        macs, warn = _hostapd_cli_list_stas(str(ctrl_dir), ap_if)
        if warn:
            hostapd_cli_unreliable = True
            _warn_hostapd_cli_unreliable(warnings)
        if macs is not None and allow_hostapd_results and not hostapd_cli_unreliable:
            primary = "hostapd_cli"
            for mac in macs:
                ip = mac_to_ip.get(mac) or (leases.get(mac)[0] if mac in leases else None)
                hn = leases.get(mac)[1] if mac in leases else None
                clients.append(Client(mac=mac, ip=ip, hostname=hn, source="hostapd_cli"))

    # Enrich any clients we have with IP/hostname from neigh/leases
    by_mac: Dict[str, Client] = {c.mac.lower(): c for c in clients if _is_mac(c.mac)}
    for mac, c in list(by_mac.items()):
        ip = c.ip or mac_to_ip.get(mac) or (leases.get(mac)[0] if mac in leases else None)
        hn = c.hostname or (leases.get(mac)[1] if mac in leases else None)
        by_mac[mac] = Client(
            mac=c.mac,
            ip=ip,
            hostname=hn,
            authorized=c.authorized,
            authenticated=c.authenticated,
            associated=c.associated,
            signal_dbm=c.signal_dbm,
            signal_avg_dbm=c.signal_avg_dbm,
            tx_bitrate_mbps=c.tx_bitrate_mbps,
            rx_bitrate_mbps=c.rx_bitrate_mbps,
            inactive_ms=c.inactive_ms,
            connected_time_s=c.connected_time_s,
            tx_retries=c.tx_retries,
            tx_failed=c.tx_failed,
            tx_packets=c.tx_packets,
            rx_packets=c.rx_packets,
            tx_bytes=c.tx_bytes,
            rx_bytes=c.rx_bytes,
            source=c.source,
        )

    # Sort stable output
    out_clients = [asdict(by_mac[k]) for k in sorted(by_mac.keys())]

    enrichment: List[str] = []
    if leases:
        enrichment.append("dnsmasq_leases")
    if mac_to_ip:
        enrichment.append("ip_neigh")

    if warnings:
        _append_debug_warnings(warnings, conf_dir, ap_if, iw_ap_ifaces)

    return {
        "conf_dir": str(conf_dir) if conf_dir else None,
        "ap_interface": ap_if,
        "clients": out_clients,
        "warnings": warnings,
        "sources": {"primary": primary, "enrichment": enrichment},
    }


def list_clients(ap_ifname: str) -> List[dict]:
    if not ap_ifname:
        return []

    try:
        cfg = load_config()
        lease_file = _find_leases_file(cfg) if isinstance(cfg, dict) else None
        leases = _parse_leases(lease_file) if lease_file else {}

        proc = subprocess.run(
            ["ip", "neigh", "show", "dev", ap_ifname],
            capture_output=True,
            text=True,
            timeout=0.8,
        )
        if proc.returncode != 0 and not proc.stdout:
            return []

        out: List[dict] = []
        for entry in _parse_ip_neigh(proc.stdout or ""):
            dev = entry.get("dev")
            if dev and dev != ap_ifname:
                continue
            mac = entry.get("mac")
            item = {"ip": entry.get("ip"), "mac": mac, "state": entry.get("state")}
            if mac and mac in leases:
                item["hostname"] = leases[mac]
            out.append(item)

        return out
    except Exception:
        return []
