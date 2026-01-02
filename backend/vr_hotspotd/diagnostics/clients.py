import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from vr_hotspotd.config import load_config

_KNOWN_STATES = {
    "INCOMPLETE",
    "REACHABLE",
    "STALE",
    "DELAY",
    "PROBE",
    "FAILED",
    "NOARP",
    "PERMANENT",
}


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


def _parse_neighbors(text: str, leases: Dict[str, str]) -> List[dict]:
    clients: List[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue

        ip = parts[0]
        mac = None
        state = None

        if "lladdr" in parts:
            idx = parts.index("lladdr")
            if idx + 1 < len(parts):
                mac = parts[idx + 1]

        for tok in reversed(parts):
            if tok in _KNOWN_STATES:
                state = tok
                break

        entry = {"ip": ip, "mac": mac, "state": state}
        if mac:
            hostname = leases.get(mac.lower())
            if hostname:
                entry["hostname"] = hostname

        clients.append(entry)

    return clients


def list_clients(ap_ifname: str) -> List[dict]:
    if not ap_ifname:
        return []

    try:
        cfg = load_config()
        lease_file = _find_leases_file(cfg)
        leases = _parse_leases(lease_file) if lease_file else {}

        proc = subprocess.run(
            ["ip", "neigh", "show", "dev", ap_ifname],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 and not proc.stdout:
            return []
        return _parse_neighbors(proc.stdout or "", leases)
    except Exception:
        return []
