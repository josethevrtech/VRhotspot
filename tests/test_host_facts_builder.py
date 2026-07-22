import json
import subprocess
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from vr_hotspotd.host_facts_builder import (
    MAX_CAPTURE_CHARS,
    MAX_ERROR_MESSAGE_CHARS,
    HostFactsSnapshotBuilder,
)


IW_DEV_OUTPUT = """
phy#0
    Interface wlan0
        ifindex 3
        type managed
        ssid Upstream Network Must Stay Private
phy#1
    Interface wlan1
        ifindex 7
        type managed
"""

IW_PHY0_OUTPUT = """
Wiphy phy0
  Supported interface modes:
     * managed
     * AP
  Band 1:
    Frequencies:
      * 2412.0 MHz [1] (22.0 dBm)
"""

IW_PHY1_OUTPUT = """
Wiphy phy1
  Supported interface modes:
     * managed
     * AP/VLAN
  valid interface combinations:
     * #{ managed } <= 1, #{ AP, P2P-client } <= 1,
       total <= 2, #channels <= 1
  Band 1:
    Frequencies:
      * 2412.0 MHz [1] (22.0 dBm)
  Band 2:
    HE Iftypes: AP, managed
    HE40/HE80/5GHz
    Frequencies:
      * 5180.0 MHz [36] (23.0 dBm)
      * 5200.0 MHz [40] (no IR)
"""

IW_REG_OUTPUT = """
global
country US: DFS-FCC
phy#0
country US: DFS-FCC
phy#1 (self-managed)
country CA: DFS-FCC
"""

TOOL_PATHS = {
    "iw": "/usr/sbin/iw",
    "ip": "/usr/sbin/ip",
    "nmcli": "/usr/bin/nmcli",
    "NetworkManager": "/usr/sbin/NetworkManager",
    "systemctl": "/usr/bin/systemctl",
    "iwd": "/usr/lib/iwd",
    "iwctl": "/usr/bin/iwctl",
    "firewall-cmd": "/usr/bin/firewall-cmd",
    "ufw": "/usr/sbin/ufw",
    "nft": "/usr/sbin/nft",
    "iptables": "/usr/sbin/iptables",
}

EXPECTED_COMMANDS = [
    ("/usr/sbin/iw", "dev"),
    ("/usr/sbin/iw", "phy", "phy0", "info"),
    ("/usr/sbin/iw", "phy", "phy1", "info"),
    ("/usr/sbin/iw", "reg", "get"),
    ("/usr/sbin/ip", "route", "show", "default"),
    ("/usr/bin/nmcli", "-t", "-f", "RUNNING", "g"),
    ("/usr/bin/systemctl", "is-active", "NetworkManager"),
    ("/usr/bin/systemctl", "is-active", "iwd"),
    ("/usr/bin/firewall-cmd", "--state"),
    ("/usr/bin/systemctl", "is-active", "firewalld"),
    ("/usr/sbin/ufw", "status"),
    ("/usr/bin/systemctl", "is-active", "ufw"),
    ("/usr/sbin/iptables", "--version"),
]


class FakeClock:
    def __init__(self):
        self._monotonic = 100.0
        self._utc = datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc)

    def monotonic(self):
        value = self._monotonic
        self._monotonic += 0.001
        return value

    def utc_now(self):
        value = self._utc
        self._utc += timedelta(seconds=1)
        return value


class FakeRunner:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        key = tuple(argv)
        self.calls.append((key, dict(kwargs)))
        response = self.responses[key]
        if isinstance(response, BaseException):
            raise response
        returncode, stdout, stderr = response
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _responses():
    return {
        ("/usr/sbin/iw", "dev"): (0, IW_DEV_OUTPUT, ""),
        ("/usr/sbin/iw", "phy", "phy0", "info"): (0, IW_PHY0_OUTPUT, ""),
        ("/usr/sbin/iw", "phy", "phy1", "info"): (0, IW_PHY1_OUTPUT, ""),
        ("/usr/sbin/iw", "reg", "get"): (0, IW_REG_OUTPUT, ""),
        ("/usr/sbin/ip", "route", "show", "default"): (
            0,
            "\n".join(
                [
                    "default via 192.0.2.1 dev enp4s0 proto dhcp metric 100",
                    "default via 198.51.100.1 dev wlan0 proto dhcp metric 600",
                ]
            ),
            "",
        ),
        ("/usr/bin/nmcli", "-t", "-f", "RUNNING", "g"): (0, "running\n", ""),
        ("/usr/bin/systemctl", "is-active", "NetworkManager"): (0, "active\n", ""),
        ("/usr/bin/systemctl", "is-active", "iwd"): (0, "active\n", ""),
        ("/usr/bin/firewall-cmd", "--state"): (0, "running\n", ""),
        ("/usr/bin/systemctl", "is-active", "firewalld"): (0, "active\n", ""),
        ("/usr/sbin/ufw", "status"): (0, "Status: active\n", ""),
        ("/usr/bin/systemctl", "is-active", "ufw"): (0, "active\n", ""),
        ("/usr/sbin/iptables", "--version"): (
            0,
            "iptables v1.8.10 (nf_tables)\n",
            "",
        ),
    }


def _build_snapshot(*, response_overrides=None, tool_paths=None):
    responses = _responses()
    responses.update(response_overrides or {})
    runner = FakeRunner(responses)
    paths = TOOL_PATHS if tool_paths is None else tool_paths
    sysfs_targets = {
        "/sys/class/net/wlan0/device": "/devices/pci0000:00/0000:00:14.3",
        "/sys/class/net/wlan1/device": (
            "/devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2:1.0"
        ),
    }
    builder = HostFactsSnapshotBuilder(
        runner=runner,
        executable_resolver=lambda name: paths.get(name),
        os_release_reader=lambda: {
            "id": "ubuntu",
            "id_like": "debian",
            "pretty_name": "Ubuntu 24.04 LTS",
            "version_id": "24.04",
        },
        sysfs_reader=lambda path: sysfs_targets.get(path),
        clock=FakeClock(),
        snapshot_id_factory=lambda: "snapshot-test-1",
    )
    return builder.build(operation_kind="unit-test"), runner


def _firewall_backend(snapshot, name):
    return next(item for item in snapshot.firewall.backends if item.name == name)


def _probe_record(snapshot, probe_id):
    return next(item for item in snapshot.probe_records if item.probe_id == probe_id)


def _probe_error(snapshot, probe_id, kind):
    return next(
        item
        for item in snapshot.probe_errors
        if item.probe_id == probe_id and item.kind == kind
    )


def test_builder_collects_serializable_immutable_snapshot_with_fake_runner():
    snapshot, runner = _build_snapshot()

    assert snapshot.schema_version == 1
    assert snapshot.metadata.snapshot_id == "snapshot-test-1"
    assert snapshot.metadata.operation_kind == "unit-test"
    assert snapshot.metadata.source == "host_facts_builder"
    assert snapshot.metadata.started_at_utc == "2026-07-21T16:00:00.000Z"
    assert snapshot.metadata.completed_at_utc == "2026-07-21T16:00:01.000Z"
    assert snapshot.metadata.monotonic_duration_ms > 0

    assert snapshot.platform.os_id == "ubuntu"
    assert snapshot.platform.family == "debian"
    assert snapshot.platform.package_manager_family == "apt"
    assert snapshot.platform.host_kind == "mutable_linux"
    assert snapshot.platform.is_immutable is False

    assert snapshot.default_uplink.selected_interface == "enp4s0"
    assert [item.interface for item in snapshot.default_uplink.routes] == [
        "enp4s0",
        "wlan0",
    ]
    assert [(item.ifname, item.phy, item.ssid_present) for item in snapshot.iw_dev.interfaces] == [
        ("wlan0", "phy0", True),
        ("wlan1", "phy1", False),
    ]

    phys = {item.phy: item for item in snapshot.iw_phys}
    assert phys["phy0"].supports_2ghz is True
    assert phys["phy1"].supports_ap is True
    assert phys["phy1"].supports_5ghz is True
    assert phys["phy1"].supports_80mhz is True
    assert phys["phy1"].supports_wifi6 is True
    assert phys["phy1"].supports_ap_managed_concurrency is True

    assert snapshot.regulatory.global_country == "US"
    assert {item.phy: item.country for item in snapshot.regulatory.phys} == {
        "phy0": "US",
        "phy1": "CA",
    }
    assert snapshot.network_manager.nmcli_running is True
    assert snapshot.network_manager.service_active is True
    assert snapshot.iwd.service_active is True
    assert snapshot.iwd.associated_interfaces == ("wlan0",)
    assert snapshot.firewall.selected_backend == "firewalld"
    assert snapshot.firewall.rationale == "firewalld_running"

    adapters = {item.ifname: item for item in snapshot.adapters}
    assert adapters["wlan0"].bus == "pci"
    assert adapters["wlan1"].bus == "usb"
    assert adapters["wlan1"].regulatory_country == "CA"
    assert snapshot.probe_errors == ()

    serialized = snapshot.to_dict()
    encoded = json.dumps(serialized, sort_keys=True)
    assert serialized["schema_version"] == 1
    assert "Upstream Network Must Stay Private" not in encoded

    with pytest.raises(FrozenInstanceError):
        snapshot.schema_version = 2
    with pytest.raises(FrozenInstanceError):
        snapshot.metadata.operation_kind = "changed"
    assert isinstance(snapshot.adapters, tuple)

    assert [argv for argv, _kwargs in runner.calls] == EXPECTED_COMMANDS
    for _argv, kwargs in runner.calls:
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] > 0
        assert "shell" not in kwargs


def test_partial_command_failures_remain_data_and_keep_successful_siblings():
    phy1_command = ("/usr/sbin/iw", "phy", "phy1", "info")
    ufw_command = ("/usr/sbin/ufw", "status")
    iwd_service = ("/usr/bin/systemctl", "is-active", "iwd")
    snapshot, _runner = _build_snapshot(
        response_overrides={
            phy1_command: subprocess.TimeoutExpired(
                cmd=list(phy1_command),
                timeout=4.0,
                output="partial wireless output",
            ),
            ufw_command: PermissionError("permission denied by fake runner"),
            iwd_service: (3, "inactive\n", ""),
        }
    )

    errors = {(item.probe_id, item.kind) for item in snapshot.probe_errors}
    assert ("iw.phy.phy1", "timeout") in errors
    assert ("firewall.ufw.functional", "permission") in errors
    assert ("iwd.service", "nonzero") in errors

    phys = {item.phy: item for item in snapshot.iw_phys}
    assert phys["phy0"].supports_2ghz is True
    assert phys["phy1"].supports_ap is None
    assert phys["phy1"].frequencies == ()
    assert snapshot.iwd.service_state is None
    assert snapshot.iwd.service_active is None
    assert snapshot.default_uplink.selected_interface == "enp4s0"

    records = {item.probe_id: item for item in snapshot.probe_records}
    assert records["iw.phy.phy1"].timed_out is True
    assert records["firewall.ufw.functional"].permission_denied is True


def test_systemctl_nonzero_exit_does_not_produce_a_confident_service_status():
    probe_id = "network_manager.service"
    command = ("/usr/bin/systemctl", "is-active", "NetworkManager")
    snapshot, _runner = _build_snapshot(
        response_overrides={command: (3, "inactive\n", "unit is not active\n")}
    )

    assert snapshot.network_manager.service_state is None
    assert snapshot.network_manager.service_active is None
    assert snapshot.network_manager.nmcli_running is True
    assert snapshot.default_uplink.selected_interface == "enp4s0"

    error = _probe_error(snapshot, probe_id, "nonzero")
    record = _probe_record(snapshot, probe_id)
    assert error.exit_status == 3
    assert record.exit_status == 3
    assert record.timed_out is False


@pytest.mark.parametrize(
    "malformed_output",
    (
        "inactive\n",
        "active\nunexpected diagnostic\n",
        "Failed to connect to bus: malformed fake response\n",
    ),
)
def test_systemctl_malformed_output_keeps_service_status_indeterminate(
    malformed_output,
):
    probe_id = "iwd.service"
    command = ("/usr/bin/systemctl", "is-active", "iwd")
    snapshot, _runner = _build_snapshot(
        response_overrides={command: (0, malformed_output, "")}
    )

    assert snapshot.iwd.service_state is None
    assert snapshot.iwd.service_active is None
    assert snapshot.network_manager.service_active is True
    assert snapshot.default_uplink.selected_interface == "enp4s0"

    error = _probe_error(snapshot, probe_id, "parse")
    record = _probe_record(snapshot, probe_id)
    assert error.exit_status == 0
    assert record.exit_status == 0


def test_missing_systemctl_is_a_probe_error_and_all_service_statuses_are_unknown():
    tool_paths = dict(TOOL_PATHS)
    tool_paths.pop("systemctl")
    snapshot, runner = _build_snapshot(tool_paths=tool_paths)

    assert snapshot.network_manager.service_state is None
    assert snapshot.network_manager.service_active is None
    assert snapshot.iwd.service_state is None
    assert snapshot.iwd.service_active is None
    assert all(
        backend.service_state is None and backend.service_active is None
        for backend in snapshot.firewall.backends
    )
    assert snapshot.network_manager.nmcli_running is True
    assert _firewall_backend(snapshot, "firewalld").functional_active is True

    service_probe_ids = {
        "network_manager.service",
        "iwd.service",
        "firewall.firewalld.service",
        "firewall.ufw.service",
    }
    errors = {(item.probe_id, item.kind) for item in snapshot.probe_errors}
    assert {(probe_id, "missing") for probe_id in service_probe_ids} <= errors
    assert all(_probe_record(snapshot, probe_id).missing for probe_id in service_probe_ids)
    assert all("systemctl" not in command[0] for command, _kwargs in runner.calls)


def test_firewall_cmd_nonzero_exit_does_not_produce_a_confident_functional_status():
    probe_id = "firewall.firewalld.functional"
    command = ("/usr/bin/firewall-cmd", "--state")
    snapshot, _runner = _build_snapshot(
        response_overrides={
            command: (2, "running\n", "failed fake firewalld status probe\n")
        }
    )

    firewalld = _firewall_backend(snapshot, "firewalld")
    ufw = _firewall_backend(snapshot, "ufw")
    assert firewalld.tool_present is True
    assert firewalld.functional_active is None
    assert firewalld.service_active is True
    assert ufw.functional_active is True
    assert snapshot.firewall.selected_backend == "ufw"
    assert snapshot.default_uplink.selected_interface == "enp4s0"

    error = _probe_error(snapshot, probe_id, "nonzero")
    record = _probe_record(snapshot, probe_id)
    assert error.exit_status == 2
    assert record.exit_status == 2


def test_firewall_cmd_malformed_output_keeps_functional_status_indeterminate():
    probe_id = "firewall.firewalld.functional"
    command = ("/usr/bin/firewall-cmd", "--state")
    snapshot, _runner = _build_snapshot(
        response_overrides={command: (0, "running\nunexpected diagnostic\n", "")}
    )

    firewalld = _firewall_backend(snapshot, "firewalld")
    assert firewalld.functional_active is None
    assert firewalld.service_active is True
    assert _firewall_backend(snapshot, "ufw").functional_active is True
    assert snapshot.firewall.selected_backend == "ufw"
    assert snapshot.network_manager.nmcli_running is True

    error = _probe_error(snapshot, probe_id, "parse")
    record = _probe_record(snapshot, probe_id)
    assert error.exit_status == 0
    assert record.exit_status == 0


def test_missing_firewall_cmd_is_a_probe_error_and_functional_status_is_unknown():
    tool_paths = dict(TOOL_PATHS)
    tool_paths.pop("firewall-cmd")
    probe_id = "firewall.firewalld.functional"
    snapshot, runner = _build_snapshot(tool_paths=tool_paths)

    firewalld = _firewall_backend(snapshot, "firewalld")
    assert firewalld.tool_present is False
    assert firewalld.functional_active is None
    assert firewalld.service_active is True
    assert _probe_error(snapshot, probe_id, "missing").exit_status is None
    record = _probe_record(snapshot, probe_id)
    assert record.missing is True
    assert record.exit_status is None
    assert all("firewall-cmd" not in command[0] for command, _kwargs in runner.calls)


def test_timeout_partial_status_output_is_not_promoted_to_service_or_firewall_facts():
    iwd_command = ("/usr/bin/systemctl", "is-active", "iwd")
    firewalld_command = ("/usr/bin/firewall-cmd", "--state")
    snapshot, _runner = _build_snapshot(
        response_overrides={
            iwd_command: subprocess.TimeoutExpired(
                cmd=list(iwd_command),
                timeout=1.0,
                output="active\n",
            ),
            firewalld_command: subprocess.TimeoutExpired(
                cmd=list(firewalld_command),
                timeout=1.0,
                output="running\n",
            ),
        }
    )

    assert snapshot.iwd.service_state is None
    assert snapshot.iwd.service_active is None
    assert _firewall_backend(snapshot, "firewalld").functional_active is None
    assert snapshot.network_manager.service_active is True
    assert _firewall_backend(snapshot, "ufw").functional_active is True
    assert snapshot.default_uplink.selected_interface == "enp4s0"

    for probe_id in ("iwd.service", "firewall.firewalld.functional"):
        assert _probe_error(snapshot, probe_id, "timeout").exit_status == 124
        assert _probe_record(snapshot, probe_id).timed_out is True


def test_failed_status_probe_evidence_is_bounded_and_sanitized():
    probe_id = "firewall.firewalld.functional"
    command = ("/usr/bin/firewall-cmd", "--state")
    unsafe_message = "fake permission denied\x00\n\t" + ("x" * 500)
    snapshot, _runner = _build_snapshot(
        response_overrides={command: PermissionError(unsafe_message)}
    )

    assert _firewall_backend(snapshot, "firewalld").functional_active is None
    assert snapshot.network_manager.nmcli_running is True

    error = _probe_error(snapshot, probe_id, "permission")
    record = _probe_record(snapshot, probe_id)
    assert 0 < len(error.message) <= MAX_ERROR_MESSAGE_CHARS
    assert "\x00" not in error.message
    assert "\n" not in error.message
    assert "\t" not in error.message
    assert record.permission_denied is True
    assert record.source == command
    assert all(len(item) <= 512 for item in record.source)


def test_missing_tools_are_probe_errors_and_never_call_the_runner():
    snapshot, runner = _build_snapshot(tool_paths={})

    assert runner.calls == []
    assert snapshot.iw_dev.interfaces == ()
    assert snapshot.iw_phys == ()
    assert snapshot.default_uplink.selected_interface is None
    assert snapshot.network_manager.nmcli_running is None
    assert snapshot.iwd.service_active is None
    assert snapshot.firewall.selected_backend == "unknown"

    errors = {(item.probe_id, item.kind) for item in snapshot.probe_errors}
    assert ("iw.dev", "missing") in errors
    assert ("iw.regulatory", "missing") in errors
    assert ("network.default_uplink", "missing") in errors
    assert ("network_manager.nmcli", "missing") in errors
    assert ("network_manager.service", "missing") in errors
    assert ("iwd.service", "missing") in errors
    assert ("firewall.firewalld.functional", "missing") in errors
    assert ("firewall.ufw.functional", "missing") in errors
    assert ("firewall.iptables.version", "missing") in errors
    assert all(item.missing for item in snapshot.probe_records if item.probe_id in {
        "iw.dev",
        "iw.regulatory",
        "network.default_uplink",
    })


def test_malformed_and_truncated_inputs_are_explicit_probe_errors():
    phy0_command = ("/usr/sbin/iw", "phy", "phy0", "info")
    reg_command = ("/usr/sbin/iw", "reg", "get")
    snapshot, _runner = _build_snapshot(
        response_overrides={
            phy0_command: (
                0,
                IW_PHY0_OUTPUT + ("x" * (MAX_CAPTURE_CHARS + 128)),
                "",
            ),
            reg_command: (0, "localized or malformed regulatory output\n", ""),
        }
    )

    errors = {(item.probe_id, item.kind) for item in snapshot.probe_errors}
    assert ("iw.phy.phy0", "truncated") in errors
    assert ("iw.regulatory", "parse") in errors

    records = {item.probe_id: item for item in snapshot.probe_records}
    assert records["iw.phy.phy0"].output_truncated is True
    assert snapshot.regulatory.global_country is None
    assert {item.phy for item in snapshot.iw_phys} == {"phy0", "phy1"}
    assert snapshot.default_uplink.selected_interface == "enp4s0"


def test_malformed_phy_output_produces_unknown_capabilities_not_false_facts():
    phy1_command = ("/usr/sbin/iw", "phy", "phy1", "info")
    snapshot, _runner = _build_snapshot(
        response_overrides={phy1_command: (0, "malformed phy output\n", "")}
    )

    phy1 = next(item for item in snapshot.iw_phys if item.phy == "phy1")
    assert phy1.supports_ap is None
    assert phy1.supports_2ghz is None
    assert phy1.supports_5ghz is None
    assert phy1.supports_6ghz is None
    assert phy1.supports_80mhz is None
    assert phy1.supports_wifi6 is None
    assert phy1.frequencies == ()
    assert ("iw.phy.phy1", "parse") in {
        (item.probe_id, item.kind) for item in snapshot.probe_errors
    }
