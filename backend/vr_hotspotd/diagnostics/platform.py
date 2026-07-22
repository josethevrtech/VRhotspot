"""
Platform capability matrix for cross-distro compatibility detection.

Provides a quick, safe probe of platform characteristics for the WebUI
and diagnostic purposes. All probes are best-effort and fail gracefully.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from vr_hotspotd import host_probes
from vr_hotspotd.host_facts import HostFactsSnapshot

try:
    from vr_hotspotd import os_release as os_release_mod
except Exception:
    os_release_mod = None


def _run_cmd(cmd: List[str], timeout_s: float = 0.5) -> tuple[int, str]:
    """Run a command with timeout, returning (exit_code, stdout). Never raises."""
    result = host_probes.run_command(cmd, timeout_s=timeout_s)
    if result.timed_out or result.error:
        return 127, ""
    return result.exit_status, result.stdout.strip()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_os_release_text(text: str) -> Dict[str, str]:
    if os_release_mod and hasattr(os_release_mod, "parse_os_release"):
        return os_release_mod.parse_os_release(text)
    data: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = _strip_quotes(value)
        if key:
            data[key] = value
    return data


def _read_os_release() -> Dict[str, str]:
    if os_release_mod and hasattr(os_release_mod, "read_os_release"):
        try:
            info = os_release_mod.read_os_release()
            if info:
                return info
        except Exception:
            pass
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except Exception:
        return {}
    return _parse_os_release_text(text)


def _split_like(value: Optional[Any]) -> List[str]:
    return host_probes.split_tokens(value)


def _snapshot_probe_failed(snapshot: HostFactsSnapshot, probe_id: str) -> bool:
    return any(error.probe_id == probe_id for error in snapshot.probe_errors)


def _probe_os(
    host_facts_snapshot: Optional[HostFactsSnapshot] = None,
) -> Dict[str, Any]:
    if host_facts_snapshot is not None and not _snapshot_probe_failed(
        host_facts_snapshot,
        "platform.os_release",
    ):
        facts = host_facts_snapshot.platform
        return {
            "pretty_name": facts.os_name or "",
            "id": facts.os_id or "",
            "version_id": facts.version_id or "",
            "variant_id": facts.variant_id or "",
            "id_like": list(facts.id_like),
        }

    info = _read_os_release()
    return {
        "pretty_name": info.get("pretty_name") or info.get("name") or "",
        "id": info.get("id") or "",
        "version_id": info.get("version_id") or "",
        "variant_id": info.get("variant_id") or "",
        "id_like": _split_like(info.get("id_like")),
    }


def _systemctl_is_active(service: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    rc, _ = _run_cmd(["systemctl", "is-active", service], timeout_s=0.3)
    return rc == 0


def _path_is_writable(path: str) -> bool:
    path_obj = Path(path)
    try:
        if path_obj.is_dir():
            return os.access(path_obj, os.W_OK)
        parent = path_obj.parent
        if parent == path_obj:
            return False
        return parent.is_dir() and os.access(parent, os.W_OK)
    except Exception:
        return False


def _parse_mount_line(line: str) -> Optional[List[str]]:
    if " on " in line and " type " in line:
        _, rest = line.split(" on ", 1)
        target, _, tail = rest.partition(" type ")
        if target.strip() != "/":
            return None
        if "(" in tail and ")" in tail:
            opts = tail.split("(", 1)[1].split(")", 1)[0]
            return [opt.strip() for opt in opts.split(",") if opt.strip()]
        return []
    parts = line.split()
    if len(parts) >= 4 and parts[1] == "/":
        return [opt.strip() for opt in parts[3].split(",") if opt.strip()]
    return None


def _root_mount_is_ro() -> bool:
    if not shutil.which("mount"):
        return False
    rc, out = _run_cmd(["mount"], timeout_s=0.3)
    if rc != 0:
        return False
    for line in out.splitlines():
        opts = _parse_mount_line(line)
        if opts is None:
            continue
        return "ro" in opts
    return False


def _probe_immutability() -> Dict[str, Any]:
    signal = "unknown"
    is_immutable = False

    if shutil.which("rpm-ostree"):
        signal = "rpm-ostree"
        is_immutable = True
    elif shutil.which("steamos-readonly"):
        rc, out = _run_cmd(["steamos-readonly", "status"], timeout_s=0.3)
        if rc == 0 and "enabled" in out.lower():
            signal = "steamos-readonly"
            is_immutable = True
    elif _root_mount_is_ro():
        signal = "mount-ro"
        is_immutable = True

    writable_paths = {
        "/var": _path_is_writable("/var"),
        "/var/lib": _path_is_writable("/var/lib"),
        "/var/lib/vr-hotspot": _path_is_writable("/var/lib/vr-hotspot"),
    }

    return {
        "is_immutable": is_immutable,
        "signal": signal,
        "writable_paths": writable_paths,
    }


def _probe_session() -> Dict[str, Any]:
    return {
        "wayland": bool(os.environ.get("WAYLAND_DISPLAY")),
        "x11": bool(os.environ.get("DISPLAY")),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "",
    }


def _probe_integration(
    host_facts_snapshot: Optional[HostFactsSnapshot] = None,
) -> Dict[str, Any]:
    systemctl_present = shutil.which("systemctl") is not None
    systemd = {
        "present": systemctl_present,
        "active": _systemctl_is_active("systemd-journald") if systemctl_present else False,
    }

    snapshot_tools_complete = host_facts_snapshot is not None and not any(
        _snapshot_probe_failed(host_facts_snapshot, f"tool.{name}")
        for name in (
            "nmcli",
            "NetworkManager",
            "firewall-cmd",
            "ufw",
            "nft",
            "iptables",
        )
    )
    if not snapshot_tools_complete:
        nmcli = shutil.which("nmcli") is not None
        network_manager = {
            "present": nmcli or shutil.which("NetworkManager") is not None,
            "active": _systemctl_is_active("NetworkManager") if systemctl_present else False,
            "nmcli": nmcli,
        }

        firewall = {
            "firewalld": {
                "present": shutil.which("firewall-cmd") is not None,
                "active": _systemctl_is_active("firewalld") if systemctl_present else False,
            },
            "ufw": {
                "present": shutil.which("ufw") is not None,
                "active": _systemctl_is_active("ufw") if systemctl_present else False,
            },
            "nft": {"present": shutil.which("nft") is not None},
            "iptables": {"present": shutil.which("iptables") is not None},
        }
    else:
        nm_facts = host_facts_snapshot.network_manager
        nm_active = nm_facts.service_active is True
        if _snapshot_probe_failed(
            host_facts_snapshot,
            "network_manager.service",
        ):
            nm_active = (
                _systemctl_is_active("NetworkManager") if systemctl_present else False
            )
        network_manager = {
            "present": bool(nm_facts.nmcli_present or nm_facts.binary_present),
            "active": nm_active,
            "nmcli": nm_facts.nmcli_present,
        }

        firewall_by_name = {
            item.name: item for item in host_facts_snapshot.firewall.backends
        }
        firewalld = firewall_by_name.get("firewalld")
        ufw = firewall_by_name.get("ufw")
        nft = firewall_by_name.get("nftables")
        iptables = firewall_by_name.get("iptables")
        firewalld_active = bool(
            firewalld and firewalld.service_active is True
        )
        if _snapshot_probe_failed(
            host_facts_snapshot,
            "firewall.firewalld.service",
        ):
            firewalld_active = (
                _systemctl_is_active("firewalld") if systemctl_present else False
            )
        ufw_active = bool(ufw and ufw.service_active is True)
        if _snapshot_probe_failed(
            host_facts_snapshot,
            "firewall.ufw.service",
        ):
            ufw_active = _systemctl_is_active("ufw") if systemctl_present else False
        firewall = {
            "firewalld": {
                "present": bool(firewalld and firewalld.tool_present),
                "active": firewalld_active,
            },
            "ufw": {
                "present": bool(ufw and ufw.tool_present),
                "active": ufw_active,
            },
            "nft": {"present": bool(nft and nft.tool_present)},
            "iptables": {
                "present": bool(iptables and iptables.tool_present),
            },
        }

    return {
        "systemd": systemd,
        "network_manager": network_manager,
        "firewall": firewall,
    }


def _generate_notes(immutability: Dict[str, Any], integration: Dict[str, Any]) -> List[str]:
    notes: List[str] = []

    if immutability.get("is_immutable"):
        signal = immutability.get("signal", "unknown")
        notes.append(f"immutable:{signal}")

    writable_paths = immutability.get("writable_paths", {})
    if isinstance(writable_paths, dict):
        if not writable_paths.get("/var/lib/vr-hotspot", True):
            notes.append("path_readonly:/var/lib/vr-hotspot")

    nm = integration.get("network_manager", {})
    if nm.get("active"):
        notes.append("network_manager_active")

    firewall = integration.get("firewall", {})
    if firewall.get("firewalld", {}).get("active"):
        notes.append("firewalld_active")
    if firewall.get("ufw", {}).get("active"):
        notes.append("ufw_active")

    return notes


def collect_platform_matrix(
    *,
    host_facts_snapshot: Optional[HostFactsSnapshot] = None,
) -> Dict[str, Any]:
    """
    Collect platform capability matrix.

    Returns a dict safe for JSON serialization with OS, integration, and
    environment information. All probes are best-effort and fail gracefully.
    """
    os_info = _probe_os(host_facts_snapshot)
    immutability = _probe_immutability()
    integration = _probe_integration(host_facts_snapshot)
    session = _probe_session()
    notes = _generate_notes(immutability, integration)

    return {
        "os": os_info,
        "immutability": immutability,
        "integration": integration,
        "session": session,
        "notes": notes,
    }
