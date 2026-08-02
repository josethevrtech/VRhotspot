from __future__ import annotations

import subprocess

from vr_hotspotd.devtools.adb_operations import (
    RESULT_FAILED,
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.wireless_bootstrap import enable_wireless_adb


ADB = "/var/lib/vr-hotspot/devtools/platform-tools/adb"
TOOLS_STATUS = {"adb": {"path": ADB}}


class BootstrapRunner:
    def __init__(
        self,
        *,
        authorized: bool = True,
        route: str = "default via 192.168.0.1 dev wlan0 src 192.168.0.98 metric 303",
        fallback_address: str = "",
        tcpip_ok: bool = True,
        connect_failures: int = 0,
    ) -> None:
        self.authorized = authorized
        self.route = route
        self.fallback_address = fallback_address
        self.tcpip_ok = tcpip_ok
        self.connect_failures = connect_failures
        self.connect_calls = 0
        self.calls: list[tuple[str, ...]] = []
        self.kwargs: list[dict] = []

    def __call__(self, argv, **kwargs):
        command = tuple(argv)
        self.calls.append(command)
        self.kwargs.append(kwargs)

        if command[-1] == "get-state":
            if self.authorized:
                return subprocess.CompletedProcess(argv, 0, b"device\n", b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"error: device unauthorized\n")

        if command[-2:] == ("getprop", "ro.product.model"):
            return subprocess.CompletedProcess(argv, 0, b"Quest_3S\n", b"")

        if command[-2:] == ("ip", "route"):
            return subprocess.CompletedProcess(argv, 0, self.route.encode(), b"")

        if command[-6:] == ("ip", "-f", "inet", "addr", "show", "wlan0"):
            return subprocess.CompletedProcess(argv, 0, self.fallback_address.encode(), b"")

        if len(command) >= 2 and command[-2] == "tcpip":
            if self.tcpip_ok:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    b"restarting in TCP mode port: 5555\n",
                    b"",
                )
            return subprocess.CompletedProcess(argv, 1, b"", b"tcpip failed\n")

        if len(command) >= 2 and command[-2] == "connect":
            self.connect_calls += 1
            if self.connect_calls <= self.connect_failures:
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    b"",
                    b"failed to connect: Connection refused\n",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                b"connected to 192.168.0.98:5555\n",
                b"",
            )

        raise AssertionError(f"unexpected argv: {command!r}")


def test_enable_wireless_adb_runs_fixed_usb_to_tcp_sequence() -> None:
    runner = BootstrapRunner()

    result = enable_wireless_adb(
        "3487C10J3C0CBP",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert result["stage"] == "complete"
    assert result["data"] == {
        "usb_serial": "3487C10J3C0CBP",
        "port": 5555,
        "model": "Quest 3S",
        "ip": "192.168.0.98",
        "target": "192.168.0.98:5555",
    }
    assert runner.calls == [
        (ADB, "-s", "3487C10J3C0CBP", "get-state"),
        (ADB, "-s", "3487C10J3C0CBP", "shell", "getprop", "ro.product.model"),
        (ADB, "-s", "3487C10J3C0CBP", "shell", "ip", "route"),
        (ADB, "-s", "3487C10J3C0CBP", "tcpip", "5555"),
        (ADB, "connect", "192.168.0.98:5555"),
    ]
    assert all(call["shell"] is False for call in runner.kwargs)
    assert all(call["input"] is None for call in runner.kwargs)


def test_enable_wireless_adb_explains_usb_authorization() -> None:
    runner = BootstrapRunner(authorized=False)

    result = enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_FAILED
    assert result["stage"] == "authorize"
    assert "approve the USB debugging prompt" in result["message"]
    assert runner.calls == [(ADB, "-s", "USB123", "get-state")]


def test_enable_wireless_adb_uses_wlan_fallback_address() -> None:
    runner = BootstrapRunner(
        route="default via 192.168.0.1 dev wlan0",
        fallback_address="4: wlan0 inet 10.39.107.42/24 brd 10.39.107.255 scope global wlan0",
    )

    result = enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is True
    assert result["data"]["target"] == "10.39.107.42:5555"
    assert (ADB, "-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0") in runner.calls


def test_enable_wireless_adb_reports_missing_wifi_address() -> None:
    runner = BootstrapRunner(route="default via 192.168.0.1 dev wlan0")

    result = enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["stage"] == "wifi"
    assert "Connect the headset to Wi-Fi" in result["message"]
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


def test_enable_wireless_adb_retries_network_connection() -> None:
    runner = BootstrapRunner(connect_failures=2)

    result = enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is True
    assert runner.connect_calls == 3


def test_enable_wireless_adb_rejects_network_serial_without_running_adb() -> None:
    runner = BootstrapRunner()

    result = enable_wireless_adb(
        "192.168.0.98:5555",
        tools_status=TOOLS_STATUS,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_INVALID_REQUEST
    assert result["stage"] == "validate"
    assert runner.calls == []


def test_enable_wireless_adb_reports_missing_tools() -> None:
    result = enable_wireless_adb(
        "USB123",
        tools_status={"adb": {"path": None}},
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_TOOLS_UNAVAILABLE
    assert result["stage"] == "validate"
