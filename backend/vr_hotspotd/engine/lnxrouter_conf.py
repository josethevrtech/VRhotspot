import re
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_LNXROUTER_TMP = Path("/dev/shm/lnxrouter_tmp")
_CTRL_DIR_RE = re.compile(r"DIR=([^\s]+)")


def candidate_conf_dirs(adapter_ifname: Optional[str], tmp_dir: Optional[Path] = None) -> List[Path]:
    base = tmp_dir or DEFAULT_LNXROUTER_TMP
    if not base.exists():
        return []
    patterns = [f"lnxrouter.{adapter_ifname}.conf.*"] if adapter_ifname else ["lnxrouter.*.conf.*"]
    candidates: List[Path] = []
    for pat in patterns:
        candidates.extend([p for p in base.glob(pat) if p.is_dir()])
    return candidates


def parse_kv_file(path: Path) -> Dict[str, str]:
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


def read_subn_iface(conf_dir: Path) -> Optional[str]:
    path = conf_dir / "subn_iface"
    if not path.exists():
        return None
    raw = path.read_text(errors="ignore").strip()
    if not raw:
        return None
    return raw.splitlines()[0].strip() or None


def read_hostapd_conf_interface(conf_dir: Path) -> Optional[str]:
    hostapd_conf = conf_dir / "hostapd.conf"
    kv = parse_kv_file(hostapd_conf)
    ap_if = kv.get("interface")
    return ap_if.strip() if ap_if else None


def read_dnsmasq_conf_interface(conf_dir: Path) -> Optional[str]:
    dnsmasq_conf = conf_dir / "dnsmasq.conf"
    if not dnsmasq_conf.exists():
        return None
    for line in dnsmasq_conf.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("interface="):
            value = line.split("=", 1)[1].strip()
            if not value:
                continue
            return value.split(",", 1)[0].strip() or None
    return None


def conf_dir_matches_ap(conf_dir: Path, ap_interface: str) -> bool:
    if read_hostapd_conf_interface(conf_dir) == ap_interface:
        return True
    if read_dnsmasq_conf_interface(conf_dir) == ap_interface:
        return True
    return read_subn_iface(conf_dir) == ap_interface


def find_latest_conf_dir(
    adapter_ifname: Optional[str],
    ap_interface: Optional[str] = None,
    tmp_dir: Optional[Path] = None,
) -> Optional[Path]:
    candidates = candidate_conf_dirs(adapter_ifname, tmp_dir=tmp_dir)
    if not candidates:
        return None
    if ap_interface:
        matches = [c for c in candidates if conf_dir_matches_ap(c, ap_interface)]
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


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


def ctrl_dir_from_conf(conf_dir: Path) -> Optional[Path]:
    hostapd_conf = conf_dir / "hostapd.conf"
    kv = parse_kv_file(hostapd_conf)
    ctrl_value = kv.get("ctrl_interface")
    ctrl_dir = _parse_ctrl_interface_dir(ctrl_value)
    return Path(ctrl_dir) if ctrl_dir else None


def find_ctrl_dir(
    conf_dir: Optional[Path],
    ap_interface: str,
    extra_candidates: Optional[List[Path]] = None,
) -> Optional[Path]:
    candidates: List[Path] = []
    if conf_dir:
        ctrl_dir = ctrl_dir_from_conf(conf_dir)
        if ctrl_dir:
            candidates.append(ctrl_dir)

    candidates.extend([Path("/run/hostapd"), Path("/var/run/hostapd")])
    if extra_candidates:
        candidates.extend(extra_candidates)

    for cand in candidates:
        if (cand / ap_interface).exists():
            return cand
    return None


def read_pid_file(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    try:
        raw = path.read_text(errors="ignore").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return int(raw.split()[0])
    except Exception:
        return None


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    return Path(f"/proc/{pid}").exists()
