import json
from collections import deque

import pytest

from vr_hotspotd import config as config_module
from vr_hotspotd import host_probes
from vr_hotspotd.diagnostics import preflight_report
from vr_hotspotd.policy import ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK


PLATFORM = {
    "os": {
        "pretty_name": "Ubuntu 24.04 LTS",
        "id": "ubuntu",
        "version_id": "24.04",
        "variant_id": "",
        "id_like": ["debian"],
    },
    "immutability": {
        "is_immutable": False,
        "signal": "unknown",
        "writable_paths": {},
    },
    "integration": {
        "network_manager": {
            "present": True,
            "active": True,
            "nmcli": True,
        }
    },
}

FIREWALL = {
    "firewalld": {"available": False, "active": False},
    "ufw": {"available": False, "active": False},
    "nftables": {"available": True},
    "iptables": {"available": True, "variant": "iptables-nft"},
    "selected_backend": "nftables",
    "rationale": "nft_present",
}

BINARIES = {
    "hostapd": {
        "available": True,
        "source": "system",
        "path": "/usr/sbin/hostapd",
        "version": "2.11",
        "capabilities": {"sae": True, "he": True},
        "probe_error": None,
    },
    "dnsmasq": {
        "available": True,
        "source": "system",
        "path": "/usr/sbin/dnsmasq",
        "version": "2.90",
        "capabilities": {},
        "probe_error": None,
    },
    "selection_error": None,
}

INVENTORY = {
    "recommended": "wlan1",
    "global_regdom": {"country": "US"},
    "adapters": [
        {
            "ifname": "wlan1",
            "phy": "phy1",
            "bus": "usb",
            "supports_ap": True,
            "supports_2ghz": True,
            "supports_5ghz": True,
            "supports_6ghz": False,
            "supports_80mhz": True,
            "supports_wifi6": True,
            "regdom": {
                "country": "US",
                "global_country": "US",
                "source": "kernel-managed",
            },
        }
    ],
}

READINESS = {
    "recommended": "wlan1",
    "basic_mode_recommended": "wlan1",
    "adapters": [
        {
            "interface": "wlan1",
            "readiness_state": "good_for_vr",
            "recommendation_score": 79,
            "reason_codes": [
                "supports_ap_mode",
                "supports_5ghz",
                "supports_80mhz",
            ],
            "explanation": "wlan1 supports AP mode, 5 GHz, and 80 MHz channels.",
        }
    ],
}


def _build(**overrides):
    values = {
        "platform_matrix": PLATFORM,
        "firewall": FIREWALL,
        "network_manager": {"nmcli": True, "running": True},
        "iwd": {
            "present": False,
            "active": False,
            "status": "not_installed",
            "iwctl": False,
        },
        "binaries": BINARIES,
        "inventory": INVENTORY,
        "readiness": READINESS,
        "active_uplink_interface": "enp4s0",
        "concurrency_by_phy": {"phy1": True},
        "existing_preflight": {"errors": [], "warnings": [], "details": {}},
        "config": {
            "band_preference": "5ghz",
            "channel_width": "80",
            "allow_fallback_40mhz": False,
            "enable_internet": True,
        },
    }
    values.update(overrides)
    return preflight_report.build_preflight_report(**values)


def test_ready_report_normalizes_existing_probe_results():
    report = _build()

    assert report["schema_version"] == 1
    assert report["overall_readiness"] == "ready"
    assert report["platform"]["os_family"] == "debian"
    assert report["platform"]["package_manager_family"] == "apt"
    assert report["platform"]["host_kind"] == "mutable_linux"
    assert report["platform"]["is_steamos"] is False
    assert report["platform"]["os_id"] == "ubuntu"
    assert "os" not in report["platform"]
    assert report["firewall"]["backend"] == "nftables"
    assert report["firewall"]["status"] == "available"
    assert "backends" not in report["firewall"]
    assert report["services"]["network_manager"]["status"] == "active"
    assert report["services"]["iwd"]["status"] == "not_installed"
    assert report["binaries"]["hostapd"]["version"] == "2.11"
    assert report["binaries"]["dnsmasq"]["version"] == "2.90"
    assert report["network"]["active_uplink_interface"] == "enp4s0"
    assert report["wifi"]["selected_adapter"] == "wlan1"
    assert report["wifi"]["selected_adapter_capabilities"] == {
        "ap_mode": True,
        "supports_2ghz": True,
        "supports_5ghz": True,
        "supports_6ghz": False,
        "supports_80mhz": True,
        "supports_wifi6_he": True,
        "supports_sta_ap_concurrency": True,
    }
    assert report["issues"] == []
    assert report["recommended_actions"] == []
    assert report["target_configuration"]["channel_width"] == "80"
    assert "channel_width_mhz" not in report["target_configuration"]
    assert report["evidence"]["stability"] == "debug"
    assert "checks" not in report
    json.dumps(report)


def test_report_distinguishes_steamos_from_mutable_linux():
    steamos_platform = {
        **PLATFORM,
        "os": {
            "pretty_name": "SteamOS 3",
            "id": "steamos",
            "version_id": "3",
            "variant_id": "steamdeck",
            "id_like": ["arch"],
        },
        "immutability": {
            "is_immutable": True,
            "signal": "steamos-readonly",
            "writable_paths": {},
        },
    }

    report = _build(platform_matrix=steamos_platform)

    assert report["platform"]["os_family"] == "arch"
    assert report["platform"]["package_manager_family"] == "pacman"
    assert report["platform"]["host_kind"] == "steamos"
    assert report["platform"]["is_steamos"] is True
    assert report["platform"]["is_mutable_linux"] is False


def test_report_blocks_on_missing_required_host_facts_with_human_actions():
    report = _build(
        binaries={
            "hostapd": {"available": False},
            "dnsmasq": {"available": False},
            "selection_error": "binary_missing",
        },
        inventory={
            "error": "iw_not_found",
            "adapters": [],
            "recommended": None,
        },
        readiness={"adapters": [], "recommended": None},
        active_uplink_interface=None,
        concurrency_by_phy={},
        existing_preflight={
            "errors": ["rfkill_blocked"],
            "warnings": ["regdom_unknown_or_global_00"],
            "details": {},
        },
    )

    assert report["overall_readiness"] == "blocked"
    issues = {item["code"]: item for item in report["issues"]}
    assert issues["adapter_inventory_unavailable"]["severity"] == "blocked"
    assert "Wi-Fi adapter inventory" in issues["adapter_inventory_unavailable"]["message"]
    assert issues["no_wifi_adapter"]["severity"] == "blocked"
    assert issues["hostapd_not_available"]["severity"] == "blocked"
    assert issues["dnsmasq_not_available"]["severity"] == "blocked"
    assert issues["rfkill_blocked"]["message"] == "A Wi-Fi radio is blocked by rfkill."
    assert all(item["message"] for item in report["recommended_actions"])


def test_report_blocks_when_inventory_has_no_ap_capable_adapter():
    inventory = {
        "recommended": None,
        "adapters": [
            {
                "ifname": "wlan0",
                "phy": "phy0",
                "bus": "pci",
                "supports_ap": False,
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            },
            {
                "ifname": "wlan1",
                "phy": "phy1",
                "bus": "usb",
                "supports_ap": False,
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            },
        ],
    }
    readiness = {
        "recommended": None,
        "basic_mode_recommended": None,
        "adapters": [
            {
                "interface": "wlan0",
                "readiness_state": "unsupported",
                "basic_mode_visibility": {"selectable": False},
            },
            {
                "interface": "wlan1",
                "readiness_state": "unsupported",
                "basic_mode_visibility": {"selectable": False},
            },
        ],
    }

    report = _build(
        inventory=inventory,
        readiness=readiness,
        concurrency_by_phy={"phy0": None, "phy1": None},
    )

    assert report["wifi"]["selected_adapter"] is None
    assert report["overall_readiness"] == "blocked"
    issue = next(item for item in report["issues"] if item["code"] == "no_ap_capable_adapter")
    assert issue["severity"] == "blocked"
    assert issue["context"] == {
        "adapter_count": 2,
        "interfaces": ["wlan0", "wlan1"],
    }


def test_report_normalizes_parameterized_issue_code_and_separates_context():
    report = _build(
        existing_preflight={
            "errors": [],
            "warnings": ["regdom_mismatch(adapter=CA,config=US)"],
            "details": {
                "regdom": {
                    "adapter_country": "CA",
                    "cfg_country": "US",
                    "global_country": "US",
                }
            },
        }
    )

    issue = next(item for item in report["issues"] if item["code"] == "regdom_mismatch")
    assert issue["context"] == {
        "adapter_country": "CA",
        "configured_country": "US",
        "global_country": "US",
    }
    assert all("(" not in item["code"] and ":" not in item["code"] for item in report["issues"])


@pytest.mark.parametrize(
    "config",
    (
        {
            "band_preference": "5ghz",
            "channel_width": "80",
            "allow_fallback_40mhz": False,
            "enable_internet": True,
        },
        {
            "ap_adapter": "wlan1",
            "band_preference": "5ghz",
            "channel_width": "80",
            "allow_fallback_40mhz": False,
            "enable_internet": True,
        },
    ),
    ids=("recommended_adapter", "configured_adapter"),
)
def test_same_radio_uplink_is_blocking_even_with_concurrency_evidence(config):
    report = _build(active_uplink_interface="wlan1", config=config)

    assert report["overall_readiness"] == "blocked"
    issue = next(
        item
        for item in report["issues"]
        if item["code"] == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    )
    assert issue["severity"] == "blocked"
    assert "does not yet support" in issue["message"]
    action = next(
        item
        for item in report["recommended_actions"]
        if item["code"] == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    )
    assert "separate Wi-Fi adapter" in action["message"]
    assert "Ethernet or another interface" in action["message"]
    assert report["wifi"]["adapters"][0]["is_active_uplink"] is True
    assert (
        report["wifi"]["adapters"][0]["capabilities"]["supports_sta_ap_concurrency"]
        is True
    )


@pytest.mark.parametrize(
    ("active_uplink_interface", "expected_readiness"),
    (
        (None, "warning"),
        ("enp4s0", "ready"),
        ("wlan0", "ready"),
    ),
    ids=("no_uplink", "separate_ethernet_uplink", "separate_wifi_uplink"),
)
def test_non_conflicting_uplink_does_not_trigger_role_guard(
    active_uplink_interface,
    expected_readiness,
):
    report = _build(active_uplink_interface=active_uplink_interface)

    assert report["overall_readiness"] == expected_readiness
    assert ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK not in {
        item["code"] for item in report["issues"]
    }


def test_collector_uses_mocked_read_only_probe_results(monkeypatch):
    calls = []

    monkeypatch.setattr(
        preflight_report,
        "collect_platform_matrix",
        lambda: calls.append("platform") or PLATFORM,
    )
    monkeypatch.setattr(
        preflight_report.host_probes,
        "probe_firewall_backends",
        lambda: calls.append("firewall") or FIREWALL,
    )
    monkeypatch.setattr(
        preflight_report.host_probes,
        "probe_network_manager",
        lambda: calls.append("network_manager")
        or {"nmcli": True, "running": True},
    )
    monkeypatch.setattr(
        preflight_report.host_probes,
        "probe_iwd",
        lambda: calls.append("iwd")
        or {"present": False, "active": False, "status": "not_installed"},
    )
    monkeypatch.setattr(
        preflight_report.supervisor,
        "inspect_runtime_binaries",
        lambda: calls.append("binaries")
        or {
            "hostapd": "/usr/sbin/hostapd",
            "dnsmasq": "/usr/sbin/dnsmasq",
            "selection_error": None,
            "probe_environment": {},
        },
    )

    def fail_engine_env(*_args, **_kwargs):
        raise AssertionError("diagnostics called private lifecycle engine setup")

    monkeypatch.setattr(
        preflight_report.supervisor,
        "_build_engine_env",
        fail_engine_env,
    )
    monkeypatch.setattr(
        preflight_report.preflight,
        "probe_hostapd_capabilities",
        lambda _path: {
            "sae": True,
            "he": True,
            "raw": "hostapd v2.11\nSAE\nIEEE 802.11ax",
        },
    )
    monkeypatch.setattr(
        preflight_report.host_probes,
        "run_command",
        lambda argv, **_kwargs: host_probes.CommandResult(
            argv=tuple(argv),
            exit_status=0,
            stdout="Dnsmasq version 2.90\n",
        ),
    )
    monkeypatch.setattr(
        preflight_report.supervisor,
        "_stderr_tail",
        deque(["existing supervisor stderr"]),
    )
    stderr_before = preflight_report.supervisor.get_tails()[1]
    monkeypatch.setattr(
        preflight_report,
        "get_adapters",
        lambda: calls.append("inventory") or INVENTORY,
    )
    monkeypatch.setattr(
        preflight_report,
        "build_readiness_model",
        lambda inventory: calls.append(("readiness", inventory)) or READINESS,
    )
    monkeypatch.setattr(
        preflight_report.host_probes,
        "probe_default_uplink",
        lambda: calls.append("uplink") or "enp4s0",
    )
    monkeypatch.setattr(
        preflight_report,
        "probe_ap_managed_concurrency",
        lambda phy: calls.append(("concurrency", phy)) or True,
    )

    def fake_preflight(config, **kwargs):
        calls.append(("preflight", config, kwargs))
        return {"errors": [], "warnings": [], "details": {}}

    monkeypatch.setattr(preflight_report.preflight, "run", fake_preflight)

    report = preflight_report.collect_preflight_report(
        {
            "band_preference": "5ghz",
            "channel_width": "80",
            "enable_internet": True,
            "wpa2_passphrase": "must-not-be-reported",
        }
    )

    assert report["overall_readiness"] == "ready"
    assert ("readiness", INVENTORY) in calls
    assert ("concurrency", "phy1") in calls
    assert any(call[0] == "preflight" for call in calls if isinstance(call, tuple))
    assert "must-not-be-reported" not in json.dumps(report)
    assert preflight_report.supervisor.get_tails()[1] == stderr_before


def test_runtime_binary_probe_uses_public_read_only_inspection(monkeypatch):
    selection_calls = []

    def inspect_runtime_binaries():
        selection_calls.append(True)
        return {
            "hostapd": "/usr/sbin/hostapd",
            "dnsmasq": "/opt/vr/vendor/bin/dnsmasq",
            "selection_error": None,
            "probe_environment": {"LC_ALL": "C", "LANG": "C"},
        }

    monkeypatch.setattr(
        preflight_report.supervisor,
        "inspect_runtime_binaries",
        inspect_runtime_binaries,
    )

    def fail_engine_env(*_args, **_kwargs):
        raise AssertionError("diagnostics called private lifecycle engine setup")

    monkeypatch.setattr(preflight_report.supervisor, "_build_engine_env", fail_engine_env)
    monkeypatch.setattr(
        preflight_report,
        "vendor_bin_dirs",
        lambda: ["/opt/vr/vendor/bin"],
    )
    monkeypatch.setattr(
        preflight_report.preflight,
        "probe_hostapd_capabilities",
        lambda path: {
            "sae": True,
            "he": True,
            "raw": "hostapd v2.11\nSAE\nIEEE 802.11ax",
        },
    )

    def run_command(argv, **_kwargs):
        assert argv == ["/opt/vr/vendor/bin/dnsmasq", "--version"]
        return host_probes.CommandResult(
            argv=tuple(argv),
            exit_status=0,
            stdout="Dnsmasq version 2.90  Copyright (c) Simon Kelley\n",
        )

    monkeypatch.setattr(preflight_report.host_probes, "run_command", run_command)

    result = preflight_report._collect_runtime_binaries()

    assert result["selection_error"] is None
    assert result["hostapd"]["source"] == "system"
    assert result["hostapd"]["version"] == "2.11"
    assert result["hostapd"]["capabilities"] == {"sae": True, "he": True}
    assert result["dnsmasq"]["source"] == "bundled"
    assert result["dnsmasq"]["version"] == "2.90"
    assert selection_calls == [True]


def test_public_binary_inspection_does_not_execute_create_or_log(monkeypatch):
    supervisor = preflight_report.supervisor
    monkeypatch.delenv("VR_HOTSPOT_FORCE_VENDOR_BIN", raising=False)
    monkeypatch.delenv("VR_HOTSPOT_VENDOR_STRICT", raising=False)
    monkeypatch.delenv("VR_HOTSPOT_FORCE_SYSTEM_BIN", raising=False)
    monkeypatch.setattr(
        supervisor,
        "resolve_vendor_required",
        lambda _names: ({}, None, None, []),
    )
    monkeypatch.setattr(supervisor, "vendor_lib_dirs", lambda preferred_profile=None: [])
    monkeypatch.setattr(
        supervisor,
        "_which_in_path",
        lambda name, _path: f"/usr/sbin/{name}",
    )
    monkeypatch.setattr(supervisor.os_release, "read_os_release", lambda: {"id": "ubuntu"})

    def fail_side_effect(*_args, **_kwargs):
        raise AssertionError("read-only binary inspection caused a side effect")

    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        fail_side_effect,
    )
    monkeypatch.setattr(
        supervisor.tempfile,
        "mkdtemp",
        fail_side_effect,
    )
    monkeypatch.setattr(supervisor, "_stderr_tail", deque(["existing supervisor stderr"]))
    stderr_before = supervisor.get_tails()[1]

    result = supervisor.inspect_runtime_binaries()

    assert result["hostapd"] == "/usr/sbin/hostapd"
    assert result["dnsmasq"] == "/usr/sbin/dnsmasq"
    assert result["selection_error"] is None
    assert supervisor.get_tails()[1] == stderr_before


def test_iwd_probe_reports_mocked_service_state():
    paths = {
        "systemctl": "/usr/bin/systemctl",
        "iwctl": "/usr/bin/iwctl",
    }

    def runner(argv, **_kwargs):
        assert argv == ["/usr/bin/systemctl", "is-active", "iwd"]
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": "active\n", "stderr": ""},
        )()

    assert host_probes.probe_iwd(
        which=lambda name: paths.get(name),
        runner=runner,
    ) == {
        "present": True,
        "active": True,
        "status": "active",
        "iwctl": True,
    }


def test_config_snapshot_applies_migrations_without_writing(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "read_config_file",
        lambda: {"version": 1, "ssid": "Existing"},
    )

    def fail_write(*_args, **_kwargs):
        raise AssertionError("read-only config snapshot attempted a write")

    monkeypatch.setattr(config_module, "_write_atomic", fail_write)

    snapshot = config_module.load_config_snapshot()

    assert snapshot["version"] == config_module.CONFIG_SCHEMA_VERSION
    assert snapshot["ssid"] == "Existing"
