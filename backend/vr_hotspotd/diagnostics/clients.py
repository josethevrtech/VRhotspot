from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple



# Back-compat for older tests/consumers that monkeypatch vr_hotspotd.diagnostics.clients.load_config
try:
    from vr_hotspotd.config import load_config as load_config
except Exception:  # pragma: no cover
    def load_config():
        return {}

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


def _find_latest_conf_dir(adapter_ifname: Optional[str]) -> Optional[Path]:
    """
    lnxrouter uses: /dev/shm/lnxrouter_tmp/lnxrouter.<ifname>.conf.<RAND>
    Prefer matching adapter if provided, else any newest.
    """
    if not LNXROUTER_TMP.exists():
        return None

    pats: List[str]
    if adapter_ifname:
        pats = [f"lnxrouter.{adapter_ifname}.conf.*"]
    else:
        pats = ["lnxrouter.*.conf.*"]

    candidates: List[Path] = []
    for pat in pats:
        candidates.extend([p for p in LNXROUTER_TMP.glob(pat) if p.is_dir()])

    if not candidates:
        return None

    # newest mtime wins
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


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


def _read_hostapd_runtime(conf_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (ap_ifname, ctrl_dir).
    """
    hostapd_conf = conf_dir / "hostapd.conf"
    kv = _parse_kv_file(hostapd_conf)
    ap_if = kv.get("interface")
    ctrl_dir = kv.get("ctrl_interface")
    return ap_if, ctrl_dir


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


def _ip_neigh(ap_if: str) -> Dict[str, str]:
    """
    Returns mac -> ip from `ip neigh show dev <ap_if>`.
    """
    rc, stdout, _ = _run(["ip", "neigh", "show", "dev", ap_if], timeout_s=0.8)
    if rc != 0:
        return {}
    mapping: Dict[str, str] = {}
    # Example: 192.168.120.217 lladdr 76:d4:ff:3c:12:8d REACHABLE
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        ip = parts[0]
        if "lladdr" in parts:
            idx = parts.index("lladdr")
            if idx + 1 < len(parts):
                mac = parts[idx + 1].lower()
                if _is_mac(mac):
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

    conf_dir = _find_latest_conf_dir(adapter_ifname)
    if not conf_dir:
        return {
            "conf_dir": None,
            "ap_interface": None,
            "clients": [],
            "warnings": ["lnxrouter_conf_dir_not_found"],
            "sources": {"primary": None, "enrichment": []},
        }

    ap_if, ctrl_dir = _read_hostapd_runtime(conf_dir)
    if not ap_if:
        warnings.append("hostapd_conf_missing_interface")

    leases = _dnsmasq_leases(conf_dir)  # mac -> (ip, hostname)
    neigh: Dict[str, str] = {}
    if ap_if:
        neigh = _ip_neigh(ap_if)

    clients: List[Client] = []
    primary = None

    # Primary attempt: hostapd_cli list_sta (fast + authoritative), but do not trust it to be stable.
    if ap_if and ctrl_dir:
        macs, warn = _hostapd_cli_list_stas(ctrl_dir, ap_if)
        if warn:
            warnings.append(warn)
        if macs is not None:
            primary = "hostapd_cli"
            for mac in macs:
                ip = neigh.get(mac) or (leases.get(mac)[0] if mac in leases else None)
                hn = leases.get(mac)[1] if mac in leases else None
                clients.append(Client(mac=mac, ip=ip, hostname=hn, source="hostapd_cli"))

    # Fallback: iw station dump
    if primary is None:
        if not ap_if:
            warnings.append("no_ap_interface_for_iw_fallback")
        else:
            iw_clients, warn = _iw_station_dump(ap_if)
            if warn:
                warnings.append(warn)
            if iw_clients is not None:
                primary = "iw"
                clients = iw_clients

    # Enrich any clients we have with IP/hostname from neigh/leases
    by_mac: Dict[str, Client] = {c.mac.lower(): c for c in clients if _is_mac(c.mac)}
    for mac, c in list(by_mac.items()):
        ip = c.ip or neigh.get(mac) or (leases.get(mac)[0] if mac in leases else None)
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
    if neigh:
        enrichment.append("ip_neigh")

    return {
        "conf_dir": str(conf_dir),
        "ap_interface": ap_if,
        "clients": out_clients,
        "warnings": warnings,
        "sources": {"primary": primary, "enrichment": enrichment},
    }
