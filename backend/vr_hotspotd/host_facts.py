"""Immutable internal models for one read-only host-facts collection window.

These types are the shared factual foundation consumed by preflight, adapter
inventory/readiness, and lifecycle selection. They contain facts and
provenance only; scoring, safety policy, host mutation, and public response
projection remain with their owning modules.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class SnapshotMetadata:
    snapshot_id: str
    operation_kind: str
    source: str
    started_at_utc: str
    completed_at_utc: str
    monotonic_duration_ms: int
    builder_version: str


@dataclass(frozen=True)
class ProbeRecord:
    probe_id: str
    source_kind: str
    source: Tuple[str, ...]
    started_offset_ms: int
    completed_offset_ms: int
    exit_status: Optional[int]
    timed_out: bool
    missing: bool
    permission_denied: bool
    output_truncated: bool


@dataclass(frozen=True)
class ProbeError:
    probe_id: str
    kind: str
    message: str
    exit_status: Optional[int]


@dataclass(frozen=True)
class OsReleaseFact:
    key: str
    value: str


@dataclass(frozen=True)
class PlatformFacts:
    os_release: Tuple[OsReleaseFact, ...]
    os_id: Optional[str]
    os_name: Optional[str]
    version_id: Optional[str]
    variant_id: Optional[str]
    id_like: Tuple[str, ...]
    flavor: str
    family: Optional[str]
    package_manager_family: Optional[str]
    host_kind: str
    is_immutable: Optional[bool]
    immutability_signals: Tuple[str, ...]
    source_probe_id: str


@dataclass(frozen=True)
class DefaultRouteFact:
    interface: Optional[str]
    gateway: Optional[str]
    metric: Optional[int]
    protocol: Optional[str]


@dataclass(frozen=True)
class DefaultUplinkFacts:
    selected_interface: Optional[str]
    routes: Tuple[DefaultRouteFact, ...]
    source_probe_id: str


@dataclass(frozen=True)
class IwInterfaceFacts:
    ifname: str
    phy: Optional[str]
    interface_type: Optional[str]
    ssid_present: Optional[bool]


@dataclass(frozen=True)
class IwDevFacts:
    interfaces: Tuple[IwInterfaceFacts, ...]
    source_probe_id: str


@dataclass(frozen=True)
class FrequencyFacts:
    frequency_mhz: int
    channel: int
    band: str
    disabled: bool
    no_ir: bool
    dfs: bool


@dataclass(frozen=True)
class IwPhyFacts:
    phy: str
    interface_modes_known: bool
    supported_interface_modes: Tuple[str, ...]
    supports_ap: Optional[bool]
    supports_2ghz: Optional[bool]
    supports_5ghz: Optional[bool]
    supports_6ghz: Optional[bool]
    supports_80mhz: Optional[bool]
    supports_wifi6: Optional[bool]
    supports_ap_managed_concurrency: Optional[bool]
    frequencies: Tuple[FrequencyFacts, ...]
    source_probe_id: str


@dataclass(frozen=True)
class RegulatoryDomainFacts:
    phy: str
    country: Optional[str]
    source: str
    raw_header: Optional[str]


@dataclass(frozen=True)
class RegulatoryFacts:
    global_country: Optional[str]
    global_raw_header: Optional[str]
    phys: Tuple[RegulatoryDomainFacts, ...]
    source_probe_id: str


@dataclass(frozen=True)
class NetworkManagerFacts:
    binary_present: bool
    nmcli_present: bool
    nmcli_running: Optional[bool]
    service_state: Optional[str]
    service_active: Optional[bool]
    source_probe_ids: Tuple[str, ...]


@dataclass(frozen=True)
class IwdFacts:
    binary_present: bool
    iwctl_present: bool
    service_state: Optional[str]
    service_active: Optional[bool]
    associated_interfaces: Tuple[str, ...]
    source_probe_ids: Tuple[str, ...]


@dataclass(frozen=True)
class FirewallBackendFacts:
    name: str
    tool_present: bool
    functional_active: Optional[bool]
    service_state: Optional[str]
    service_active: Optional[bool]
    variant: Optional[str]
    source_probe_ids: Tuple[str, ...]


@dataclass(frozen=True)
class FirewallFacts:
    backends: Tuple[FirewallBackendFacts, ...]
    selected_backend: str
    rationale: str


@dataclass(frozen=True)
class AdapterFacts:
    ifname: str
    phy: Optional[str]
    interface_type: Optional[str]
    associated: Optional[bool]
    bus: str
    supports_ap: Optional[bool]
    supports_2ghz: Optional[bool]
    supports_5ghz: Optional[bool]
    supports_6ghz: Optional[bool]
    supports_80mhz: Optional[bool]
    supports_wifi6: Optional[bool]
    regulatory_country: Optional[str]
    regulatory_source: Optional[str]
    source_probe_ids: Tuple[str, ...]


@dataclass(frozen=True)
class HostFactsSnapshot:
    schema_version: int
    metadata: SnapshotMetadata
    platform: PlatformFacts
    default_uplink: DefaultUplinkFacts
    iw_dev: IwDevFacts
    iw_phys: Tuple[IwPhyFacts, ...]
    regulatory: RegulatoryFacts
    network_manager: NetworkManagerFacts
    iwd: IwdFacts
    firewall: FirewallFacts
    adapters: Tuple[AdapterFacts, ...]
    probe_records: Tuple[ProbeRecord, ...]
    probe_errors: Tuple[ProbeError, ...]

    def to_dict(self) -> Dict[str, Any]:
        """Return a recursively JSON-serializable copy of the snapshot."""

        value = _to_serializable(self)
        if not isinstance(value, dict):  # pragma: no cover - defensive typing guard
            raise TypeError("snapshot serialization did not produce a dictionary")
        return value


def _to_serializable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            item.name: _to_serializable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, tuple):
        return [_to_serializable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")
