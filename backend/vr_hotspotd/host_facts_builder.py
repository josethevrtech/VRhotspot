"""Read-only builder for an operation-scoped :class:`HostFactsSnapshot`.

Diagnostics, adapter inventory/readiness, and lifecycle selection consume a
fresh snapshot for each operation. Command execution and filesystem access are
injectable so unit tests use deterministic fakes; live post-start route/NAT
discovery remains outside this pre-mutation collection window.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from vr_hotspotd import host_probes, os_release
from vr_hotspotd.host_facts import (
    AdapterFacts,
    DefaultRouteFact,
    DefaultUplinkFacts,
    FirewallBackendFacts,
    FirewallFacts,
    FrequencyFacts,
    HostFactsSnapshot,
    IwDevFacts,
    IwdFacts,
    IwInterfaceFacts,
    IwPhyFacts,
    NetworkManagerFacts,
    OsReleaseFact,
    PlatformFacts,
    ProbeError,
    ProbeRecord,
    RegulatoryDomainFacts,
    RegulatoryFacts,
    SnapshotMetadata,
)


SNAPSHOT_SCHEMA_VERSION = 1
BUILDER_VERSION = "1"
MAX_CAPTURE_CHARS = 64 * 1024
MAX_ERROR_MESSAGE_CHARS = 240
MAX_OS_RELEASE_ENTRIES = 64
MAX_OS_RELEASE_VALUE_CHARS = 512

CommandRunner = Callable[..., Any]
ExecutableResolver = Callable[[str], Optional[str]]
OsReleaseReader = Callable[[], Mapping[str, str]]
SysfsReader = Callable[[str], Optional[str]]
SnapshotIdFactory = Callable[[], str]

_VALID_IFNAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
_VALID_PHY_RE = re.compile(r"^phy\d+$")


class SnapshotClock(Protocol):
    def utc_now(self) -> datetime:
        ...

    def monotonic(self) -> float:
        ...


class SystemSnapshotClock:
    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


def _default_sysfs_reader(path: str) -> Optional[str]:
    if not os.path.lexists(path):
        return None
    return os.path.realpath(path)


def _default_snapshot_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class _CommandCapture:
    result: host_probes.CommandResult
    output: str
    output_truncated: bool


class HostFactsSnapshotBuilder:
    """Collect one immutable snapshot through read-only injected dependencies."""

    def __init__(
        self,
        *,
        runner: Optional[CommandRunner] = None,
        executable_resolver: Optional[ExecutableResolver] = None,
        os_release_reader: Optional[OsReleaseReader] = None,
        sysfs_reader: Optional[SysfsReader] = None,
        clock: Optional[SnapshotClock] = None,
        snapshot_id_factory: Optional[SnapshotIdFactory] = None,
    ) -> None:
        self._runner = runner
        self._executable_resolver = executable_resolver or shutil.which
        self._os_release_reader = os_release_reader or os_release.read_os_release
        self._sysfs_reader = sysfs_reader or _default_sysfs_reader
        self._clock = clock or SystemSnapshotClock()
        self._snapshot_id_factory = snapshot_id_factory or _default_snapshot_id

    def build(self, *, operation_kind: str = "unspecified") -> HostFactsSnapshot:
        collector = _SnapshotCollector(
            runner=self._runner,
            executable_resolver=self._executable_resolver,
            os_release_reader=self._os_release_reader,
            sysfs_reader=self._sysfs_reader,
            clock=self._clock,
            snapshot_id_factory=self._snapshot_id_factory,
        )
        return collector.collect(operation_kind=operation_kind)


def build_host_facts_snapshot(
    *,
    operation_kind: str = "unspecified",
    runner: Optional[CommandRunner] = None,
    executable_resolver: Optional[ExecutableResolver] = None,
    os_release_reader: Optional[OsReleaseReader] = None,
    sysfs_reader: Optional[SysfsReader] = None,
    clock: Optional[SnapshotClock] = None,
    snapshot_id_factory: Optional[SnapshotIdFactory] = None,
) -> HostFactsSnapshot:
    """Convenience wrapper that creates a fresh operation-scoped builder."""

    return HostFactsSnapshotBuilder(
        runner=runner,
        executable_resolver=executable_resolver,
        os_release_reader=os_release_reader,
        sysfs_reader=sysfs_reader,
        clock=clock,
        snapshot_id_factory=snapshot_id_factory,
    ).build(operation_kind=operation_kind)


class _SnapshotCollector:
    def __init__(
        self,
        *,
        runner: Optional[CommandRunner],
        executable_resolver: ExecutableResolver,
        os_release_reader: OsReleaseReader,
        sysfs_reader: SysfsReader,
        clock: SnapshotClock,
        snapshot_id_factory: SnapshotIdFactory,
    ) -> None:
        self._runner = runner
        self._executable_resolver = executable_resolver
        self._os_release_reader = os_release_reader
        self._sysfs_reader = sysfs_reader
        self._clock = clock
        self._snapshot_id_factory = snapshot_id_factory
        self._records: List[ProbeRecord] = []
        self._errors: List[ProbeError] = []
        self._resolved_tools: Dict[str, Optional[str]] = {}
        self._started_monotonic = 0.0

    def collect(self, *, operation_kind: str) -> HostFactsSnapshot:
        self._started_monotonic = self._clock.monotonic()
        started_at_utc = _format_utc(self._clock.utc_now())

        os_info = self._capture_os_release()
        platform = self._build_platform_facts(os_info)
        iw_dev, iw_phys = self._capture_iw_facts()
        regulatory = self._capture_regulatory_facts()
        default_uplink = self._capture_default_uplink()
        network_manager = self._capture_network_manager()
        iwd = self._capture_iwd(iw_dev)
        firewall = self._capture_firewall()
        adapters = self._build_adapter_facts(iw_dev, iw_phys, regulatory)

        completed_monotonic = self._clock.monotonic()
        completed_at_utc = _format_utc(self._clock.utc_now())
        duration_ms = _duration_ms(self._started_monotonic, completed_monotonic)
        snapshot_id = _bounded_text(self._snapshot_id_factory(), 128) or "unknown"
        normalized_operation = _bounded_text(operation_kind, 64) or "unspecified"

        return HostFactsSnapshot(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            metadata=SnapshotMetadata(
                snapshot_id=snapshot_id,
                operation_kind=normalized_operation,
                source="host_facts_builder",
                started_at_utc=started_at_utc,
                completed_at_utc=completed_at_utc,
                monotonic_duration_ms=duration_ms,
                builder_version=BUILDER_VERSION,
            ),
            platform=platform,
            default_uplink=default_uplink,
            iw_dev=iw_dev,
            iw_phys=iw_phys,
            regulatory=regulatory,
            network_manager=network_manager,
            iwd=iwd,
            firewall=firewall,
            adapters=adapters,
            probe_records=tuple(self._records),
            probe_errors=tuple(self._errors),
        )

    def _capture_os_release(self) -> Dict[str, str]:
        probe_id = "platform.os_release"
        started = self._clock.monotonic()
        permission_denied = False
        missing = False
        truncated = False
        normalized: Dict[str, str] = {}
        try:
            raw_info = self._os_release_reader()
            if not isinstance(raw_info, Mapping):
                raise TypeError("OS release reader did not return a mapping")
            for index, (raw_key, raw_value) in enumerate(
                sorted(raw_info.items(), key=lambda item: str(item[0]).lower())
            ):
                if index >= MAX_OS_RELEASE_ENTRIES:
                    truncated = True
                    break
                key = _bounded_text(raw_key, 64).strip().lower()
                value_text = str(raw_value)
                value = _bounded_text(value_text, MAX_OS_RELEASE_VALUE_CHARS)
                truncated = truncated or len(value_text) > len(value)
                if key:
                    normalized[key] = value
            if not normalized:
                missing = True
                self._add_error(probe_id, "missing", "OS release data is unavailable")
            if truncated:
                self._add_error(
                    probe_id,
                    "truncated",
                    "OS release input exceeded the snapshot budget",
                )
        except PermissionError as exc:
            permission_denied = True
            self._add_error(probe_id, "permission", exc, exit_status=None)
        except FileNotFoundError as exc:
            missing = True
            self._add_error(probe_id, "missing", exc, exit_status=None)
        except Exception as exc:
            self._add_error(probe_id, "io", exc, exit_status=None)
        completed = self._clock.monotonic()
        self._records.append(
            self._probe_record(
                probe_id=probe_id,
                source_kind="file",
                source=("/etc/os-release", "/usr/lib/os-release"),
                started=started,
                completed=completed,
                exit_status=0 if normalized else None,
                timed_out=False,
                missing=missing,
                permission_denied=permission_denied,
                output_truncated=truncated,
            )
        )
        return normalized

    def _build_platform_facts(self, info: Mapping[str, str]) -> PlatformFacts:
        classification = host_probes.classify_os_flavor(info)
        flavor = str(classification.get("flavor") or "unknown")
        family_value = classification.get("family")
        family = str(family_value) if isinstance(family_value, str) else None

        signals: List[str] = []
        if flavor in ("steamos", "bazzite", "fedora_atomic"):
            signals.append(f"flavor:{flavor}")
        if self._resolve_tool("rpm-ostree"):
            signals.append("tool:rpm-ostree")
        if self._resolve_tool("steamos-readonly"):
            signals.append("tool:steamos-readonly")

        if signals:
            is_immutable: Optional[bool] = True
        elif info:
            is_immutable = False
        else:
            is_immutable = None

        if flavor == "steamos":
            host_kind = "steamos"
        elif is_immutable is True:
            host_kind = "immutable_linux"
        elif info:
            host_kind = "mutable_linux"
        else:
            host_kind = "unknown"

        return PlatformFacts(
            os_release=tuple(
                OsReleaseFact(key=key, value=value)
                for key, value in sorted(info.items())
            ),
            os_id=_optional_text(info.get("id")),
            os_name=_optional_text(info.get("pretty_name") or info.get("name")),
            version_id=_optional_text(info.get("version_id")),
            variant_id=_optional_text(info.get("variant_id")),
            id_like=tuple(host_probes.split_tokens(info.get("id_like"))),
            flavor=flavor,
            family=family,
            package_manager_family=_package_manager_family(
                family=family,
                flavor=flavor,
                immutable=is_immutable,
            ),
            host_kind=host_kind,
            is_immutable=is_immutable,
            immutability_signals=tuple(signals),
            source_probe_id="platform.os_release",
        )

    def _capture_iw_facts(self) -> Tuple[IwDevFacts, Tuple[IwPhyFacts, ...]]:
        iw = self._resolve_tool("iw")
        if not iw:
            self._missing_command("iw.dev", ("iw", "dev"), "iw")
            return IwDevFacts(interfaces=(), source_probe_id="iw.dev"), ()

        capture = self._capture_command("iw.dev", (iw, "dev"), timeout_s=3.0)
        interfaces: List[IwInterfaceFacts] = []
        discovered_phys: List[str] = []
        if capture.result.ok:
            try:
                parsed = host_probes.parse_iw_dev_facts(capture.output)
            except Exception as exc:
                parsed = []
                self._add_error(
                    "iw.dev",
                    "parse",
                    f"iw dev parsing failed: {exc}",
                    capture.result.exit_status,
                )
            if capture.output.strip() and not parsed:
                self._add_error(
                    "iw.dev",
                    "parse",
                    "iw dev output contained no parseable interfaces",
                    capture.result.exit_status,
                )
            for item in parsed:
                ifname = str(item.get("ifname") or "").strip()
                if not ifname:
                    self._add_error(
                        "iw.dev",
                        "parse",
                        "iw dev contained an interface without a name",
                        capture.result.exit_status,
                    )
                    continue
                raw_phy = item.get("phy")
                phy = str(raw_phy) if isinstance(raw_phy, str) and raw_phy else None
                if phy is None or not _VALID_PHY_RE.match(phy):
                    self._add_error(
                        "iw.dev",
                        "parse",
                        f"interface {ifname} has no valid phy identifier",
                        capture.result.exit_status,
                    )
                    phy = None
                elif phy not in discovered_phys:
                    discovered_phys.append(phy)
                interface_type = _optional_text(item.get("interface_type"))
                interfaces.append(
                    IwInterfaceFacts(
                        ifname=_bounded_text(ifname, 64),
                        phy=phy,
                        interface_type=interface_type,
                        ssid_present=bool(item.get("ssid_present")),
                    )
                )

        phy_facts = tuple(self._capture_one_phy(iw, phy) for phy in discovered_phys)
        return (
            IwDevFacts(interfaces=tuple(interfaces), source_probe_id="iw.dev"),
            phy_facts,
        )

    def _capture_one_phy(self, iw: str, phy: str) -> IwPhyFacts:
        probe_id = f"iw.phy.{phy}"
        capture = self._capture_command(
            probe_id,
            (iw, "phy", phy, "info"),
            timeout_s=4.0,
        )
        if not capture.result.ok:
            return _empty_phy_facts(phy, probe_id)

        try:
            modes = host_probes.parse_all_supported_interface_modes(capture.output)
            frequency_items = host_probes.parse_iw_frequencies(capture.output)
            bands = host_probes.parse_band_support(capture.output)
            supports_ap = _modes_support_ap(modes)
            supports_80mhz = host_probes.supports_80mhz(capture.output)
            supports_wifi6 = host_probes.supports_wifi6(capture.output)
            concurrency = host_probes.parse_ap_managed_concurrency(capture.output)
        except Exception as exc:
            self._add_error(
                probe_id,
                "parse",
                f"wireless phy parsing failed: {exc}",
                capture.result.exit_status,
            )
            return _empty_phy_facts(phy, probe_id)
        if not modes:
            self._add_error(
                probe_id,
                "parse",
                "supported interface mode facts were not found",
                capture.result.exit_status,
            )
        if not frequency_items:
            self._add_error(
                probe_id,
                "parse",
                "wireless frequency facts were not found",
                capture.result.exit_status,
            )
        frequencies = tuple(
            FrequencyFacts(
                frequency_mhz=int(item["frequency_mhz"]),
                channel=int(item["channel"]),
                band=str(item["band"]),
                disabled=bool(item["disabled"]),
                no_ir=bool(item["no_ir"]),
                dfs=bool(item["dfs"]),
            )
            for item in frequency_items
        )
        structurally_complete = bool(modes) and bool(frequency_items)
        return IwPhyFacts(
            phy=phy,
            interface_modes_known=bool(modes),
            supported_interface_modes=tuple(modes or ()),
            supports_ap=supports_ap if modes else None,
            supports_2ghz=(bool(bands.get("supports_2ghz")) if frequency_items else None),
            supports_5ghz=(bool(bands.get("supports_5ghz")) if frequency_items else None),
            supports_6ghz=(bool(bands.get("supports_6ghz")) if frequency_items else None),
            supports_80mhz=supports_80mhz if structurally_complete else None,
            supports_wifi6=supports_wifi6 if structurally_complete else None,
            supports_ap_managed_concurrency=concurrency,
            frequencies=frequencies,
            source_probe_id=probe_id,
        )

    def _capture_regulatory_facts(self) -> RegulatoryFacts:
        probe_id = "iw.regulatory"
        iw = self._resolve_tool("iw")
        if not iw:
            self._missing_command(probe_id, ("iw", "reg", "get"), "iw")
            return RegulatoryFacts(
                global_country=None,
                global_raw_header=None,
                phys=(),
                source_probe_id=probe_id,
            )

        capture = self._capture_command(
            probe_id,
            (iw, "reg", "get"),
            timeout_s=2.0,
        )
        if not capture.result.ok:
            return RegulatoryFacts(
                global_country=None,
                global_raw_header=None,
                phys=(),
                source_probe_id=probe_id,
            )

        try:
            parsed = host_probes.parse_regulatory_domains(capture.output)
        except Exception as exc:
            self._add_error(
                probe_id,
                "parse",
                f"regulatory parsing failed: {exc}",
                capture.result.exit_status,
            )
            return RegulatoryFacts(
                global_country=None,
                global_raw_header=None,
                phys=(),
                source_probe_id=probe_id,
            )
        global_data = parsed.get("global", {})
        global_country = _country_or_none(global_data.get("country"))
        raw_phys = parsed.get("phys", {})
        phys: List[RegulatoryDomainFacts] = []
        if isinstance(raw_phys, Mapping):
            for phy, raw_value in sorted(raw_phys.items(), key=lambda item: str(item[0])):
                value = raw_value if isinstance(raw_value, Mapping) else {}
                phys.append(
                    RegulatoryDomainFacts(
                        phy=_bounded_text(phy, 64),
                        country=_country_or_none(value.get("country")),
                        source=_bounded_text(value.get("source") or "unknown", 64),
                        raw_header=_optional_bounded_text(value.get("raw_header"), 256),
                    )
                )
        if global_country is None and not any(item.country for item in phys):
            self._add_error(
                probe_id,
                "parse",
                "regulatory output contained no country facts",
                capture.result.exit_status,
            )
        return RegulatoryFacts(
            global_country=global_country,
            global_raw_header=_optional_bounded_text(global_data.get("raw_header"), 256),
            phys=tuple(phys),
            source_probe_id=probe_id,
        )

    def _capture_default_uplink(self) -> DefaultUplinkFacts:
        probe_id = "network.default_uplink"
        ip = self._resolve_tool("ip")
        if not ip:
            self._missing_command(
                probe_id,
                ("ip", "route", "show", "default"),
                "ip",
            )
            return DefaultUplinkFacts(
                selected_interface=None,
                routes=(),
                source_probe_id=probe_id,
            )

        capture = self._capture_command(
            probe_id,
            (ip, "route", "show", "default"),
            timeout_s=2.0,
        )
        if not capture.result.ok:
            return DefaultUplinkFacts(
                selected_interface=None,
                routes=(),
                source_probe_id=probe_id,
            )
        try:
            route_items = host_probes.parse_default_routes(capture.output)
            selected_interface = host_probes.parse_default_uplink(capture.output)
        except Exception as exc:
            self._add_error(
                probe_id,
                "parse",
                f"default-route parsing failed: {exc}",
                capture.result.exit_status,
            )
            return DefaultUplinkFacts(
                selected_interface=None,
                routes=(),
                source_probe_id=probe_id,
            )
        if capture.output.strip() and not route_items:
            self._add_error(
                probe_id,
                "parse",
                "default-route output contained no parseable default routes",
                capture.result.exit_status,
            )
        routes = tuple(
            DefaultRouteFact(
                interface=_optional_bounded_text(item.get("interface"), 64),
                gateway=_optional_bounded_text(item.get("gateway"), 128),
                metric=item.get("metric") if isinstance(item.get("metric"), int) else None,
                protocol=_optional_bounded_text(item.get("protocol"), 64),
            )
            for item in route_items
        )
        return DefaultUplinkFacts(
            selected_interface=_optional_bounded_text(
                selected_interface,
                64,
            ),
            routes=routes,
            source_probe_id=probe_id,
        )

    def _capture_network_manager(self) -> NetworkManagerFacts:
        nmcli_probe = "network_manager.nmcli"
        nmcli = self._resolve_tool("nmcli")
        nmcli_running: Optional[bool] = None
        if not nmcli:
            self._missing_command(
                nmcli_probe,
                ("nmcli", "-t", "-f", "RUNNING", "g"),
                "nmcli",
            )
        else:
            capture = self._capture_command(
                nmcli_probe,
                (nmcli, "-t", "-f", "RUNNING", "g"),
                timeout_s=1.0,
            )
            if capture.result.ok:
                status = _first_output_line(capture.output)
                if status == "running":
                    nmcli_running = True
                elif status == "not running":
                    nmcli_running = False
                else:
                    self._add_error(
                        nmcli_probe,
                        "parse",
                        "nmcli returned an unrecognized global running state",
                        capture.result.exit_status,
                    )

        service_probe = "network_manager.service"
        service_state, service_active = self._capture_service(
            service_probe,
            "NetworkManager",
        )
        return NetworkManagerFacts(
            binary_present=bool(self._resolve_tool("NetworkManager")),
            nmcli_present=bool(nmcli),
            nmcli_running=nmcli_running,
            service_state=service_state,
            service_active=service_active,
            source_probe_ids=(nmcli_probe, service_probe),
        )

    def _capture_iwd(self, iw_dev: IwDevFacts) -> IwdFacts:
        service_probe = "iwd.service"
        service_state, service_active = self._capture_service(service_probe, "iwd")
        associated = tuple(
            item.ifname for item in iw_dev.interfaces if item.ssid_present is True
        )
        return IwdFacts(
            binary_present=bool(self._resolve_tool("iwd")),
            iwctl_present=bool(self._resolve_tool("iwctl")),
            service_state=service_state,
            service_active=service_active,
            associated_interfaces=associated,
            source_probe_ids=(service_probe, iw_dev.source_probe_id),
        )

    def _capture_firewall(self) -> FirewallFacts:
        firewall_cmd = self._resolve_tool("firewall-cmd")
        firewalld_probe = "firewall.firewalld.functional"
        firewalld_active: Optional[bool] = None
        if not firewall_cmd:
            self._missing_command(
                firewalld_probe,
                ("firewall-cmd", "--state"),
                "firewall-cmd",
            )
        else:
            capture = self._capture_command(
                firewalld_probe,
                (firewall_cmd, "--state"),
                timeout_s=1.0,
            )
            if capture.result.ok and not capture.output_truncated:
                state = _parse_exact_status_line(capture.output)
                if state == "running":
                    firewalld_active = True
                elif state in ("not running", "stopped"):
                    firewalld_active = False
                else:
                    self._add_error(
                        firewalld_probe,
                        "parse",
                        "firewall-cmd returned an unrecognized state",
                        capture.result.exit_status,
                    )
        firewalld_service_probe = "firewall.firewalld.service"
        firewalld_service_state, firewalld_service_active = self._capture_service(
            firewalld_service_probe,
            "firewalld",
        )

        ufw = self._resolve_tool("ufw")
        ufw_probe = "firewall.ufw.functional"
        ufw_active: Optional[bool] = None
        if not ufw:
            self._missing_command(ufw_probe, ("ufw", "status"), "ufw")
        else:
            capture = self._capture_command(
                ufw_probe,
                (ufw, "status"),
                timeout_s=1.5,
            )
            if capture.result.ok:
                parsed_ufw = _parse_ufw_active(capture.output)
                if parsed_ufw is not None:
                    ufw_active = parsed_ufw
                else:
                    self._add_error(
                        ufw_probe,
                        "parse",
                        "ufw output contained no parseable status field",
                        capture.result.exit_status,
                    )
        ufw_service_probe = "firewall.ufw.service"
        ufw_service_state, ufw_service_active = self._capture_service(
            ufw_service_probe,
            "ufw",
        )

        nft = self._resolve_tool("nft")
        iptables = self._resolve_tool("iptables")
        iptables_probe = "firewall.iptables.version"
        iptables_variant: Optional[str] = None
        if not iptables:
            self._missing_command(
                iptables_probe,
                ("iptables", "--version"),
                "iptables",
            )
        else:
            capture = self._capture_command(
                iptables_probe,
                (iptables, "--version"),
                timeout_s=1.0,
            )
            if capture.result.ok:
                lowered = capture.output.lower()
                if "nf_tables" in lowered or "nft" in lowered:
                    iptables_variant = "iptables-nft"
                elif "legacy" in lowered:
                    iptables_variant = "iptables-legacy"
                else:
                    iptables_variant = "iptables-unknown"

        if firewalld_active is True:
            selected = "firewalld"
            rationale = "firewalld_running"
        elif ufw_active is True:
            selected = "ufw"
            rationale = "ufw_active"
        elif nft:
            selected = "nftables"
            rationale = "nft_present"
        elif iptables:
            selected = "iptables"
            rationale = "iptables_present"
        else:
            selected = "unknown"
            rationale = "no_firewall_detected"

        return FirewallFacts(
            backends=(
                FirewallBackendFacts(
                    name="firewalld",
                    tool_present=bool(firewall_cmd),
                    functional_active=firewalld_active,
                    service_state=firewalld_service_state,
                    service_active=firewalld_service_active,
                    variant=None,
                    source_probe_ids=(firewalld_probe, firewalld_service_probe),
                ),
                FirewallBackendFacts(
                    name="ufw",
                    tool_present=bool(ufw),
                    functional_active=ufw_active,
                    service_state=ufw_service_state,
                    service_active=ufw_service_active,
                    variant=None,
                    source_probe_ids=(ufw_probe, ufw_service_probe),
                ),
                FirewallBackendFacts(
                    name="nftables",
                    tool_present=bool(nft),
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant=None,
                    source_probe_ids=(),
                ),
                FirewallBackendFacts(
                    name="iptables",
                    tool_present=bool(iptables),
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant=iptables_variant,
                    source_probe_ids=(iptables_probe,),
                ),
            ),
            selected_backend=selected,
            rationale=rationale,
        )

    def _build_adapter_facts(
        self,
        iw_dev: IwDevFacts,
        iw_phys: Tuple[IwPhyFacts, ...],
        regulatory: RegulatoryFacts,
    ) -> Tuple[AdapterFacts, ...]:
        phy_by_name = {item.phy: item for item in iw_phys}
        regulatory_by_phy = {item.phy: item for item in regulatory.phys}
        adapters: List[AdapterFacts] = []
        for interface in iw_dev.interfaces:
            phy = phy_by_name.get(interface.phy or "")
            reg = regulatory_by_phy.get(interface.phy or "")
            bus, sysfs_probe = self._capture_adapter_bus(interface.ifname)
            source_ids = [iw_dev.source_probe_id]
            if phy is not None:
                source_ids.append(phy.source_probe_id)
            source_ids.append(regulatory.source_probe_id)
            if sysfs_probe:
                source_ids.append(sysfs_probe)
            adapters.append(
                AdapterFacts(
                    ifname=interface.ifname,
                    phy=interface.phy,
                    interface_type=interface.interface_type,
                    associated=interface.ssid_present,
                    bus=bus,
                    supports_ap=phy.supports_ap if phy else None,
                    supports_2ghz=phy.supports_2ghz if phy else None,
                    supports_5ghz=phy.supports_5ghz if phy else None,
                    supports_6ghz=phy.supports_6ghz if phy else None,
                    supports_80mhz=phy.supports_80mhz if phy else None,
                    supports_wifi6=phy.supports_wifi6 if phy else None,
                    regulatory_country=(
                        reg.country if reg and reg.country else regulatory.global_country
                    ),
                    regulatory_source=(
                        reg.source if reg else ("global" if regulatory.global_country else None)
                    ),
                    source_probe_ids=tuple(source_ids),
                )
            )
        return tuple(adapters)

    def _capture_adapter_bus(self, ifname: str) -> Tuple[str, Optional[str]]:
        probe_id = f"sysfs.adapter.{_bounded_text(ifname, 64)}.device"
        if not _VALID_IFNAME_RE.match(ifname):
            self._add_error(
                probe_id,
                "parse",
                "interface name is unsafe for a sysfs lookup",
            )
            return "unknown", None

        path = f"/sys/class/net/{ifname}/device"
        started = self._clock.monotonic()
        missing = False
        permission_denied = False
        truncated = False
        target: Optional[str] = None
        try:
            target = self._sysfs_reader(path)
            if target is not None:
                target_text = str(target)
                target = _bounded_text(target_text, 1024)
                truncated = len(target_text) > len(target)
                if truncated:
                    self._add_error(
                        probe_id,
                        "truncated",
                        "adapter sysfs target exceeded the snapshot budget",
                    )
            if not target:
                missing = True
                self._add_error(probe_id, "missing", "adapter sysfs device link is absent")
        except PermissionError as exc:
            permission_denied = True
            self._add_error(probe_id, "permission", exc)
        except FileNotFoundError as exc:
            missing = True
            self._add_error(probe_id, "missing", exc)
        except Exception as exc:
            self._add_error(probe_id, "io", exc)
        completed = self._clock.monotonic()
        self._records.append(
            self._probe_record(
                probe_id=probe_id,
                source_kind="sysfs",
                source=(path,),
                started=started,
                completed=completed,
                exit_status=0 if target else None,
                timed_out=False,
                missing=missing,
                permission_denied=permission_denied,
                output_truncated=truncated,
            )
        )
        return _bus_from_sysfs_target(target), probe_id

    def _capture_service(
        self,
        probe_id: str,
        unit: str,
    ) -> Tuple[Optional[str], Optional[bool]]:
        systemctl = self._resolve_tool("systemctl")
        if not systemctl:
            self._missing_command(
                probe_id,
                ("systemctl", "is-active", unit),
                "systemctl",
            )
            return None, None
        capture = self._capture_command(
            probe_id,
            (systemctl, "is-active", unit),
            timeout_s=1.0,
        )
        if not capture.result.ok or capture.output_truncated:
            return None, None
        state = _parse_exact_status_line(capture.output)
        if state != "active":
            self._add_error(
                probe_id,
                "parse",
                f"systemctl returned an unrecognized state for {unit}",
                capture.result.exit_status,
            )
            return None, None
        return state, True

    def _capture_command(
        self,
        probe_id: str,
        argv: Sequence[str],
        *,
        timeout_s: float,
    ) -> _CommandCapture:
        started = self._clock.monotonic()
        try:
            result = host_probes.run_command(
                argv,
                timeout_s=timeout_s,
                runner=self._runner,
            )
        except Exception as exc:
            result = host_probes.CommandResult(
                argv=tuple(str(item) for item in argv),
                exit_status=127,
                error=f"{type(exc).__name__}: {exc}",
                exception=exc,
            )
        completed = self._clock.monotonic()
        raw_output = result.combined_output(include_error=False)
        output = raw_output[:MAX_CAPTURE_CHARS]
        truncated = len(raw_output) > len(output)
        self._records.append(
            self._probe_record(
                probe_id=probe_id,
                source_kind="command",
                source=tuple(result.argv),
                started=started,
                completed=completed,
                exit_status=result.exit_status,
                timed_out=result.timed_out,
                missing=result.missing,
                permission_denied=result.permission_denied,
                output_truncated=truncated,
            )
        )
        if result.timed_out:
            self._add_error(probe_id, "timeout", "read-only command timed out", result.exit_status)
        elif result.missing:
            self._add_error(probe_id, "missing", result.error or "command is missing", result.exit_status)
        elif result.permission_denied:
            self._add_error(
                probe_id,
                "permission",
                result.error or "command permission was denied",
                result.exit_status,
            )
        elif result.error:
            self._add_error(probe_id, "execution", result.error, result.exit_status)
        elif result.exit_status != 0:
            self._add_error(
                probe_id,
                "nonzero",
                f"read-only command exited with status {result.exit_status}",
                result.exit_status,
            )
        if truncated:
            self._add_error(
                probe_id,
                "truncated",
                f"command output exceeded {MAX_CAPTURE_CHARS} characters",
                result.exit_status,
            )
        return _CommandCapture(
            result=result,
            output=output,
            output_truncated=truncated,
        )

    def _missing_command(
        self,
        probe_id: str,
        argv: Sequence[str],
        tool: str,
    ) -> None:
        started = self._clock.monotonic()
        completed = self._clock.monotonic()
        self._records.append(
            self._probe_record(
                probe_id=probe_id,
                source_kind="command",
                source=tuple(str(item) for item in argv),
                started=started,
                completed=completed,
                exit_status=None,
                timed_out=False,
                missing=True,
                permission_denied=False,
                output_truncated=False,
            )
        )
        self._add_error(probe_id, "missing", f"required tool is unavailable: {tool}")

    def _resolve_tool(self, name: str) -> Optional[str]:
        if name in self._resolved_tools:
            return self._resolved_tools[name]
        probe_id = f"tool.{name}"
        started = self._clock.monotonic()
        resolution_failed = False
        try:
            value = self._executable_resolver(name)
            resolved = os.fspath(value) if value else None
        except Exception as exc:
            resolved = None
            resolution_failed = True
            self._add_error(
                probe_id,
                "io",
                f"executable resolution failed: {exc}",
            )
        completed = self._clock.monotonic()
        self._records.append(
            self._probe_record(
                probe_id=probe_id,
                source_kind="environment",
                source=(name,),
                started=started,
                completed=completed,
                exit_status=0 if resolved else None,
                timed_out=False,
                missing=resolved is None and not resolution_failed,
                permission_denied=False,
                output_truncated=False,
            )
        )
        self._resolved_tools[name] = resolved
        return resolved

    def _probe_record(
        self,
        *,
        probe_id: str,
        source_kind: str,
        source: Tuple[str, ...],
        started: float,
        completed: float,
        exit_status: Optional[int],
        timed_out: bool,
        missing: bool,
        permission_denied: bool,
        output_truncated: bool,
    ) -> ProbeRecord:
        return ProbeRecord(
            probe_id=_bounded_text(probe_id, 128),
            source_kind=source_kind,
            source=tuple(_bounded_text(item, 512) for item in source),
            started_offset_ms=_duration_ms(self._started_monotonic, started),
            completed_offset_ms=_duration_ms(self._started_monotonic, completed),
            exit_status=exit_status,
            timed_out=timed_out,
            missing=missing,
            permission_denied=permission_denied,
            output_truncated=output_truncated,
        )

    def _add_error(
        self,
        probe_id: str,
        kind: str,
        message: object,
        exit_status: Optional[int] = None,
    ) -> None:
        self._errors.append(
            ProbeError(
                probe_id=_bounded_text(probe_id, 128),
                kind=_bounded_text(kind, 32),
                message=_sanitized_error(message),
                exit_status=exit_status,
            )
        )


def _empty_phy_facts(phy: str, probe_id: str) -> IwPhyFacts:
    return IwPhyFacts(
        phy=phy,
        interface_modes_known=False,
        supported_interface_modes=(),
        supports_ap=None,
        supports_2ghz=None,
        supports_5ghz=None,
        supports_6ghz=None,
        supports_80mhz=None,
        supports_wifi6=None,
        supports_ap_managed_concurrency=None,
        frequencies=(),
        source_probe_id=probe_id,
    )


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return normalized.replace("+00:00", "Z")


def _duration_ms(started: float, completed: float) -> int:
    return max(0, int(round((completed - started) * 1000.0)))


def _bounded_text(value: object, limit: int) -> str:
    return str(value)[:limit]


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bounded_text(value: object, limit: int) -> Optional[str]:
    text = _optional_text(value)
    return _bounded_text(text, limit) if text is not None else None


def _sanitized_error(value: object) -> str:
    normalized = " ".join(str(value).replace("\x00", "").split())
    return normalized[:MAX_ERROR_MESSAGE_CHARS] or "unknown probe error"


def _first_output_line(output: str) -> Optional[str]:
    for raw in output.splitlines():
        line = raw.strip().lower()
        if line:
            return _bounded_text(line, 128)
    return None


def _parse_exact_status_line(output: str) -> Optional[str]:
    lines = [raw.strip().lower() for raw in output.splitlines() if raw.strip()]
    if len(lines) != 1:
        return None
    return _bounded_text(lines[0], 128)


def _parse_ufw_active(output: str) -> Optional[bool]:
    for raw in output.splitlines():
        label, separator, _value = raw.partition(":")
        if separator and label.strip().casefold() == "status":
            return host_probes.parse_ufw_status(output)
    return None


def _modes_support_ap(modes: Optional[List[str]]) -> Optional[bool]:
    if modes is None:
        return None
    for mode in modes:
        normalized = mode.upper()
        if (
            normalized == "AP"
            or normalized.startswith("AP/")
            or normalized.startswith("AP-")
        ):
            return True
    return False


def _country_or_none(value: object) -> Optional[str]:
    country = _optional_text(value)
    if not country or country.lower() == "unknown":
        return None
    return _bounded_text(country, 16)


def _bus_from_sysfs_target(target: Optional[str]) -> str:
    if not target:
        return "unknown"
    lowered = target.lower()
    if "/virtual/" in lowered:
        return "virtual"
    if "/usb" in lowered:
        return "usb"
    if "/pci" in lowered:
        return "pci"
    if "/sdio" in lowered:
        return "sdio"
    if "/platform" in lowered:
        return "platform"
    return "unknown"


def _package_manager_family(
    *,
    family: Optional[str],
    flavor: str,
    immutable: Optional[bool],
) -> Optional[str]:
    if flavor == "bazzite" or (flavor == "fedora_atomic" and immutable is True):
        return "rpm-ostree"
    if family == "arch":
        return "pacman"
    if family == "debian":
        return "apt"
    if family == "fedora":
        return "rpm-ostree" if immutable is True else "dnf"
    return None
