from __future__ import annotations

import subprocess

from vr_hotspotd.devtools.adb_operations import execute_adb_operation


TOOLS = {"adb": {"path": "/opt/vrhotspot/platform-tools/adb"}}


def _completed(argv, *, stdout=b"", stderr=b"", returncode=0, **kwargs):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_version_uses_selected_adb_without_shell():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(argv, stdout=b"Android Debug Bridge version 1.0.41\n")

    result = execute_adb_operation("version", tools_status=TOOLS, runner=runner)

    assert result["success"] is True
    assert result["operation"] == "version"
    assert calls[0][0] == ["/opt/vrhotspot/platform-tools/adb", "version"]
    assert calls[0][1]["shell"] is False


def test_devices_parses_connected_headsets():
    output = (
        b"List of devices attached\n"
        b"192.168.68.24:5555 device product:hollywood model:Quest_3 device:hollywood\n"
        b"1WMHH123 unauthorized usb:1-2\n"
    )

    def runner(argv, **kwargs):
        return _completed(argv, stdout=output)

    result = execute_adb_operation("devices", tools_status=TOOLS, runner=runner)

    assert result["success"] is True
    devices = result["data"]["devices"]
    assert devices[0]["serial"] == "192.168.68.24:5555"
    assert devices[0]["state"] == "device"
    assert devices[0]["properties"]["model"] == "Quest_3"
    assert devices[1]["state"] == "unauthorized"


def test_pairing_code_is_sent_via_stdin_not_argv():
    observed = {}

    def runner(argv, **kwargs):
        observed["argv"] = argv
        observed["input"] = kwargs["input"]
        return _completed(argv, stdout=b"Successfully paired to 192.168.68.24:37143\n")

    result = execute_adb_operation(
        "pair",
        {"ip": "192.168.68.24", "port": 37143, "pairing_code": "123456"},
        tools_status=TOOLS,
        runner=runner,
    )

    assert result["success"] is True
    assert observed["argv"] == [
        "/opt/vrhotspot/platform-tools/adb",
        "pair",
        "192.168.68.24:37143",
    ]
    assert observed["input"] == b"123456\n"
    assert "123456" not in repr(result)


def test_connect_and_disconnect_build_typed_commands():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, stdout=b"ok\n")

    connect = execute_adb_operation(
        "connect",
        {"ip": "192.168.68.24", "port": 5555},
        tools_status=TOOLS,
        runner=runner,
    )
    disconnect = execute_adb_operation(
        "disconnect",
        {"serial": "192.168.68.24:5555"},
        tools_status=TOOLS,
        runner=runner,
    )

    assert connect["success"] is True
    assert disconnect["success"] is True
    assert calls == [
        ["/opt/vrhotspot/platform-tools/adb", "connect", "192.168.68.24:5555"],
        ["/opt/vrhotspot/platform-tools/adb", "disconnect", "192.168.68.24:5555"],
    ]


def test_invalid_inputs_are_rejected_before_execution():
    called = False

    def runner(argv, **kwargs):
        nonlocal called
        called = True
        return _completed(argv)

    bad_ip = execute_adb_operation(
        "connect",
        {"ip": "not-an-ip"},
        tools_status=TOOLS,
        runner=runner,
    )
    bad_code = execute_adb_operation(
        "pair",
        {"ip": "192.168.68.24", "port": 37143, "pairing_code": "12;456"},
        tools_status=TOOLS,
        runner=runner,
    )
    unsupported = execute_adb_operation(
        "shell",
        {"command": "rm -rf /"},
        tools_status=TOOLS,
        runner=runner,
    )

    assert called is False
    assert bad_ip["result_code"] == "invalid_request"
    assert bad_code["result_code"] == "invalid_request"
    assert unsupported["result_code"] == "invalid_request"


def test_missing_adb_reports_tools_unavailable():
    result = execute_adb_operation(
        "devices",
        tools_status={"adb": {"path": None}},
        runner=lambda argv, **kwargs: _completed(argv),
    )

    assert result["success"] is False
    assert result["result_code"] == "tools_unavailable"


def test_timeout_returns_structured_result():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    result = execute_adb_operation("devices", tools_status=TOOLS, runner=runner)

    assert result["success"] is False
    assert result["result_code"] == "timeout"
    assert result["returncode"] is None
