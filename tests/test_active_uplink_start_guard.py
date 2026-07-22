from copy import deepcopy
from dataclasses import replace

import pytest

from vr_hotspotd import host_facts, lifecycle
from vr_hotspotd.policy import ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
from vr_hotspotd.state import DEFAULT_STATE
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


def _config(*, ap_adapter=""):
    return {
        "ssid": "VR-Hotspot",
        "wpa2_passphrase": "password123",
        "band_preference": "5ghz",
        "ap_adapter": ap_adapter,
        "enable_internet": True,
    }


def _inventory():
    return {
        "recommended": "wlan1",
        "adapters": [
            {
                "ifname": "wlan0",
                "phy": "phy0",
                "bus": "pci",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            },
            {
                "ifname": "wlan1",
                "phy": "phy1",
                "bus": "usb",
                "supports_ap": True,
                "supports_5ghz": True,
                "supports_80mhz": True,
            },
        ],
    }


def _stub_read_only_start_environment(
    monkeypatch,
    *,
    config,
    active_uplink_interface,
    snapshot=None,
):
    state = deepcopy(DEFAULT_STATE)
    events = []
    operation_snapshot = snapshot or make_host_facts_snapshot(
        snapshot_id="lifecycle-start-snapshot",
        operation_kind="lifecycle_start",
        default_uplink_interface=active_uplink_interface,
    )

    def load_state():
        return state

    def update_state(**kwargs):
        for key, value in kwargs.items():
            if key == "engine" and isinstance(value, dict):
                state["engine"].update(value)
            elif key == "warnings" and isinstance(value, list):
                state["warnings"] = list(value)
            else:
                state[key] = value
        return state

    def build_snapshot(*, operation_kind):
        events.append("snapshot")
        assert operation_kind == "lifecycle_start"
        return operation_snapshot

    def get_adapters(*, host_facts_snapshot):
        events.append("inventory")
        assert host_facts_snapshot is operation_snapshot
        return _inventory()

    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "load_state", load_state)
    monkeypatch.setattr(lifecycle, "update_state", update_state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: dict(config))
    monkeypatch.setattr(lifecycle, "build_host_facts_snapshot", build_snapshot)
    monkeypatch.setattr(lifecycle, "get_adapters", get_adapters)
    monkeypatch.setattr(
        lifecycle.host_probes,
        "probe_default_uplink",
        lambda: (_ for _ in ()).throw(
            AssertionError("lifecycle re-probed the default uplink")
        ),
    )
    monkeypatch.setattr(
        lifecycle.wifi_probe,
        "detect_firewall_backends",
        lambda: {"selected_backend": "nftables"},
    )
    monkeypatch.setattr(lifecycle.os_release, "read_os_release", lambda: {"id": "ubuntu"})
    monkeypatch.setattr(
        lifecycle.os_release,
        "apply_platform_overrides",
        lambda cfg, _info: (cfg, []),
    )
    monkeypatch.setattr(lifecycle.os_release, "is_cachyos", lambda _info=None: False)
    monkeypatch.setattr(lifecycle.os_release, "is_pop_os", lambda _info=None: False)
    monkeypatch.setattr(lifecycle.os_release, "is_bazzite", lambda _info=None: False)

    return state, events, operation_snapshot


def _forbid_mutations(monkeypatch):
    calls = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"mutation helper called before active-uplink guard: {name}")

        return fail

    for name in (
        "_repair_impl",
        "_reserve_iwd_ap_adapter",
        "_nm_set_unmanaged",
        "_disconnect_iwd_ap_adapter",
        "_prepare_ap_interface",
        "_ensure_iface_up",
        "_maybe_set_regdom",
        "_start_hotspot_5ghz_strict",
        "start_engine",
    ):
        monkeypatch.setattr(lifecycle, name, forbidden(name))
    monkeypatch.setattr(
        lifecycle.system_tuning,
        "apply_pre",
        forbidden("system_tuning.apply_pre"),
    )
    monkeypatch.setattr(
        lifecycle.network_tuning,
        "apply",
        forbidden("network_tuning.apply"),
    )
    return calls


@pytest.mark.parametrize(
    ("configured_adapter", "active_uplink_interface", "selected_adapter"),
    (
        ("wlan0", "wlan0", "wlan0"),
        ("", "wlan1", "wlan1"),
    ),
    ids=("configured_adapter", "automatically_recommended_adapter"),
)
def test_active_uplink_conflict_blocks_before_any_mutation(
    monkeypatch,
    configured_adapter,
    active_uplink_interface,
    selected_adapter,
):
    state, events, _snapshot = _stub_read_only_start_environment(
        monkeypatch,
        config=_config(ap_adapter=configured_adapter),
        active_uplink_interface=active_uplink_interface,
    )
    mutation_calls = _forbid_mutations(monkeypatch)

    result = lifecycle._start_hotspot_impl(correlation_id="active-uplink-test")

    assert result.code == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    assert state["phase"] == "error"
    assert state["running"] is False
    assert state["last_error"] == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    assert state["last_error_detail"] == {
        "code": ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK,
        "remediation": (
            "Use a separate Wi-Fi adapter for the AP, or use Ethernet or another "
            "interface as the uplink."
        ),
        "context": {
            "ap_adapter": selected_adapter,
            "active_uplink_interface": active_uplink_interface,
        },
    }
    assert events == ["snapshot", "inventory"]
    assert mutation_calls == []


def test_active_uplink_conflict_after_reselection_blocks_before_replacement_mutation(
    monkeypatch,
):
    state, events, snapshot = _stub_read_only_start_environment(
        monkeypatch,
        config=_config(ap_adapter="wlan0"),
        active_uplink_interface="wlan1",
    )
    inventory = _inventory()
    active_uplink_adapter = next(
        adapter
        for adapter in inventory["adapters"]
        if adapter["ifname"] == "wlan1"
    )
    calls = []

    def repair(*_args, **_kwargs):
        calls.append(("repair", None))
        return state

    def reserve_iwd(ifname, **_kwargs):
        calls.append(("iwd_reservation", ifname))
        return []

    def prepare(ifname, **_kwargs):
        calls.append(("prepare", ifname))
        if ifname == "wlan1":
            raise AssertionError("active uplink prepared after late reselection")
        return ["ap_iface_not_up_prestart"]

    def ensure_up(ifname):
        calls.append(("ensure_up", ifname))
        if ifname == "wlan1":
            raise AssertionError("active uplink mutated after late reselection")
        return False

    def reselect(**kwargs):
        calls.append(("reselect", kwargs["ap_ifname"]))
        assert kwargs["host_facts_snapshot"] is snapshot
        return (
            "wlan1",
            inventory,
            active_uplink_adapter,
            ["ap_adapter_reselected_after_reload:wlan0->wlan1"],
        )

    def forbidden(name):
        def fail(*_args, **_kwargs):
            calls.append((name, None))
            raise AssertionError(f"called after active-uplink reselection: {name}")

        return fail

    monkeypatch.setattr(lifecycle, "_repair_impl", repair)
    monkeypatch.setattr(lifecycle, "_reserve_iwd_ap_adapter", reserve_iwd)
    monkeypatch.setattr(lifecycle, "_nm_gate_check", lambda _ifname: None)
    monkeypatch.setattr(lifecycle, "_prepare_ap_interface", prepare)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", ensure_up)
    monkeypatch.setattr(
        lifecycle,
        "_maybe_reselect_ap_after_prestart_failure",
        reselect,
    )
    monkeypatch.setattr(lifecycle.os_release, "is_pop_os", lambda _info=None: True)
    monkeypatch.setattr(lifecycle.os_release, "is_steamos", lambda _info=None: False)
    monkeypatch.setattr(
        lifecycle,
        "_start_hotspot_5ghz_strict",
        forbidden("_start_hotspot_5ghz_strict"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_maybe_set_regdom",
        forbidden("_maybe_set_regdom"),
    )
    monkeypatch.setattr(
        lifecycle.system_tuning,
        "apply_pre",
        forbidden("system_tuning.apply_pre"),
    )
    monkeypatch.setattr(
        lifecycle.network_tuning,
        "apply",
        forbidden("network_tuning.apply"),
    )
    monkeypatch.setattr(lifecycle, "start_engine", forbidden("start_engine"))

    result = lifecycle._start_hotspot_impl(
        correlation_id="active-uplink-reselection-test"
    )

    assert result.code == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    assert state["phase"] == "error"
    assert state["running"] is False
    assert state["last_error"] == ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    assert state["last_error_detail"] == {
        "code": ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK,
        "remediation": (
            "Use a separate Wi-Fi adapter for the AP, or use Ethernet or another "
            "interface as the uplink."
        ),
        "context": {
            "ap_adapter": "wlan1",
            "active_uplink_interface": "wlan1",
        },
    }
    assert events == ["snapshot", "inventory"]
    assert calls == [
        ("repair", None),
        ("iwd_reservation", "wlan0"),
        ("prepare", "wlan0"),
        ("ensure_up", "wlan0"),
        ("reselect", "wlan0"),
    ]


@pytest.mark.parametrize(
    "active_uplink_interface",
    (None, "enp4s0", "wlan0"),
    ids=("no_uplink", "separate_ethernet_uplink", "separate_wifi_uplink"),
)
def test_non_conflicting_uplink_is_allowed_past_guard(
    monkeypatch,
    active_uplink_interface,
):
    state, events, snapshot = _stub_read_only_start_environment(
        monkeypatch,
        config=_config(),
        active_uplink_interface=active_uplink_interface,
    )
    mutation_calls = []

    def repair(*_args, **kwargs):
        assert kwargs["host_facts_snapshot"] is snapshot
        assert kwargs["inventory"] == _inventory()
        mutation_calls.append("repair")
        return state

    def stop_after_guard(*_args, **_kwargs):
        mutation_calls.append("iwd_reservation")
        raise RuntimeError("stop_after_active_uplink_guard")

    monkeypatch.setattr(lifecycle, "_repair_impl", repair)
    monkeypatch.setattr(lifecycle, "_reserve_iwd_ap_adapter", stop_after_guard)

    result = lifecycle._start_hotspot_impl(correlation_id="allowed-uplink-test")

    assert result.code == "start_failed"
    assert state["last_error"] == "stop_after_active_uplink_guard"
    assert state["last_error"] != ERROR_AP_ADAPTER_IS_ACTIVE_UPLINK
    assert events == ["snapshot", "inventory"]
    assert mutation_calls == ["repair", "iwd_reservation"]


def test_unknown_snapshot_default_uplink_blocks_before_any_mutation(monkeypatch):
    snapshot = make_host_facts_snapshot(
        snapshot_id="unknown-uplink-snapshot",
        operation_kind="lifecycle_start",
        default_uplink_interface=None,
    )
    snapshot = replace(
        snapshot,
        probe_errors=(
            host_facts.ProbeError(
                probe_id="network.default_uplink",
                kind="timeout",
                message="read-only command timed out",
                exit_status=124,
            ),
        ),
    )
    state, events, _snapshot = _stub_read_only_start_environment(
        monkeypatch,
        config=_config(ap_adapter="wlan0"),
        active_uplink_interface=None,
        snapshot=snapshot,
    )
    mutation_calls = _forbid_mutations(monkeypatch)

    result = lifecycle._start_hotspot_impl(correlation_id="unknown-uplink-test")

    assert result.code == "start_failed"
    assert state["last_error"] == "default_uplink_unknown"
    assert state["last_error_detail"] == {
        "code": "default_uplink_unknown",
        "remediation": "Check logs for details.",
        "context": {
            "ap_adapter": "wlan0",
            "active_uplink_interface": None,
        },
    }
    assert events == ["snapshot", "inventory"]
    assert mutation_calls == []
