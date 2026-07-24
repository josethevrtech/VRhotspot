import socket

import pytest

from vr_hotspotd.diagnostics import devbridge


def test_classify_headset_hostname_matches_known_vendors():
    assert devbridge.classify_headset_hostname("Quest3-abc") == {
        "likelihood": devbridge.LIKELIHOOD_LIKELY,
        "vendor_guess": "Meta Quest",
    }
    assert devbridge.classify_headset_hostname("PICO-4-Ultra")["vendor_guess"] == "Pico"
    assert devbridge.classify_headset_hostname("laptop") == {
        "likelihood": devbridge.LIKELIHOOD_UNKNOWN,
        "vendor_guess": None,
    }
    assert devbridge.classify_headset_hostname(None)["likelihood"] == (
        devbridge.LIKELIHOOD_UNKNOWN
    )


def test_subnet_cidr_from_gateway():
    assert devbridge.subnet_cidr_from_gateway("192.168.68.1") == "192.168.68.0/24"
    assert devbridge.subnet_cidr_from_gateway(None) is None
    assert devbridge.subnet_cidr_from_gateway("not-an-ip") is None


def test_adb_connect_commands_use_ip_and_port():
    commands = devbridge.build_adb_connect_commands("192.168.68.23")
    assert commands["connect"] == "adb connect 192.168.68.23:5555"
    assert commands["disconnect"] == "adb disconnect 192.168.68.23:5555"
    assert commands["list_devices"] == "adb devices -l"
    assert commands["enable_tcpip_over_usb"] == "adb tcpip 5555"


def test_adb_connect_commands_fall_back_to_placeholder():
    commands = devbridge.build_adb_connect_commands(None)
    assert commands["connect"] == "adb connect <HEADSET_IP>:5555"


def test_logcat_commands_target_serial():
    commands = devbridge.build_logcat_commands("192.168.68.23")
    assert commands["follow"] == "adb -s 192.168.68.23:5555 logcat"
    assert commands["unity_only"] == "adb -s 192.168.68.23:5555 logcat -s Unity"
    assert commands["clear"] == "adb -s 192.168.68.23:5555 logcat -c"


def test_pairing_guidance_uses_placeholders_and_never_secrets():
    guidance = devbridge.build_pairing_guidance()
    assert guidance["commands"]["pair"] == (
        "adb pair <HEADSET_IP>:<PAIRING_PORT> <PAIRING_CODE>"
    )
    assert guidance["commands"]["connect_after_pairing"] == (
        "adb connect <HEADSET_IP>:5555"
    )
    assert guidance["steps"]


def test_probe_adb_tcp_reports_reachable(monkeypatch):
    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    seen = {}

    def fake_create_connection(address, timeout=None):
        seen["address"] = address
        seen["timeout"] = timeout
        return _FakeConnection()

    monkeypatch.setattr(devbridge.socket, "create_connection", fake_create_connection)
    result = devbridge.probe_adb_tcp("192.168.68.23")

    assert seen["address"] == ("192.168.68.23", 5555)
    assert seen["timeout"] == devbridge.ADB_PROBE_TIMEOUT_S
    assert result["checked"] is True
    assert result["reachable"] is True
    assert result["error"] is None
    assert result["latency_ms"] is not None


@pytest.mark.parametrize(
    "raised, expected_error",
    [
        (socket.timeout(), "timeout"),
        (ConnectionRefusedError(), "connection_refused"),
        (OSError("no route"), "unreachable"),
    ],
)
def test_probe_adb_tcp_reports_failures(monkeypatch, raised, expected_error):
    def fake_create_connection(_address, timeout=None):
        raise raised

    monkeypatch.setattr(devbridge.socket, "create_connection", fake_create_connection)
    result = devbridge.probe_adb_tcp("192.168.68.23")

    assert result["checked"] is True
    assert result["reachable"] is False
    assert result["error"] == expected_error
    assert result["latency_ms"] is None


def _snapshot(clients=(), ap_interface="x0wlan1", warnings=()):
    return {
        "conf_dir": None,
        "ap_interface": ap_interface,
        "clients": list(clients),
        "warnings": list(warnings),
        "sources": {"primary": "iw", "enrichment": ["dnsmasq_leases"]},
    }


def _client(**overrides):
    client = {
        "mac": "aa:bb:cc:dd:ee:ff",
        "ip": "192.168.68.23",
        "hostname": "Quest3",
        "source": "iw",
        "associated": True,
        "authorized": True,
        "signal_dbm": -40,
        "tx_bitrate_mbps": 866.7,
        "rx_bitrate_mbps": 866.7,
        "connected_time_s": 120,
    }
    client.update(overrides)
    return client


def test_devices_report_projects_clients_and_probes():
    probe = {
        "tcp_port": 5555,
        "checked": True,
        "reachable": True,
        "error": None,
        "latency_ms": 2.5,
    }
    report = devbridge.build_devbridge_devices_report(
        clients_snapshot=_snapshot([_client()]),
        adb_probes={"192.168.68.23": probe},
        probe_requested=True,
        gateway_ip="192.168.68.1",
    )

    assert report["schema_version"] == devbridge.DEVBRIDGE_SCHEMA_VERSION
    assert report["subnet_cidr"] == "192.168.68.0/24"
    assert report["summary"] == {
        "devices_detected": 1,
        "likely_headsets": 1,
        "adb_tcp_reachable": 1,
    }
    device = report["devices"][0]
    assert device["headset_likelihood"] == devbridge.LIKELIHOOD_LIKELY
    assert device["headset_vendor_guess"] == "Meta Quest"
    assert device["adb"]["reachable"] is True
    assert device["commands"]["connect"]["connect"] == "adb connect 192.168.68.23:5555"
    assert device["commands"]["logcat"]["follow"] == "adb -s 192.168.68.23:5555 logcat"


def test_devices_report_without_ip_has_no_commands_and_unchecked_probe():
    report = devbridge.build_devbridge_devices_report(
        clients_snapshot=_snapshot([_client(ip=None, hostname=None)]),
        adb_probes={},
        probe_requested=False,
        gateway_ip="192.168.68.1",
    )

    device = report["devices"][0]
    assert device["commands"] is None
    assert device["adb"]["checked"] is False
    assert device["adb"]["reachable"] is None
    assert report["probe_requested"] is False
    assert report["summary"]["adb_tcp_reachable"] == 0


def test_status_model_shape():
    state = {
        "running": True,
        "phase": "running",
        "adapter": "wlan1",
        "ap_interface": "x0wlan1",
    }
    config = {
        "ssid": "VR-Hotspot",
        "lan_gateway_ip": "192.168.68.1",
        "dhcp_start_ip": "192.168.68.10",
        "dhcp_end_ip": "192.168.68.250",
    }
    status = devbridge.build_devbridge_status(
        state=state,
        config=config,
        clients_snapshot=_snapshot([_client()]),
        host_adb_path="/usr/bin/adb",
    )

    assert set(status) == {
        "schema_version",
        "mode",
        "adb_tcp_port",
        "hotspot",
        "network",
        "host_adb",
        "summary",
        "warnings",
        "notes",
    }
    assert status["mode"] == "read_only"
    assert status["adb_tcp_port"] == 5555
    assert status["hotspot"]["ssid"] == "VR-Hotspot"
    assert status["network"]["subnet_cidr"] == "192.168.68.0/24"
    assert status["host_adb"] == {"present": True, "path": "/usr/bin/adb"}
    assert status["summary"] == {"devices_detected": 1, "likely_headsets": 1}


def test_adb_command_report_all_kinds_with_devices():
    report = devbridge.build_adb_command_report(
        clients=[_client()],
        host_adb_path="/usr/bin/adb",
    )

    assert report["kind"] == "all"
    assert report["host_adb"]["present"] is True
    assert report["host_adb"]["install_hint"] is None
    assert report["devices"][0]["ip"] == "192.168.68.23"
    assert "connect" in report["devices"][0]["commands"]
    assert "logcat" in report["devices"][0]["commands"]
    assert report["generic"]["connect"]["connect"] == "adb connect <HEADSET_IP>:5555"
    assert "pairing" in report
    assert report["warnings"] == []


def test_adb_command_report_kind_filters_sections():
    connect_report = devbridge.build_adb_command_report(clients=[_client()], kind="connect")
    assert "connect" in connect_report["generic"]
    assert "logcat" not in connect_report["generic"]
    assert "pairing" in connect_report

    logcat_report = devbridge.build_adb_command_report(clients=[_client()], kind="logcat")
    assert "logcat" in logcat_report["generic"]
    assert "connect" not in logcat_report["generic"]
    assert "pairing" not in logcat_report


def test_adb_command_report_requested_ip_not_detected_still_gets_commands():
    report = devbridge.build_adb_command_report(
        clients=[_client()],
        ip="192.168.68.99",
    )

    assert report["warnings"] == ["requested_ip_not_in_client_list"]
    assert report["devices"] == [
        {
            "ip": "192.168.68.99",
            "hostname": None,
            "commands": report["devices"][0]["commands"],
        }
    ]
    assert (
        report["devices"][0]["commands"]["connect"]["connect"]
        == "adb connect 192.168.68.99:5555"
    )


def _readiness(**overrides):
    kwargs = {
        "state": {
            "running": True,
            "phase": "running",
            "ap_interface": "x0wlan1",
        },
        "network_config_errors": (),
        "clients": [_client()],
        "adb_probes": {
            "192.168.68.23": {
                "tcp_port": 5555,
                "checked": True,
                "reachable": True,
                "error": None,
                "latency_ms": 2.0,
            }
        },
        "host_adb_path": "/usr/bin/adb",
        "probe_requested": True,
        "ssid": "VR-Hotspot",
    }
    kwargs.update(overrides)
    return devbridge.build_devbridge_readiness_report(**kwargs)


def test_readiness_all_pass_is_ready():
    report = _readiness()

    assert report["overall"] == devbridge.READINESS_READY
    assert [check["status"] for check in report["checks"]] == ["pass"] * 7
    assert [check["code"] for check in report["checks"]] == [
        "hotspot_running",
        "ap_interface_present",
        "network_config_valid",
        "host_adb_present",
        "devices_detected",
        "adb_tcp_reachable",
        "unity_build_and_run",
    ]
    assert report["summary"]["pass"] == 7
    unity = report["checks"][-1]
    assert unity["next_step_command"] == "adb connect 192.168.68.23:5555"


def test_readiness_hotspot_stopped_is_not_ready_with_copyable_next_steps():
    report = _readiness(
        state={"running": False, "phase": "stopped", "ap_interface": None},
        adb_probes={},
    )

    assert report["overall"] == devbridge.READINESS_NOT_READY
    by_code = {check["code"]: check for check in report["checks"]}
    assert by_code["hotspot_running"]["status"] == devbridge.CHECK_FAIL
    assert "curl" in by_code["hotspot_running"]["next_step_command"]
    assert "$VR_HOTSPOTD_API_TOKEN" in by_code["hotspot_running"]["next_step_command"]
    assert by_code["ap_interface_present"]["status"] == devbridge.CHECK_SKIPPED
    assert by_code["devices_detected"]["status"] == devbridge.CHECK_SKIPPED
    for check in report["checks"]:
        if check["status"] != devbridge.CHECK_PASS:
            assert check["next_step_command"], check["code"]


def test_readiness_no_reachable_device_warns_with_tcpip_next_step():
    report = _readiness(
        adb_probes={
            "192.168.68.23": {
                "tcp_port": 5555,
                "checked": True,
                "reachable": False,
                "error": "connection_refused",
                "latency_ms": None,
            }
        },
        host_adb_path=None,
    )

    assert report["overall"] == devbridge.READINESS_PARTIAL
    by_code = {check["code"]: check for check in report["checks"]}
    assert by_code["adb_tcp_reachable"]["status"] == devbridge.CHECK_WARNING
    assert by_code["adb_tcp_reachable"]["next_step_command"] == "adb tcpip 5555"
    assert by_code["host_adb_present"]["status"] == devbridge.CHECK_WARNING
    assert by_code["host_adb_present"]["next_step_command"] == "adb version"
    assert by_code["unity_build_and_run"]["status"] == devbridge.CHECK_SKIPPED


def test_readiness_probe_disabled_reports_unknown():
    report = _readiness(adb_probes={}, probe_requested=False)

    by_code = {check["code"]: check for check in report["checks"]}
    assert by_code["adb_tcp_reachable"]["status"] == devbridge.CHECK_UNKNOWN
    assert report["overall"] == devbridge.READINESS_PARTIAL


def test_collectors_never_probe_when_disabled(monkeypatch):
    monkeypatch.setattr(devbridge, "load_state", lambda: {"running": True, "phase": "running"})
    monkeypatch.setattr(
        devbridge,
        "load_config_snapshot",
        lambda: {"ssid": "VR-Hotspot", "lan_gateway_ip": "192.168.68.1"},
    )
    monkeypatch.setattr(
        devbridge,
        "get_clients_snapshot",
        lambda *_args, **_kwargs: _snapshot([_client()]),
    )
    monkeypatch.setattr(devbridge, "validate_network_config", lambda _cfg: [])
    monkeypatch.setattr(devbridge.shutil, "which", lambda _name: "/usr/bin/adb")

    def forbidden_probe(*_args, **_kwargs):
        raise AssertionError("probe must not run when probe_adb=False")

    monkeypatch.setattr(devbridge, "probe_adb_tcp", forbidden_probe)

    devices = devbridge.collect_devbridge_devices(probe_adb=False)
    readiness = devbridge.collect_devbridge_readiness(probe_adb=False)

    assert devices["probe_requested"] is False
    assert devices["devices"][0]["adb"]["checked"] is False
    assert readiness["probe_requested"] is False


def test_collect_adb_command_report_never_touches_the_network(monkeypatch):
    monkeypatch.setattr(devbridge, "load_state", lambda: {"running": True, "phase": "running"})
    monkeypatch.setattr(
        devbridge,
        "get_clients_snapshot",
        lambda *_args, **_kwargs: _snapshot([_client()]),
    )
    monkeypatch.setattr(devbridge.shutil, "which", lambda _name: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("collect_adb_command_report must not open sockets")

    monkeypatch.setattr(devbridge.socket, "create_connection", forbidden)

    report = devbridge.collect_adb_command_report(ip="192.168.68.23", kind="connect")

    assert report["devices"][0]["ip"] == "192.168.68.23"
    assert report["host_adb"]["present"] is False
    assert report["host_adb"]["install_hint"]
