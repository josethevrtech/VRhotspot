from copy import deepcopy
from types import SimpleNamespace

import pytest

from vr_hotspotd import system_tuning


def apply(state, cfg, *, ap="x0wlan1", adapter="wlan1"):
    return system_tuning.apply_runtime(
        state, cfg, ap_ifname=ap, adapter_ifname=adapter, cpu_affinity_pids=[]
    )


@pytest.fixture
def safe_power_target(monkeypatch):
    monkeypatch.setattr(system_tuning, "_tx_power_target_guard", lambda *_args: None)


@pytest.mark.parametrize("requested_dbm", [None, 20])
def test_startup_applies_power_to_actual_ap_and_records_driver_value(monkeypatch, safe_power_target, requested_dbm):
    calls = []
    cfg = {"tx_power": requested_dbm}
    original_cfg = deepcopy(cfg)
    monkeypatch.setattr(system_tuning, "set_tx_power", lambda iface, power:
                        calls.append((iface, power)) or (True, "ok"))
    monkeypatch.setattr(system_tuning, "get_tx_power", lambda iface: 17)

    state, warnings = apply({}, cfg)

    assert calls == [("x0wlan1", requested_dbm)]
    assert warnings == []
    assert state["tx_power"] == {
        "interface": "x0wlan1", "mode": "auto" if requested_dbm is None else "fixed",
        "requested_dbm": requested_dbm, "effective_dbm": 17, "status": "applied",
    }
    assert cfg == original_cfg


@pytest.mark.parametrize("requested_dbm", [None, 20])
def test_rejected_power_preserves_error_and_effective_value_without_failing_startup(monkeypatch, safe_power_target, requested_dbm):
    error = "command failed: Operation not supported (-95)"
    monkeypatch.setattr(system_tuning, "set_tx_power", lambda *_args: (False, error))
    monkeypatch.setattr(system_tuning, "get_tx_power", lambda _iface: 12)

    state, warnings = apply({}, {"tx_power": requested_dbm})

    assert state["tx_power"]["status"] == "failed"
    assert state["tx_power"]["error"] == error
    assert state["tx_power"]["requested_dbm"] == requested_dbm
    assert state["tx_power"]["effective_dbm"] == 12
    assert warnings == [f"tx_power_apply_failed:x0wlan1:{error}"]


@pytest.mark.parametrize("accepted", [True, False])
def test_repeated_runtime_hook_does_not_change_power_during_session(monkeypatch, safe_power_target, accepted):
    calls = []
    monkeypatch.setattr(system_tuning, "set_tx_power", lambda iface, power:
                        calls.append((iface, power)) or (accepted, "ok" if accepted else "unsupported"))
    monkeypatch.setattr(system_tuning, "get_tx_power", lambda _iface: None)

    state, _ = apply({}, {"tx_power": None})
    apply(state, {"tx_power": 20})

    assert calls == [("x0wlan1", None)]
    assert state["tx_power"]["requested_dbm"] is None


def test_new_session_resets_driver_to_auto_again(monkeypatch, safe_power_target):
    calls = []
    monkeypatch.setattr(system_tuning, "set_tx_power", lambda iface, power:
                        calls.append((iface, power)) or (True, "ok"))
    monkeypatch.setattr(system_tuning, "get_tx_power", lambda _iface: None)

    apply({}, {"tx_power": None})
    apply({}, {"tx_power": None})

    assert calls == [("x0wlan1", None), ("x0wlan1", None)]


def test_missing_ap_never_changes_parent_adapter_power(monkeypatch):
    def forbidden(*_args):
        pytest.fail("do not change an uplink or unresolved AP interface")

    monkeypatch.setattr(system_tuning, "set_tx_power", forbidden)
    monkeypatch.setattr(system_tuning, "get_tx_power", forbidden)

    state, warnings = apply({}, {"tx_power": 20}, ap=None)

    assert state == {}
    assert warnings == []


@pytest.mark.parametrize("requested_dbm", [float("nan"), float("inf"), "20", True, 20.5, -1, 31])
def test_invalid_runtime_power_rejected_before_any_tuning(monkeypatch, requested_dbm):
    def forbidden(*_args):
        pytest.fail("invalid config must be rejected before host mutations")

    monkeypatch.setattr(system_tuning, "set_tx_power", forbidden)
    monkeypatch.setattr(system_tuning, "get_tx_power", forbidden)
    monkeypatch.setattr(system_tuning, "_get_power_save", forbidden)

    state, warnings = apply({}, {"tx_power": requested_dbm, "wifi_power_save_disable": True})

    assert state == {}
    assert len(warnings) == 1
    assert warnings[0].startswith("invalid_tx_power:")


DEDICATED_VIRTUAL_AP = """phy#1
\tInterface x0wlan1
\t\tifindex 4
\t\ttype AP
\t\tssid VR-Hotspot
\t\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
\tInterface wlan1
\t\tifindex 3
\t\ttype managed
phy#0
\tInterface wlan0
\t\tifindex 2
\t\ttype managed
\t\tssid home
\t\tchannel 149 (5745 MHz), width: 80 MHz, center1: 5775 MHz
"""


def mock_inventory(monkeypatch, output=DEDICATED_VIRTUAL_AP, *, link_states=None, returncode=0,
                   parent_link="Connected to 00:11:22:33:44:55 (on wlan1)\n"):
    calls = []
    monkeypatch.setattr(system_tuning, "_iw_bin", lambda: "/usr/sbin/iw")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=parent_link if argv[-1] == "link" else output)

    monkeypatch.setattr(system_tuning.subprocess, "run", run)
    states = link_states if link_states is not None else {"x0wlan1": True, "wlan1": False, "wlan0": True}
    monkeypatch.setattr(system_tuning, "_interface_admin_up", lambda iface: states.get(iface))
    return calls


def test_power_guard_accepts_owned_virtual_ap_with_down_parent(monkeypatch):
    calls = mock_inventory(monkeypatch)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") is None
    assert calls == [(["/usr/sbin/iw", "dev"], {"capture_output": True, "text": True, "timeout": 2.0})]


def test_power_guard_accepts_direct_ap(monkeypatch):
    inventory = DEDICATED_VIRTUAL_AP.split("\tInterface wlan1", 1)[0].replace("x0wlan1", "wlan1")
    mock_inventory(monkeypatch, inventory, link_states={"wlan1": True})

    assert system_tuning._tx_power_target_guard("wlan1", "wlan1", "VR-Hotspot") is None


def test_power_guard_accepts_selected_parent_up_but_proven_disconnected(monkeypatch):
    calls = mock_inventory(monkeypatch, link_states={"x0wlan1": True, "wlan1": True},
                           parent_link="Not connected.\n")

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") is None
    assert calls[-1] == (["/usr/sbin/iw", "dev", "wlan1", "link"],
                         {"capture_output": True, "text": True, "timeout": 2.0})


def test_power_guard_rejects_selected_parent_when_link_state_unknown(monkeypatch):
    mock_inventory(monkeypatch, link_states={"x0wlan1": True, "wlan1": True}, parent_link="")

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "shared_phy_peer_state_unknown"


@pytest.mark.parametrize(("ap", "adapter", "ssid", "reason"), [
    ("x0wlan1", "wlan0", "VR-Hotspot", "identity_phy_mismatch"),
    ("x0wlan1", "wlan1", "Other-SSID", "identity_ssid_mismatch"),
    ("missing", "wlan1", "VR-Hotspot", "identity_interface_missing_or_ambiguous"),
    ("x0wlan1", "missing", "VR-Hotspot", "identity_interface_missing_or_ambiguous"),
    ("x0wlan1", None, "VR-Hotspot", "identity_metadata_missing"),
    ("x0wlan1", "wlan1", None, "identity_metadata_missing"),
])
def test_power_guard_rejects_unproved_ap_identity(monkeypatch, ap, adapter, ssid, reason):
    mock_inventory(monkeypatch)

    assert system_tuning._tx_power_target_guard(ap, adapter, ssid) == reason


@pytest.mark.parametrize(("parent_up", "reason"), [
    (True, "shared_phy_active_interface"), (None, "shared_phy_peer_state_unknown"),
])
def test_power_guard_rejects_active_or_unknown_parent(monkeypatch, parent_up, reason):
    mock_inventory(monkeypatch, link_states={"x0wlan1": True, "wlan1": parent_up})

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == reason


def test_power_guard_rejects_other_active_sta_on_same_phy(monkeypatch):
    inventory = DEDICATED_VIRTUAL_AP.replace("phy#0\n", "")
    mock_inventory(monkeypatch, inventory)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "shared_phy_active_interface"


def test_power_guard_rejects_uninspectable_non_netdev_peer(monkeypatch):
    inventory = DEDICATED_VIRTUAL_AP.replace("phy#0\n", "\tUnnamed/non-netdev interface\n\t\ttype P2P-device\nphy#0\n")
    mock_inventory(monkeypatch, inventory)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "shared_phy_peer_state_unknown"


@pytest.mark.parametrize(("inventory", "reason"), [
    (DEDICATED_VIRTUAL_AP.replace("\t\tssid VR-Hotspot\n", ""), "identity_ssid_mismatch"),
    (DEDICATED_VIRTUAL_AP.replace("\t\ttype AP\n", ""), "ap_not_ready"),
    (DEDICATED_VIRTUAL_AP.replace("channel 36", "no-channel"), "ap_not_ready"),
    (DEDICATED_VIRTUAL_AP.replace("phy#1", "phy#unknown"), "inventory_invalid"),
    ("", "inventory_invalid"),
    ("x" * 131073, "inventory_invalid"),
])
def test_power_guard_rejects_missing_or_malformed_metadata(monkeypatch, inventory, reason):
    mock_inventory(monkeypatch, inventory)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == reason


@pytest.mark.parametrize("ap_up", [False, None])
def test_power_guard_requires_ap_link_up(monkeypatch, ap_up):
    mock_inventory(monkeypatch, link_states={"x0wlan1": ap_up, "wlan1": False})

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "ap_link_state_unavailable_or_down"


def test_power_guard_reports_failed_inventory(monkeypatch):
    mock_inventory(monkeypatch, returncode=1)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "inventory_failed:rc=1"


def test_power_guard_reports_inventory_timeout(monkeypatch):
    monkeypatch.setattr(system_tuning, "_iw_bin", lambda: "/usr/sbin/iw")

    def timeout(*args, **kwargs):
        raise system_tuning.subprocess.TimeoutExpired("iw", 2.0)

    monkeypatch.setattr(system_tuning.subprocess, "run", timeout)

    assert system_tuning._tx_power_target_guard("x0wlan1", "wlan1", "VR-Hotspot") == "inventory_failed:TimeoutExpired"


@pytest.mark.parametrize("requested_dbm", [None, 20])
def test_unsafe_target_skips_power_but_keeps_startup_usable(monkeypatch, requested_dbm):
    mock_inventory(monkeypatch, link_states={"x0wlan1": True, "wlan1": True})

    def forbidden(*args):
        pytest.fail("unsafe radio must not receive a power command or be presented as applied")

    monkeypatch.setattr(system_tuning, "set_tx_power", forbidden)
    monkeypatch.setattr(system_tuning, "get_tx_power", forbidden)

    state, warnings = apply({}, {"ssid": "VR-Hotspot", "tx_power": requested_dbm})

    assert state["tx_power"]["status"] == "skipped"
    assert state["tx_power"]["effective_dbm"] is None
    assert state["tx_power"]["error"] == "shared_phy_active_interface"
    assert warnings == ["tx_power_skipped:x0wlan1:shared_phy_active_interface"]


@pytest.mark.parametrize(("flags", "expected"), [("0x1003\n", True), ("0x1002\n", False), ("invalid", None), ("-1", None)])
def test_interface_admin_up_reads_flags(monkeypatch, flags, expected):
    monkeypatch.setattr(system_tuning.Path, "read_text", lambda *_args, **_kwargs: flags)

    assert system_tuning._interface_admin_up("wlan1") is expected


def test_interface_admin_up_missing_flags_is_unknown(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(system_tuning.Path, "read_text", missing)

    assert system_tuning._interface_admin_up("wlan1") is None
    assert system_tuning._interface_admin_up("../invalid") is None
