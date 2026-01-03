from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd.config import load_config


LNXROUTER_TMP = Path("/dev/shm/lnxrouter_tmp")


@dataclass(frozen=True)
class Client:
    mac: str
    ip: Optional[str] = None
    hostname: Optional[str] = None
    signal_dbm: Optional[int] = None
    tx_bitrate_mbps: Optional[float] = None
    rx_bitrate_mbps: Optional[float] = None
    inactive_ms: Optional[int] = None
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
_CTRL_DIR_RE = re.compile(r"DIR=([^\s]+)")


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
    # fall back to PATH
    rc, _, _ = _run(["sh", "-lc", "command -v hostapd_cli"], timeout_s=0.6)
    if rc == 0:
        return "hostapd_cli"
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


def _iw_dev_ap_ifaces() -> Tuple[List[Dict[str, Optional[str]]], str]:
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

    ap_ifaces = [
        i for i in interfaces if (i.get("type") or "").upper().startswith("AP") and i.get("ifname")
    ]
    return ap_ifaces, ""


def _matches_ap_adapter(ifname: str, ap_adapter: Optional[str]) -> bool:
    if not ap_adapter:
        return False
    if ifname == ap_adapter:
        return True
    return ifname.startswith("x") and ifname.endswith(ap_adapter)


def _select_ap_interface(
    adapter_ifname: Optional[str],
) -> Tuple[Optional[str], List[str], List[str]]:
    warnings: List[str] = []
    ap_ifaces, warn = _iw_dev_ap_ifaces()
    if warn:
        warnings.append(warn)
    ap_ifnames = [i.get("ifname") for i in ap_ifaces if i.get("ifname")]

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
    if not LNXROUTER_TMP.exists():
        return []

    if adapter_ifname:
        pats = [f"lnxrouter.{adapter_ifname}.conf.*"]
    else:
        pats = ["lnxrouter.*.conf.*"]

    candidates: List[Path] = []
    for pat in pats:
        candidates.extend([p for p in LNXROUTER_TMP.glob(pat) if p.is_dir()])
    return candidates


def _find_latest_conf_dir(adapter_ifname: Optional[str]) -> Optional[Path]:
    candidates = _candidate_conf_dirs(adapter_ifname)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _select_conf_dir(
    adapter_ifname: Optional[str],
    ap_interface: Optional[str],
) -> Tuple[Optional[Path], bool]:
    adapter_for_glob = _derive_adapter_from_ap(adapter_ifname) if adapter_ifname else None
    if not adapter_for_glob and ap_interface:
        adapter_for_glob = _derive_adapter_from_ap(ap_interface)
    candidates = _candidate_conf_dirs(adapter_for_glob)
    if not candidates:
        return None, False

    if ap_interface:
        matches: List[Path] = []
        for cand in candidates:
            if _read_hostapd_conf_interface(cand) == ap_interface:
                matches.append(cand)
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0], False

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0], True


def _parse_kv_file(path: Path) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    if not path.exists():
        return kv
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()
    return kv


def _read_subn_iface(conf_dir: Path) -> Optional[str]:
    path = conf_dir / "subn_iface"
    if not path.exists():
        return None
    raw = path.read_text(errors="ignore").strip()
    if not raw:
        return None
    return raw.splitlines()[0].strip() or None


def _read_dnsmasq_conf_interface(conf_dir: Path) -> Optional[str]:
    path = conf_dir / "dnsmasq.conf"
    if not path.exists():
        return None
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("interface="):
            value = line.split("=", 1)[1].strip()
            if not value:
                continue
            return value.split(",", 1)[0].strip() or None
    return None


def _read_hostapd_conf_interface(conf_dir: Path) -> Optional[str]:
    hostapd_conf = conf_dir / "hostapd.conf"
    kv = _parse_kv_file(hostapd_conf)
    ap_if = kv.get("interface")
    return ap_if.strip() if ap_if else None


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


def _ctrl_dir_from_conf(conf_dir: Path) -> Optional[Path]:
    hostapd_conf = conf_dir / "hostapd.conf"
    kv = _parse_kv_file(hostapd_conf)
    ctrl_value = kv.get("ctrl_interface")
    ctrl_dir = _parse_ctrl_interface_dir(ctrl_value)
    return Path(ctrl_dir) if ctrl_dir else None


def _find_ctrl_dir(conf_dir: Optional[Path], ap_interface: str) -> Optional[Path]:
    candidates: List[Path] = []
    if conf_dir:
        ctrl_dir = _ctrl_dir_from_conf(conf_dir)
        if ctrl_dir:
            candidates.append(ctrl_dir)

    candidates.extend([Path("/run/hostapd"), Path("/var/run/hostapd")])

    for cand in candidates:
        if (cand / ap_interface).exists():
            return cand
    return None


def _ap_interface_from_conf_dir(conf_dir: Path) -> Optional[str]:
    for reader in (_read_subn_iface, _read_dnsmasq_conf_interface, _read_hostapd_conf_interface):
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
        # try alternative command names if list_sta unavailable
        cmd2 = [binpath, "-p", ctrl_dir, "-i", ap_if, "all_sta"]
        rc2, stdout2, stderr2 = _run(cmd2, timeout_s=0.8)
        if rc2 != 0:
            return None, f"hostapd_cli_failed(rc={rc}|{rc2}):{(stderr or stderr2)[:120]}"
        # all_sta prints blocks; extract MACs
        macs: List[str] = []
        for line in stdout2.splitlines():
            line = line.strip()
            if _is_mac(line.split()[0]):
                macs.append(line.split()[0].lower())
        return sorted(set(macs)), ""
    macs = [ln.strip().lower() for ln in stdout.splitlines() if _is_mac(ln.strip())]
    if not macs:
        return [], ""
    return sorted(set(macs)), ""


def _iw_station_dump(ap_if: str) -> Tuple[Optional[List[Client]], str]:
    """
    Parse `iw dev <ap_if> station dump`.
    """
    rc, stdout, stderr = _run(["iw", "dev", ap_if, "station", "dump"], timeout_s=1.2)
    if rc != 0:
        return None, f"iw_station_dump_failed(rc={rc}):{stderr[:120]}"

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
                    signal_dbm=cur.get("signal_dbm"),
                    tx_bitrate_mbps=cur.get("tx_bitrate_mbps"),
                    rx_bitrate_mbps=cur.get("rx_bitrate_mbps"),
                    inactive_ms=cur.get("inactive_ms"),
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
        #   tx bitrate:     600.0 MBit/s
        #   rx bitrate:     433.3 MBit/s
        s = line.strip().lower()
        if s.startswith("inactive time:"):
            m = re.search(r"(\d+)\s*ms", s)
            if m:
                cur["inactive_ms"] = int(m.group(1))
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+)\s*dbm", s)
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


def get_clients_snapshot(adapter_ifname: Optional[str] = None) -> Dict[str, Any]:
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
    iw_ap_ifaces: List[str] = []
    ap_if: Optional[str] = None
    conf_dir: Optional[Path] = None
    conf_best_effort = False

    for attempt in range(3):
        warnings = []
        ap_if, iw_ap_ifaces, iw_warns = _select_ap_interface(adapter_ifname)
        warnings.extend(iw_warns)

        conf_dir, conf_best_effort = _select_conf_dir(adapter_ifname, ap_if)

        if ap_if is None and conf_dir is not None:
            ap_if = _ap_interface_from_conf_dir(conf_dir)
            if ap_if:
                matched_conf_dir, matched_best_effort = _select_conf_dir(adapter_ifname, ap_if)
                if matched_conf_dir is not None:
                    conf_dir = matched_conf_dir
                    conf_best_effort = matched_best_effort

        if ap_if and conf_dir:
            break

        if attempt < 2 and (ap_if is None or conf_dir is None):
            time.sleep(0.1)

    ctrl_dir: Optional[Path] = _find_ctrl_dir(conf_dir, ap_if) if ap_if else None

    leases: Dict[str, Tuple[str, Optional[str]]] = {}
    if conf_dir is not None:
        leases = _dnsmasq_leases(conf_dir)

    mac_to_ip: Dict[str, str] = {}
    if ap_if:
        mac_to_ip = _ip_neigh(ap_if)

    clients: List[Client] = []
    primary = None

    # Primary attempt: hostapd_cli list_sta (fast + authoritative), but do not trust it to be stable.
    if ap_if and ctrl_dir:
        macs, warn = _hostapd_cli_list_stas(str(ctrl_dir), ap_if)
        if warn:
            warnings.append(warn)
        if macs is not None:
            primary = "hostapd_cli"
            for mac in macs:
                ip = mac_to_ip.get(mac) or (leases.get(mac)[0] if mac in leases else None)
                hn = leases.get(mac)[1] if mac in leases else None
                clients.append(Client(mac=mac, ip=ip, hostname=hn, source="hostapd_cli"))

    # Fallback: iw station dump
    if primary is None:
        if not ap_if:
            warnings.append("no_ap_interface_for_iw_fallback")
        else:
            iw_clients, warn = _iw_station_dump(ap_if)
            if warn and "no such device" in warn.lower():
                retry_ap_if, retry_ap_ifaces, retry_warns = _select_ap_interface(adapter_ifname)
                warnings.extend(retry_warns)
                if retry_ap_if and retry_ap_if != ap_if:
                    ap_if = retry_ap_if
                    iw_ap_ifaces = retry_ap_ifaces or iw_ap_ifaces
                    mac_to_ip = _ip_neigh(ap_if)
                    retry_conf_dir, retry_best_effort = _select_conf_dir(adapter_ifname, ap_if)
                    if retry_conf_dir is not None:
                        conf_dir = retry_conf_dir
                        conf_best_effort = retry_best_effort
                        leases = _dnsmasq_leases(conf_dir)
                    iw_clients, warn = _iw_station_dump(ap_if)
            if warn:
                warnings.append(warn)
        if iw_clients is not None:
            primary = "iw"
            clients = iw_clients

    if ap_if:
        ctrl_dir = _find_ctrl_dir(conf_dir, ap_if)

    if conf_dir is None:
        warnings.append("lnxrouter_conf_dir_not_found")
    elif conf_best_effort:
        warnings.append("conf_dir_best_effort")

    if ap_if is None:
        warnings.append("no_ap_interface_detected")
    elif ctrl_dir is None:
        warnings.append("hostapd_ctrl_socket_missing")

    if ap_if and iw_ap_ifaces and ap_if not in iw_ap_ifaces:
        warnings.append("iw_ap_interface_mismatch")

    # Enrich any clients we have with IP/hostname from neigh/leases
    by_mac: Dict[str, Client] = {c.mac.lower(): c for c in clients if _is_mac(c.mac)}
    for mac, c in list(by_mac.items()):
        ip = c.ip or mac_to_ip.get(mac) or (leases.get(mac)[0] if mac in leases else None)
        hn = c.hostname or (leases.get(mac)[1] if mac in leases else None)
        by_mac[mac] = Client(
            mac=c.mac,
            ip=ip,
            hostname=hn,
            signal_dbm=c.signal_dbm,
            tx_bitrate_mbps=c.tx_bitrate_mbps,
            rx_bitrate_mbps=c.rx_bitrate_mbps,
            inactive_ms=c.inactive_ms,
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
