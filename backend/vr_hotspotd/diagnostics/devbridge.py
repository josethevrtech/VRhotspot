"""Read-only ADB Dev Bridge discovery, command generation, and readiness.

This module never executes adb and never mutates headset or host state. The
only network interaction is an optional TCP connect() probe against the ADB
TCP port so reachability can be reported. Every adb/logcat command string is
generated for the user to copy and run themselves.

Pure build_* functions consume already-collected inputs so CLI, HTTP,
support-bundle, and future UI callers share one contract. Impure collect_*
helpers gather those inputs from existing read-only sources (runtime state,
config, and the connected-clients snapshot) and never raise.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from vr_hotspotd.config import load_config_snapshot, validate_network_config
from vr_hotspotd.diagnostics.clients import get_clients_snapshot
from vr_hotspotd.state import load_state


DEVBRIDGE_SCHEMA_VERSION = 1
ADB_TCP_PORT = 5555
ADB_PROBE_TIMEOUT_S = 0.5

HEADSET_IP_PLACEHOLDER = "<HEADSET_IP>"
PAIRING_PORT_PLACEHOLDER = "<PAIRING_PORT>"
PAIRING_CODE_PLACEHOLDER = "<PAIRING_CODE>"

COMMAND_KIND_ALL = "all"
COMMAND_KIND_CONNECT = "connect"
COMMAND_KIND_LOGCAT = "logcat"
COMMAND_KINDS = (COMMAND_KIND_ALL, COMMAND_KIND_CONNECT, COMMAND_KIND_LOGCAT)

READINESS_READY = "ready"
READINESS_PARTIAL = "partial"
READINESS_NOT_READY = "not_ready"

CHECK_PASS = "pass"
CHECK_WARNING = "warning"
CHECK_FAIL = "fail"
CHECK_SKIPPED = "skipped"
CHECK_UNKNOWN = "unknown"

LIKELIHOOD_LIKELY = "likely_headset"
LIKELIHOOD_UNKNOWN = "unknown"

_HOSTNAME_VENDOR_HINTS = (
    ("quest", "Meta Quest"),
    ("oculus", "Meta Quest"),
    ("eureka", "Meta Quest"),
    ("pico", "Pico"),
    ("vive", "HTC Vive"),
    ("focus", "HTC Vive Focus"),
    ("pimax", "Pimax"),
    ("lynx", "Lynx"),
)

_READ_ONLY_NOTES = (
    "Dev Bridge is read-only: VR Hotspot never executes adb and never changes "
    "headset or host developer settings.",
    "Copy the generated commands into your own terminal to act on them.",
)

_START_HOTSPOT_COMMAND = (
    'curl -sS -X POST -H "X-Api-Token: $VR_HOTSPOTD_API_TOKEN" '
    "http://127.0.0.1:8732/v1/start"
)

_ADB_INSTALL_HINT = (
    "Install the Android platform tools: package android-tools on Arch/SteamOS, "
    "adb on Debian/Ubuntu, android-tools on Fedora."
)


def classify_headset_hostname(hostname: Optional[str]) -> Dict[str, Optional[str]]:
    """Best-effort, hostname-only classification. Never authoritative."""

    text = (hostname or "").lower()
    for hint, vendor in _HOSTNAME_VENDOR_HINTS:
        if hint in text:
            return {"likelihood": LIKELIHOOD_LIKELY, "vendor_guess": vendor}
    return {"likelihood": LIKELIHOOD_UNKNOWN, "vendor_guess": None}


def subnet_cidr_from_gateway(gateway_ip: Optional[str]) -> Optional[str]:
    if not gateway_ip:
        return None
    try:
        return str(ipaddress.IPv4Network(f"{gateway_ip}/24", strict=False))
    except ValueError:
        return None


def is_valid_ipv4(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def build_adb_connect_commands(
    ip: Optional[str] = None,
    *,
    port: int = ADB_TCP_PORT,
) -> Dict[str, str]:
    """Copyable adb connection commands. Nothing here is ever executed."""

    target = f"{ip or HEADSET_IP_PLACEHOLDER}:{port}"
    return {
        "connect": f"adb connect {target}",
        "disconnect": f"adb disconnect {target}",
        "list_devices": "adb devices -l",
        "enable_tcpip_over_usb": f"adb tcpip {port}",
    }


def build_logcat_commands(
    ip: Optional[str] = None,
    *,
    port: int = ADB_TCP_PORT,
) -> Dict[str, str]:
    """Copyable logcat helper commands. Logcat is never collected automatically."""

    target = f"{ip or HEADSET_IP_PLACEHOLDER}:{port}"
    return {
        "follow": f"adb -s {target} logcat",
        "unity_only": f"adb -s {target} logcat -s Unity",
        "crash_buffer": f"adb -s {target} logcat -b crash",
        "clear": f"adb -s {target} logcat -c",
        "save_snapshot": f"adb -s {target} logcat -d > headset-logcat.txt",
    }


def build_pairing_guidance(
    ip: Optional[str] = None,
    *,
    port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Wireless Debugging pairing guidance with explicit placeholders."""

    pair_host = ip or HEADSET_IP_PLACEHOLDER
    return {
        "steps": [
            (
                "On the headset, enable Developer Mode (usually via the vendor's "
                "companion app) and turn on debugging in the headset settings."
            ),
            (
                "For Android 11+ Wireless Debugging: open the headset's developer "
                "settings, enable Wireless Debugging, choose 'Pair device with "
                "pairing code', and note the pairing port and 6-digit code shown."
            ),
            "Run the pair command below with the pairing port and code from the headset.",
            (
                f"After pairing once, use the connect command on port {port} (or the "
                "port shown under Wireless Debugging)."
            ),
            (
                "Alternative without pairing: connect the headset over USB once, "
                f"accept the debugging prompt, run 'adb tcpip {port}', unplug USB, "
                "then use the connect command."
            ),
        ],
        "commands": {
            "pair": (
                f"adb pair {pair_host}:{PAIRING_PORT_PLACEHOLDER} "
                f"{PAIRING_CODE_PLACEHOLDER}"
            ),
            "connect_after_pairing": f"adb connect {pair_host}:{port}",
        },
        "notes": [
            "The pairing code is shown only on the headset and is never stored "
            "or inferred by VR Hotspot.",
        ],
    }


def probe_adb_tcp(
    ip: str,
    *,
    port: int = ADB_TCP_PORT,
    timeout_s: float = ADB_PROBE_TIMEOUT_S,
) -> Dict[str, Any]:
    """TCP connect() reachability probe. No ADB protocol bytes are exchanged."""

    result: Dict[str, Any] = {
        "tcp_port": port,
        "checked": True,
        "reachable": None,
        "error": None,
        "latency_ms": None,
    }
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            pass
    except socket.timeout:
        result["reachable"] = False
        result["error"] = "timeout"
        return result
    except ConnectionRefusedError:
        result["reachable"] = False
        result["error"] = "connection_refused"
        return result
    except OSError:
        result["reachable"] = False
        result["error"] = "unreachable"
        return result
    result["reachable"] = True
    result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    return result


def _unchecked_probe(port: int) -> Dict[str, Any]:
    return {
        "tcp_port": port,
        "checked": False,
        "reachable": None,
        "error": None,
        "latency_ms": None,
    }


def build_device_entry(
    client: Mapping[str, Any],
    *,
    adb_probe: Optional[Mapping[str, Any]] = None,
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Project one connected-clients entry into the Dev Bridge device model."""

    ip = client.get("ip")
    hostname = client.get("hostname")
    classification = classify_headset_hostname(hostname)

    adb = _unchecked_probe(adb_port)
    if isinstance(adb_probe, Mapping):
        adb.update({key: adb_probe.get(key, adb[key]) for key in adb})

    commands: Optional[Dict[str, Any]] = None
    if is_valid_ipv4(ip):
        commands = {
            "connect": build_adb_connect_commands(ip, port=adb_port),
            "logcat": build_logcat_commands(ip, port=adb_port),
        }

    return {
        "mac": client.get("mac"),
        "ip": ip,
        "hostname": hostname,
        "headset_likelihood": classification["likelihood"],
        "headset_vendor_guess": classification["vendor_guess"],
        "wifi": {
            "source": client.get("source"),
            "associated": client.get("associated"),
            "authorized": client.get("authorized"),
            "signal_dbm": client.get("signal_dbm"),
            "tx_bitrate_mbps": client.get("tx_bitrate_mbps"),
            "rx_bitrate_mbps": client.get("rx_bitrate_mbps"),
            "connected_time_s": client.get("connected_time_s"),
        },
        "adb": adb,
        "commands": commands,
    }


def build_devbridge_devices_report(
    *,
    clients_snapshot: Mapping[str, Any],
    adb_probes: Optional[Mapping[str, Mapping[str, Any]]] = None,
    probe_requested: bool = True,
    gateway_ip: Optional[str] = None,
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Pure device-scan model built from a connected-clients snapshot."""

    probes = adb_probes or {}
    clients = clients_snapshot.get("clients") or []
    devices = [
        build_device_entry(
            client,
            adb_probe=probes.get(client.get("ip")) if client.get("ip") else None,
            adb_port=adb_port,
        )
        for client in clients
        if isinstance(client, Mapping)
    ]

    return {
        "schema_version": DEVBRIDGE_SCHEMA_VERSION,
        "adb_tcp_port": adb_port,
        "probe_requested": bool(probe_requested),
        "ap_interface": clients_snapshot.get("ap_interface"),
        "subnet_cidr": subnet_cidr_from_gateway(gateway_ip),
        "devices": devices,
        "summary": {
            "devices_detected": len(devices),
            "likely_headsets": sum(
                1 for d in devices if d["headset_likelihood"] == LIKELIHOOD_LIKELY
            ),
            "adb_tcp_reachable": sum(
                1 for d in devices if d["adb"]["reachable"] is True
            ),
        },
        "warnings": list(clients_snapshot.get("warnings") or []),
        "sources": dict(clients_snapshot.get("sources") or {}),
        "notes": list(_READ_ONLY_NOTES),
    }


def build_devbridge_status(
    *,
    state: Mapping[str, Any],
    config: Mapping[str, Any],
    clients_snapshot: Mapping[str, Any],
    host_adb_path: Optional[str],
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Pure status model; probe-free so it stays cheap."""

    clients = clients_snapshot.get("clients") or []
    likely_headsets = sum(
        1
        for client in clients
        if isinstance(client, Mapping)
        and classify_headset_hostname(client.get("hostname"))["likelihood"]
        == LIKELIHOOD_LIKELY
    )
    gateway_ip = config.get("lan_gateway_ip")

    return {
        "schema_version": DEVBRIDGE_SCHEMA_VERSION,
        "mode": "read_only",
        "adb_tcp_port": adb_port,
        "hotspot": {
            "running": bool(state.get("running")),
            "phase": state.get("phase"),
            "adapter": state.get("adapter"),
            "ap_interface": clients_snapshot.get("ap_interface")
            or state.get("ap_interface"),
            "ssid": config.get("ssid"),
        },
        "network": {
            "gateway_ip": gateway_ip,
            "subnet_cidr": subnet_cidr_from_gateway(gateway_ip),
            "dhcp_start_ip": config.get("dhcp_start_ip"),
            "dhcp_end_ip": config.get("dhcp_end_ip"),
        },
        "host_adb": {
            "present": bool(host_adb_path),
            "path": host_adb_path,
        },
        "summary": {
            "devices_detected": len(clients),
            "likely_headsets": likely_headsets,
        },
        "warnings": list(clients_snapshot.get("warnings") or []),
        "notes": list(_READ_ONLY_NOTES),
    }


def build_adb_command_report(
    *,
    clients: Iterable[Mapping[str, Any]] = (),
    ip: Optional[str] = None,
    kind: str = COMMAND_KIND_ALL,
    host_adb_path: Optional[str] = None,
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Pure copyable-command model. Never executes anything."""

    if kind not in COMMAND_KINDS:
        kind = COMMAND_KIND_ALL
    include_connect = kind in (COMMAND_KIND_ALL, COMMAND_KIND_CONNECT)
    include_logcat = kind in (COMMAND_KIND_ALL, COMMAND_KIND_LOGCAT)
    warnings: List[str] = []

    def _commands_for(target_ip: Optional[str]) -> Dict[str, Any]:
        commands: Dict[str, Any] = {}
        if include_connect:
            commands["connect"] = build_adb_connect_commands(target_ip, port=adb_port)
        if include_logcat:
            commands["logcat"] = build_logcat_commands(target_ip, port=adb_port)
        return commands

    devices: List[Dict[str, Any]] = []
    matched_requested_ip = False
    for client in clients:
        if not isinstance(client, Mapping):
            continue
        client_ip = client.get("ip")
        if not is_valid_ipv4(client_ip):
            continue
        if ip and client_ip != ip:
            continue
        matched_requested_ip = matched_requested_ip or client_ip == ip
        devices.append(
            {
                "ip": client_ip,
                "hostname": client.get("hostname"),
                "commands": _commands_for(client_ip),
            }
        )

    if ip and not matched_requested_ip:
        warnings.append("requested_ip_not_in_client_list")
        devices.append({"ip": ip, "hostname": None, "commands": _commands_for(ip)})

    report: Dict[str, Any] = {
        "schema_version": DEVBRIDGE_SCHEMA_VERSION,
        "kind": kind,
        "adb_tcp_port": adb_port,
        "host_adb": {
            "present": bool(host_adb_path),
            "path": host_adb_path,
            "install_hint": None if host_adb_path else _ADB_INSTALL_HINT,
        },
        "devices": devices,
        "generic": _commands_for(None),
        "warnings": warnings,
        "notes": list(_READ_ONLY_NOTES),
    }
    if include_connect:
        report["pairing"] = build_pairing_guidance(ip, port=adb_port)
    return report


def _check(
    code: str,
    title: str,
    status: str,
    *,
    detail: str,
    next_step: Optional[str] = None,
    next_step_command: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "status": status,
        "detail": detail,
        "next_step": next_step,
        "next_step_command": next_step_command,
    }


def build_devbridge_readiness_report(
    *,
    state: Mapping[str, Any],
    network_config_errors: Iterable[str] = (),
    clients: Iterable[Mapping[str, Any]] = (),
    adb_probes: Optional[Mapping[str, Mapping[str, Any]]] = None,
    host_adb_path: Optional[str] = None,
    probe_requested: bool = True,
    ssid: Optional[str] = None,
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Any]:
    """Pure readiness model. Every failed check carries a copyable next step."""

    client_list = [c for c in clients if isinstance(c, Mapping)]
    probes = adb_probes or {}
    config_errors = [str(e) for e in network_config_errors if str(e)]
    running = bool(state.get("running"))
    phase = state.get("phase")
    ap_interface = state.get("ap_interface")
    reachable_ips = [
        ip
        for ip, probe in probes.items()
        if isinstance(probe, Mapping) and probe.get("reachable") is True
    ]
    checks: List[Dict[str, Any]] = []

    if running:
        checks.append(
            _check(
                "hotspot_running",
                "VR Hotspot is running",
                CHECK_PASS,
                detail=f"The hotspot is running (phase: {phase}).",
            )
        )
    else:
        checks.append(
            _check(
                "hotspot_running",
                "VR Hotspot is running",
                CHECK_FAIL,
                detail=f"The hotspot is not running (phase: {phase}).",
                next_step="Start the hotspot from the UI, or with the explicit API call below.",
                next_step_command=_START_HOTSPOT_COMMAND,
            )
        )

    if not running:
        checks.append(
            _check(
                "ap_interface_present",
                "AP interface is up",
                CHECK_SKIPPED,
                detail="Skipped because the hotspot is not running.",
                next_step="Start the hotspot first.",
                next_step_command=_START_HOTSPOT_COMMAND,
            )
        )
    elif ap_interface:
        checks.append(
            _check(
                "ap_interface_present",
                "AP interface is up",
                CHECK_PASS,
                detail=f"Access point interface: {ap_interface}.",
            )
        )
    else:
        checks.append(
            _check(
                "ap_interface_present",
                "AP interface is up",
                CHECK_FAIL,
                detail="The hotspot reports running but no AP interface is recorded.",
                next_step="Run the host preflight report to diagnose the access point.",
                next_step_command="vr-hotspot preflight",
            )
        )

    if config_errors:
        checks.append(
            _check(
                "network_config_valid",
                "Hotspot network configuration is valid",
                CHECK_FAIL,
                detail="Network configuration errors: " + ", ".join(config_errors) + ".",
                next_step="Fix the LAN/DHCP settings, then re-check with the preflight report.",
                next_step_command="vr-hotspot preflight",
            )
        )
    else:
        checks.append(
            _check(
                "network_config_valid",
                "Hotspot network configuration is valid",
                CHECK_PASS,
                detail="The fixed /24 LAN and DHCP range validate cleanly.",
            )
        )

    if host_adb_path:
        checks.append(
            _check(
                "host_adb_present",
                "adb is installed on this computer",
                CHECK_PASS,
                detail=f"Found adb at {host_adb_path}.",
            )
        )
    else:
        checks.append(
            _check(
                "host_adb_present",
                "adb is installed on this computer",
                CHECK_WARNING,
                detail="adb was not found on PATH. " + _ADB_INSTALL_HINT,
                next_step="Install the Android platform tools, then verify with the command below.",
                next_step_command="adb version",
            )
        )

    if not running:
        checks.append(
            _check(
                "devices_detected",
                "A headset is connected to the hotspot",
                CHECK_SKIPPED,
                detail="Skipped because the hotspot is not running.",
                next_step="Start the hotspot first.",
                next_step_command=_START_HOTSPOT_COMMAND,
            )
        )
    elif client_list:
        checks.append(
            _check(
                "devices_detected",
                "A headset is connected to the hotspot",
                CHECK_PASS,
                detail=f"{len(client_list)} device(s) detected on the hotspot network.",
            )
        )
    else:
        ssid_text = f" '{ssid}'" if ssid else ""
        checks.append(
            _check(
                "devices_detected",
                "A headset is connected to the hotspot",
                CHECK_WARNING,
                detail="No devices are connected to the hotspot network yet.",
                next_step=(
                    f"Connect the headset to the{ssid_text} Wi-Fi network, then rescan."
                ),
                next_step_command="vr-hotspot devbridge scan",
            )
        )

    if not running or not client_list:
        checks.append(
            _check(
                "adb_tcp_reachable",
                f"ADB TCP port {adb_port} is reachable on a headset",
                CHECK_SKIPPED,
                detail="Skipped because no connected devices were detected.",
                next_step="Connect the headset to the hotspot, then rescan.",
                next_step_command="vr-hotspot devbridge scan",
            )
        )
    elif not probe_requested:
        checks.append(
            _check(
                "adb_tcp_reachable",
                f"ADB TCP port {adb_port} is reachable on a headset",
                CHECK_UNKNOWN,
                detail="The TCP reachability probe was disabled for this request.",
                next_step="Rescan with probing enabled.",
                next_step_command="vr-hotspot devbridge scan",
            )
        )
    elif reachable_ips:
        checks.append(
            _check(
                "adb_tcp_reachable",
                f"ADB TCP port {adb_port} is reachable on a headset",
                CHECK_PASS,
                detail=(
                    f"ADB TCP port {adb_port} is open on: "
                    + ", ".join(sorted(reachable_ips))
                    + "."
                ),
            )
        )
    else:
        checks.append(
            _check(
                "adb_tcp_reachable",
                f"ADB TCP port {adb_port} is reachable on a headset",
                CHECK_WARNING,
                detail=(
                    f"No detected device is listening on TCP port {adb_port}. "
                    "Wireless debugging is probably off, or ADB over TCP is not enabled."
                ),
                next_step=(
                    "Enable Wireless Debugging on the headset, or connect it over USB "
                    "once and enable ADB over TCP with the command below."
                ),
                next_step_command=f"adb tcpip {adb_port}",
            )
        )

    unity_ready = bool(host_adb_path) and bool(reachable_ips)
    if unity_ready:
        first_ip = sorted(reachable_ips)[0]
        checks.append(
            _check(
                "unity_build_and_run",
                "Unity Build & Run can target the headset",
                CHECK_PASS,
                detail=(
                    "Unity deploys to devices listed by 'adb devices'. Connect first, "
                    "then confirm the headset shows as 'device' (not 'unauthorized')."
                ),
                next_step="Connect adb to the headset, then use Build & Run in Unity.",
                next_step_command=f"adb connect {first_ip}:{adb_port}",
            )
        )
    else:
        checks.append(
            _check(
                "unity_build_and_run",
                "Unity Build & Run can target the headset",
                CHECK_SKIPPED,
                detail=(
                    "Requires adb on this computer and a headset reachable on "
                    f"TCP port {adb_port}; see the earlier checks."
                ),
                next_step="Resolve the failed checks above first.",
                next_step_command=f"adb connect {HEADSET_IP_PLACEHOLDER}:{adb_port}",
            )
        )

    statuses = [check["status"] for check in checks]
    if CHECK_FAIL in statuses:
        overall = READINESS_NOT_READY
    elif CHECK_WARNING in statuses or CHECK_SKIPPED in statuses or CHECK_UNKNOWN in statuses:
        overall = READINESS_PARTIAL
    else:
        overall = READINESS_READY

    return {
        "schema_version": DEVBRIDGE_SCHEMA_VERSION,
        "overall": overall,
        "adb_tcp_port": adb_port,
        "probe_requested": bool(probe_requested),
        "checks": checks,
        "summary": {
            status: statuses.count(status)
            for status in (CHECK_PASS, CHECK_WARNING, CHECK_FAIL, CHECK_SKIPPED, CHECK_UNKNOWN)
        },
        "notes": list(_READ_ONLY_NOTES),
    }


def _host_adb_path() -> Optional[str]:
    try:
        return shutil.which("adb")
    except Exception:
        return None


def _clients_snapshot(state: Mapping[str, Any]) -> Dict[str, Any]:
    adapter = state.get("adapter")
    try:
        return get_clients_snapshot(
            adapter if adapter else None,
            ap_interface_hint=state.get("ap_interface"),
        )
    except Exception:
        return {
            "conf_dir": None,
            "ap_interface": None,
            "clients": [],
            "warnings": ["clients_snapshot_failed"],
            "sources": {"primary": None, "enrichment": []},
        }


def _probe_clients(
    clients: Iterable[Mapping[str, Any]],
    *,
    probe: Optional[Callable[..., Dict[str, Any]]] = None,
    adb_port: int = ADB_TCP_PORT,
) -> Dict[str, Dict[str, Any]]:
    probe_fn = probe or probe_adb_tcp
    results: Dict[str, Dict[str, Any]] = {}
    for client in clients:
        if not isinstance(client, Mapping):
            continue
        ip = client.get("ip")
        if not is_valid_ipv4(ip) or ip in results:
            continue
        try:
            results[ip] = probe_fn(ip, port=adb_port)
        except Exception:
            results[ip] = {
                "tcp_port": adb_port,
                "checked": True,
                "reachable": None,
                "error": "probe_failed",
                "latency_ms": None,
            }
    return results


def _network_config_errors(config: Mapping[str, Any]) -> List[str]:
    try:
        return list(validate_network_config(config))
    except Exception:
        return ["network_config_validation_failed"]


def collect_devbridge_status() -> Dict[str, Any]:
    """Gather the probe-free Dev Bridge status. Never raises."""

    state = load_state()
    config = load_config_snapshot()
    return build_devbridge_status(
        state=state,
        config=config,
        clients_snapshot=_clients_snapshot(state),
        host_adb_path=_host_adb_path(),
    )


def collect_devbridge_devices(
    *,
    probe_adb: bool = True,
    probe: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Gather the device scan, optionally TCP-probing port 5555. Never raises."""

    state = load_state()
    config = load_config_snapshot()
    snapshot = _clients_snapshot(state)
    probes = (
        _probe_clients(snapshot.get("clients") or [], probe=probe)
        if probe_adb
        else {}
    )
    return build_devbridge_devices_report(
        clients_snapshot=snapshot,
        adb_probes=probes,
        probe_requested=probe_adb,
        gateway_ip=config.get("lan_gateway_ip"),
    )


def collect_adb_command_report(
    *,
    ip: Optional[str] = None,
    kind: str = COMMAND_KIND_ALL,
) -> Dict[str, Any]:
    """Gather copyable command sets for known devices. Never raises, never probes."""

    state = load_state()
    snapshot = _clients_snapshot(state)
    return build_adb_command_report(
        clients=snapshot.get("clients") or [],
        ip=ip,
        kind=kind,
        host_adb_path=_host_adb_path(),
    )


def collect_devbridge_readiness(
    *,
    probe_adb: bool = True,
    probe: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Gather the readiness report, optionally TCP-probing port 5555. Never raises."""

    state = load_state()
    config = load_config_snapshot()
    snapshot = _clients_snapshot(state)
    clients = snapshot.get("clients") or []
    probes = _probe_clients(clients, probe=probe) if probe_adb else {}
    return build_devbridge_readiness_report(
        state=state,
        network_config_errors=_network_config_errors(config),
        clients=clients,
        adb_probes=probes,
        host_adb_path=_host_adb_path(),
        probe_requested=probe_adb,
        ssid=config.get("ssid"),
    )
