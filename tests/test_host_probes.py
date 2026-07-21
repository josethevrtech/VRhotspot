from types import SimpleNamespace
import subprocess

import pytest

from vr_hotspotd import host_probes
from vr_hotspotd import lifecycle
from vr_hotspotd import network_tuning
from vr_hotspotd import preflight
from vr_hotspotd import wifi_probe
from vr_hotspotd.adapters import inventory
from vr_hotspotd.diagnostics import platform
from vr_hotspotd.engine import hostapd6_engine
from vr_hotspotd.engine import hostapd_bridge_engine
from vr_hotspotd.engine import hostapd_nat_engine


def test_run_command_normalizes_success_and_keeps_streams_separate():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=3, stdout="standard\n", stderr="diagnostic\n")

    result = host_probes.run_command(
        ["/usr/bin/probe", "--status"],
        timeout_s=1.25,
        env={"LC_ALL": "C"},
        runner=runner,
    )

    assert result.argv == ("/usr/bin/probe", "--status")
    assert result.exit_status == 3
    assert result.returncode == 3
    assert result.stdout == "standard\n"
    assert result.stderr == "diagnostic\n"
    assert result.combined_output() == "standard\n\ndiagnostic"
    assert result.ok is False
    assert calls == [
        (
            ["/usr/bin/probe", "--status"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 1.25,
                "env": {"LC_ALL": "C"},
            },
        )
    ]


def test_run_command_discards_byte_timeout_output_for_legacy_callers():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    result = host_probes.run_command(
        ["read-only-probe"],
        timeout_s=0.1,
        runner=runner,
    )

    assert result.exit_status == 124
    assert result.timed_out is True
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.combined_output() == ""


def test_run_command_keeps_text_timeout_output_for_legacy_callers():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=kwargs["timeout"],
            output="partial stdout",
            stderr="partial stderr",
        )

    result = host_probes.run_command(
        ["read-only-probe"],
        timeout_s=0.1,
        runner=runner,
    )

    assert result.exit_status == 124
    assert result.timed_out is True
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.combined_output() == "partial stdout\npartial stderr"


@pytest.mark.parametrize(
    ("raised", "missing", "permission_denied"),
    [
        (FileNotFoundError("not installed"), True, False),
        (PermissionError("not executable"), False, True),
    ],
)
def test_run_command_normalizes_os_errors(raised, missing, permission_denied):
    def runner(_argv, **_kwargs):
        raise raised

    result = host_probes.run_command(
        ["read-only-probe"],
        timeout_s=0.1,
        runner=runner,
    )

    assert result.exit_status == 127
    assert result.missing is missing
    assert result.permission_denied is permission_denied
    assert type(raised).__name__ in (result.error or "")


def test_legacy_runner_wrappers_keep_their_timeout_contracts(monkeypatch):
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(host_probes.subprocess, "run", runner)

    assert wifi_probe._run(["probe"]) == (124, "")
    assert preflight._run(["probe"]) == (124, "")
    assert platform._run_cmd(["probe"]) == (127, "")


def test_preflight_hostapd_timeout_uses_version_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "_resolve_hostapd_path", lambda: "/usr/sbin/hostapd")

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "-vv":
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=kwargs["timeout"],
                output=b"SAE\nIEEE 802.11ax",
                stderr=b"",
            )
        return SimpleNamespace(returncode=0, stdout="hostapd v2.9", stderr="")

    monkeypatch.setattr(host_probes.subprocess, "run", runner)

    assert preflight._hostapd_caps() == {
        "sae": False,
        "he": False,
        "raw": "hostapd v2.9",
    }
    assert [argv[-1] for argv, _kwargs in calls] == ["-vv", "-v"]


def test_inventory_runner_keeps_merged_output_order_on_failure(monkeypatch):
    merged_output = "stdout-before\nstderr-middle\nstdout-after\n"

    def runner(argv, **kwargs):
        assert argv == ["probe"]
        assert kwargs == {
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
        }
        return SimpleNamespace(
            returncode=5,
            stdout=merged_output,
            stderr=None,
        )

    monkeypatch.setattr(host_probes.subprocess, "run", runner)

    with pytest.raises(subprocess.CalledProcessError) as exc:
        inventory._run(["probe"])

    assert exc.value.returncode == 5
    assert exc.value.output == merged_output


def test_inventory_ap_mode_scan_resumes_at_later_modes_block(monkeypatch):
    iw_text = """
Supported interface modes:
  * managed
Band 1:
Supported interface modes:
  * AP
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_text)

    assert inventory._phy_supports_ap("phy0") is True


@pytest.mark.parametrize(
    ("info", "flavor", "family"),
    [
        ({"id": "steamos", "id_like": "arch"}, "steamos", "arch"),
        ({"id": "bazzite", "id_like": "fedora"}, "bazzite", "fedora"),
        (
            {"id": "fedora", "variant_id": "silverblue"},
            "fedora_atomic",
            "fedora",
        ),
        ({"id": "pop", "id_like": "ubuntu debian"}, "ubuntu_debian", "debian"),
        ({"id": "cachyos", "id_like": "arch"}, "arch", "arch"),
        ({"id": "unsupported"}, "unknown", None),
    ],
)
def test_os_flavor_characterization(info, flavor, family):
    expected = host_probes.classify_os_flavor(info)

    assert expected["flavor"] == flavor
    assert expected["family"] == family
    assert wifi_probe.detect_os_flavor(info) == expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        pytest.param("Status: active\n", True, id="active"),
        pytest.param("Status: inactive\n", False, id="inactive"),
        pytest.param("  StAtUs \t: \tAcTiVe  \n", True, id="active-case-whitespace"),
        pytest.param("\tSTATUS: INACTIVE \n", False, id="inactive-case-whitespace"),
        pytest.param("Status: hyperactive\n", False, id="active-substring-in-value"),
        pytest.param("UFW is active\n", False, id="active-on-unrelated-line"),
        pytest.param("NotStatus: active\n", False, id="active-in-unrelated-field"),
        pytest.param("Status: active now\n", False, id="active-with-extra-text"),
        pytest.param("Status active\n", False, id="malformed-status-line"),
    ],
)
def test_parse_ufw_status_requires_exact_active_value(output, expected):
    assert host_probes.parse_ufw_status(output) is expected


def _firewall_probe(
    *,
    firewalld,
    ufw_output,
    nft,
    iptables_output,
    ufw_returncode=0,
    ufw_exception=None,
):
    paths = {
        "firewall-cmd": "/usr/bin/firewall-cmd",
        "ufw": "/usr/sbin/ufw",
        "nft": "/usr/sbin/nft",
        "iptables": "/usr/sbin/iptables",
    }
    if firewalld is None:
        paths.pop("firewall-cmd")
    if ufw_output is None:
        paths.pop("ufw")
    if not nft:
        paths.pop("nft")
    if iptables_output is None:
        paths.pop("iptables")

    def which(name):
        return paths.get(name)

    def runner(argv, **_kwargs):
        command = tuple(argv)
        if command == ("firewall-cmd", "--state"):
            return SimpleNamespace(
                returncode=0 if firewalld else 1,
                stdout="running\n" if firewalld else "not running\n",
                stderr="",
            )
        if command == ("ufw", "status"):
            if ufw_exception is not None:
                raise ufw_exception
            return SimpleNamespace(
                returncode=ufw_returncode,
                stdout=ufw_output,
                stderr="",
            )
        if command == ("/usr/sbin/iptables", "--version"):
            return SimpleNamespace(
                returncode=0,
                stdout=iptables_output,
                stderr="",
            )
        raise AssertionError(f"unexpected read-only command: {argv}")

    return host_probes.probe_firewall_backends(which=which, runner=runner)


def test_firewall_priority_prefers_running_firewalld():
    result = _firewall_probe(
        firewalld=True,
        ufw_output="Status: active\n",
        nft=True,
        iptables_output="iptables v1.8.10 (nf_tables)",
    )

    assert result["selected_backend"] == "firewalld"
    assert result["rationale"] == "firewalld_running"
    assert result["iptables"]["variant"] == "iptables-nft"


def test_firewall_priority_uses_active_ufw_before_nftables():
    ufw_result = _firewall_probe(
        firewalld=False,
        ufw_output="Status: active\n",
        nft=True,
        iptables_output="iptables v1.8.10 (legacy)",
    )

    assert ufw_result["ufw"] == {"available": True, "active": True}
    assert ufw_result["selected_backend"] == "ufw"
    assert ufw_result["rationale"] == "ufw_active"


def test_firewall_priority_corrects_legacy_inactive_ufw_substring_behavior():
    inactive_ufw_result = _firewall_probe(
        firewalld=False,
        ufw_output="Status: inactive\n",
        nft=True,
        iptables_output="iptables v1.8.10 (legacy)",
    )

    assert inactive_ufw_result["ufw"] == {"available": True, "active": False}
    assert inactive_ufw_result["selected_backend"] == "nftables"
    assert inactive_ufw_result["rationale"] == "nft_present"


def test_firewall_priority_falls_through_inactive_ufw_to_iptables():
    result = _firewall_probe(
        firewalld=False,
        ufw_output="Status: inactive\n",
        nft=False,
        iptables_output="iptables v1.8.10 (legacy)",
    )

    assert result["ufw"] == {"available": True, "active": False}
    assert result["selected_backend"] == "iptables"
    assert result["rationale"] == "iptables_present"
    assert result["iptables"]["variant"] == "iptables-legacy"


@pytest.mark.parametrize(
    ("ufw_output", "ufw_returncode", "ufw_exception", "available"),
    [
        pytest.param(None, 0, None, False, id="missing-command"),
        pytest.param("Status: active\n", 1, None, True, id="nonzero-result"),
        pytest.param("", 0, PermissionError("denied"), True, id="permission-error"),
        pytest.param("Status active\n", 0, None, True, id="malformed-output"),
    ],
)
def test_firewall_probe_keeps_unavailable_or_failing_ufw_inactive(
    ufw_output,
    ufw_returncode,
    ufw_exception,
    available,
):
    result = _firewall_probe(
        firewalld=False,
        ufw_output=ufw_output,
        ufw_returncode=ufw_returncode,
        ufw_exception=ufw_exception,
        nft=True,
        iptables_output="iptables v1.8.10 (legacy)",
    )

    assert result["ufw"] == {"available": available, "active": False}
    assert result["selected_backend"] == "nftables"
    assert result["rationale"] == "nft_present"


def test_firewall_priority_falls_through_missing_ufw_nft_and_iptables():
    nft_result = _firewall_probe(
        firewalld=False,
        ufw_output=None,
        nft=True,
        iptables_output="iptables v1.8.10 (legacy)",
    )
    iptables_result = _firewall_probe(
        firewalld=None,
        ufw_output=None,
        nft=False,
        iptables_output="iptables v1.8.10 (legacy)",
    )

    assert nft_result["selected_backend"] == "nftables"
    assert iptables_result["selected_backend"] == "iptables"
    assert iptables_result["iptables"]["variant"] == "iptables-legacy"


def test_network_manager_probe_preserves_result_shape():
    def runner(argv, **_kwargs):
        assert argv == ["/usr/bin/nmcli", "-t", "-f", "RUNNING", "g"]
        return SimpleNamespace(returncode=0, stdout="running\n", stderr="")

    result = host_probes.probe_network_manager(
        which=lambda name: "/usr/bin/nmcli" if name == "nmcli" else None,
        runner=runner,
    )

    assert result == {"nmcli": True, "running": True}


def test_lifecycle_iwd_detection_keeps_service_then_iwctl_fallback(monkeypatch):
    monkeypatch.setattr(lifecycle, "_systemctl_is_active", lambda _unit: False)
    monkeypatch.setattr(
        lifecycle.shutil,
        "which",
        lambda name: "/usr/bin/iwctl" if name == "iwctl" else None,
    )

    assert lifecycle._iwd_is_active() is True


def test_engine_iwd_detection_keeps_service_then_iwctl_fallback(monkeypatch):
    def which(name):
        return {
            "systemctl": "/usr/bin/systemctl",
            "iwctl": "/usr/bin/iwctl",
        }.get(name)

    def runner(argv, **kwargs):
        assert argv == [
            "/usr/bin/systemctl",
            "is-active",
            "--quiet",
            "iwd",
        ]
        assert kwargs == {"capture_output": True, "text": True}
        return SimpleNamespace(returncode=3, stdout="inactive\n", stderr="")

    monkeypatch.setattr(hostapd_nat_engine.shutil, "which", which)
    monkeypatch.setattr(hostapd_nat_engine.subprocess, "run", runner)

    assert hostapd_nat_engine._iwd_is_active() is True


IW_PHY_SAMPLE = """
Wiphy phy0
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

IW_REG_SAMPLE = """
global
country US: DFS-FCC
phy#0 (self-managed)
country CA: DFS-FCC
"""


def test_shared_iw_capability_parsers_preserve_legacy_wrappers(monkeypatch):
    assert host_probes.supports_ap_mode(IW_PHY_SAMPLE) is True
    assert wifi_probe._parse_supported_interface_modes(IW_PHY_SAMPLE) is True
    assert lifecycle._parse_supported_interface_modes(IW_PHY_SAMPLE) is True
    assert inventory._supports_wifi6_from_iw(IW_PHY_SAMPLE) is True
    assert host_probes.supports_80mhz(IW_PHY_SAMPLE) is True
    assert host_probes.parse_band_support(IW_PHY_SAMPLE) == {
        "supports_2ghz": True,
        "supports_5ghz": True,
        "supports_6ghz": False,
    }
    assert lifecycle._parse_ap_managed_concurrency(IW_PHY_SAMPLE) is True

    monkeypatch.setattr(inventory, "_run_iw", lambda _args: IW_PHY_SAMPLE)
    inventory._phy_supports_80mhz.cache_clear()
    try:
        assert inventory._phy_supports_ap("phy0") is True
        assert inventory._phy_supports_80mhz("phy0") is True
        assert inventory._phy_band_support("phy0") == {
            "supports_2ghz": True,
            "supports_5ghz": True,
            "supports_6ghz": False,
        }
    finally:
        inventory._phy_supports_80mhz.cache_clear()


def test_shared_regulatory_parser_preserves_inventory_and_wifi_shapes(monkeypatch):
    expected = {
        "global": {
            "country": "US",
            "raw_header": "country US: DFS-FCC",
        },
        "phys": {
            "phy0": {
                "country": "CA",
                "source": "self-managed",
                "raw_header": "country CA: DFS-FCC",
            }
        },
    }

    monkeypatch.setattr(inventory, "_run_iw", lambda _args: IW_REG_SAMPLE)

    assert host_probes.parse_regulatory_domains(IW_REG_SAMPLE) == expected
    assert wifi_probe._parse_iw_reg_get(IW_REG_SAMPLE) == expected
    assert inventory._parse_iw_reg_get() == expected


@pytest.mark.parametrize(
    "module",
    [
        network_tuning,
        hostapd_nat_engine,
        hostapd_bridge_engine,
        hostapd6_engine,
    ],
)
def test_default_uplink_wrappers_keep_first_default_route(module, monkeypatch):
    route_output = "\n".join(
        [
            "default via 192.0.2.1 dev enp4s0 proto dhcp metric 100",
            "default via 198.51.100.1 dev wlan0 proto dhcp metric 600",
        ]
    )
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=route_output, stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/sbin/ip" if name == "ip" else None)
    monkeypatch.setattr(module.subprocess, "run", runner)

    assert module._default_uplink_iface() == "enp4s0"
    assert calls == [
        (
            ["/usr/sbin/ip", "route", "show", "default"],
            {"capture_output": True, "text": True},
        )
    ]


def test_default_uplink_parser_keeps_missing_route_behavior():
    assert host_probes.parse_default_uplink("default via 192.0.2.1") is None
    assert host_probes.parse_default_uplink("") is None


def test_default_uplink_execution_error_policies_are_unchanged(monkeypatch):
    failure = FileNotFoundError("ip unavailable")

    def runner(_argv, **_kwargs):
        raise failure

    monkeypatch.setattr(host_probes.subprocess, "run", runner)
    monkeypatch.setattr(host_probes.shutil, "which", lambda _name: None)

    assert network_tuning._default_uplink_iface() is None
    with pytest.raises(FileNotFoundError) as exc:
        hostapd_nat_engine._default_uplink_iface()
    assert exc.value is failure
