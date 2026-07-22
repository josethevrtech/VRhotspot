from typing import Optional

from vr_hotspotd import host_facts


def make_host_facts_snapshot(
    *,
    snapshot_id: str = "adapter-snapshot-test-1",
    operation_kind: str = "adapter-test",
    default_uplink_interface: Optional[str] = "enp4s0",
) -> host_facts.HostFactsSnapshot:
    """Return one internally consistent, known-good adapter snapshot fixture."""

    return host_facts.HostFactsSnapshot(
        schema_version=1,
        metadata=host_facts.SnapshotMetadata(
            snapshot_id=snapshot_id,
            operation_kind=operation_kind,
            source="test",
            started_at_utc="2026-07-22T12:00:00.000Z",
            completed_at_utc="2026-07-22T12:00:00.050Z",
            monotonic_duration_ms=50,
            builder_version="1",
        ),
        platform=host_facts.PlatformFacts(
            os_release=(host_facts.OsReleaseFact(key="id", value="ubuntu"),),
            os_id="ubuntu",
            os_name="Ubuntu",
            version_id="24.04",
            variant_id=None,
            id_like=("debian",),
            flavor="ubuntu_debian",
            family="debian",
            package_manager_family="apt",
            host_kind="mutable_linux",
            is_immutable=False,
            immutability_signals=(),
            source_probe_id="platform.os_release",
        ),
        default_uplink=host_facts.DefaultUplinkFacts(
            selected_interface=default_uplink_interface,
            routes=(
                host_facts.DefaultRouteFact(
                    interface=default_uplink_interface,
                    gateway="192.0.2.1",
                    metric=100,
                    protocol="dhcp",
                ),
            )
            if default_uplink_interface
            else (),
            source_probe_id="network.default_uplink",
        ),
        iw_dev=host_facts.IwDevFacts(
            interfaces=(
                host_facts.IwInterfaceFacts(
                    ifname="wlan1",
                    phy="phy1",
                    interface_type="managed",
                    ssid_present=False,
                ),
            ),
            source_probe_id="iw.dev",
        ),
        iw_phys=(
            host_facts.IwPhyFacts(
                phy="phy1",
                interface_modes_known=True,
                supported_interface_modes=("managed", "AP"),
                supports_ap=True,
                supports_2ghz=True,
                supports_5ghz=True,
                supports_6ghz=False,
                supports_80mhz=True,
                supports_wifi6=True,
                supports_ap_managed_concurrency=True,
                frequencies=(
                    host_facts.FrequencyFacts(
                        frequency_mhz=2412,
                        channel=1,
                        band="2.4ghz",
                        disabled=False,
                        no_ir=False,
                        dfs=False,
                    ),
                    host_facts.FrequencyFacts(
                        frequency_mhz=5180,
                        channel=36,
                        band="5ghz",
                        disabled=False,
                        no_ir=False,
                        dfs=False,
                    ),
                ),
                source_probe_id="iw.phy.phy1",
            ),
        ),
        regulatory=host_facts.RegulatoryFacts(
            global_country="US",
            global_raw_header="country US: DFS-FCC",
            phys=(
                host_facts.RegulatoryDomainFacts(
                    phy="phy1",
                    country="US",
                    source="self-managed",
                    raw_header="country US: DFS-FCC",
                ),
            ),
            source_probe_id="iw.regulatory",
        ),
        network_manager=host_facts.NetworkManagerFacts(
            binary_present=True,
            nmcli_present=True,
            nmcli_running=True,
            service_state="active",
            service_active=True,
            source_probe_ids=(
                "network_manager.nmcli",
                "network_manager.service",
            ),
        ),
        iwd=host_facts.IwdFacts(
            binary_present=False,
            iwctl_present=False,
            service_state=None,
            service_active=None,
            associated_interfaces=(),
            source_probe_ids=("iwd.service", "iw.dev"),
        ),
        firewall=host_facts.FirewallFacts(
            backends=(
                host_facts.FirewallBackendFacts(
                    name="firewalld",
                    tool_present=False,
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant=None,
                    source_probe_ids=("firewall.firewalld.functional",),
                ),
                host_facts.FirewallBackendFacts(
                    name="ufw",
                    tool_present=False,
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant=None,
                    source_probe_ids=("firewall.ufw.functional",),
                ),
                host_facts.FirewallBackendFacts(
                    name="nftables",
                    tool_present=True,
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant=None,
                    source_probe_ids=(),
                ),
                host_facts.FirewallBackendFacts(
                    name="iptables",
                    tool_present=True,
                    functional_active=None,
                    service_state=None,
                    service_active=None,
                    variant="iptables-nft",
                    source_probe_ids=("firewall.iptables.version",),
                ),
            ),
            selected_backend="nftables",
            rationale="nft_present",
        ),
        adapters=(
            host_facts.AdapterFacts(
                ifname="wlan1",
                phy="phy1",
                interface_type="managed",
                associated=False,
                bus="usb",
                supports_ap=True,
                supports_2ghz=True,
                supports_5ghz=True,
                supports_6ghz=False,
                supports_80mhz=True,
                supports_wifi6=True,
                regulatory_country="US",
                regulatory_source="self-managed",
                source_probe_ids=(
                    "iw.dev",
                    "iw.phy.phy1",
                    "iw.regulatory",
                    "sysfs.adapter.wlan1.device",
                ),
            ),
        ),
        probe_records=(
            host_facts.ProbeRecord(
                probe_id="network.default_uplink",
                source_kind="command",
                source=("/usr/sbin/ip", "route", "show", "default"),
                started_offset_ms=10,
                completed_offset_ms=11,
                exit_status=0,
                timed_out=False,
                missing=False,
                permission_denied=False,
                output_truncated=False,
            ),
        ),
        probe_errors=(),
    )
