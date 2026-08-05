from __future__ import annotations

import json
import subprocess

from vr_hotspotd.devtools.adb_operations import (
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.wireless_bootstrap import (
    RESULT_HEADSET_ROUTE_MISMATCH,
    RESULT_HOTSPOT_NOT_RUNNING,
    RESULT_WIFI_CONTROL_UNAVAILABLE,
    enable_wireless_adb,
)


ADB = "/var/lib/vr-hotspot/devtools/platform-tools/adb"
TOOLS_STATUS = {"adb": {"path": ADB}}
PASSPHRASE = "correct-horse-battery"
CONFIG = {
    "ssid": "VR-Hotspot",
    "wpa2_passphrase": PASSPHRASE,
    "ap_security": "wpa2",
    "lan_gateway_ip": "192.168.68.1",
    "dhcp_start_ip": "192.168.68.10",
    "dhcp_end_ip": "192.168.68.250",
    "ap_adapter": "wlan1",
}
RUNNING_STATE = {
    "running": True,
    "phase": "running",
    "ap_interface": "x0wlan1",
}


class BootstrapRunner:
    def __init__(
        self,
        *,
        authorized: bool = True,
        initial_address: str = "192.168.68.23",
        initial_gateway: str = "192.168.68.1",
        initial_ssid: str = "VR-Hotspot",
        joined_address: str = "192.168.68.23",
        joined_gateway: str = "192.168.68.1",
        joined_ssid: str = "VR-Hotspot",
        wifi_control: bool = True,
        join_ok: bool = True,
        tcpip_ok: bool = True,
        connect_failures: int = 0,
    ) -> None:
        self.authorized = authorized
        self.initial_address = initial_address
        self.initial_gateway = initial_gateway
        self.initial_ssid = initial_ssid
        self.joined_address = joined_address
        self.joined_gateway = joined_gateway
        self.joined_ssid = joined_ssid
        self.wifi_control = wifi_control
        self.join_ok = join_ok
        self.tcpip_ok = tcpip_ok
        self.connect_failures = connect_failures
        self.joined = False
        self.connect_calls = 0
        self.calls: list[tuple[str, ...]] = []
        self.kwargs: list[dict] = []

    def network(self) -> tuple[str, str, str]:
        if self.joined:
            return self.joined_address, self.joined_gateway, self.joined_ssid
        return self.initial_address, self.initial_gateway, self.initial_ssid

    def __call__(self, argv, **kwargs):
        command = tuple(argv)
        self.calls.append(command)
        self.kwargs.append(kwargs)

        if command[-1] == "get-state":
            if self.authorized:
                return subprocess.CompletedProcess(argv, 0, b"device\n", b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"unauthorized\n")

        if command[-2:] == ("getprop", "ro.product.model"):
            return subprocess.CompletedProcess(argv, 0, b"Quest_3S\n", b"")

        if command[-5:] == ("ip", "-4", "addr", "show", "wlan0"):
            address, _gateway, _ssid = self.network()
            value = (
                f"4: wlan0 inet {address}/24 brd 192.168.68.255 "
                "scope global wlan0\n"
            )
            return subprocess.CompletedProcess(argv, 0, value.encode(), b"")

        if command[-4:] == ("ip", "route", "show", "default"):
            address, gateway, _ssid = self.network()
            value = f"default via {gateway} dev wlan0 src {address}\n"
            return subprocess.CompletedProcess(argv, 0, value.encode(), b"")

        if command[-3:] == ("cmd", "wifi", "status"):
            _address, _gateway, ssid = self.network()
            value = (
                f'Wi-Fi is enabled\nWifiInfo: SSID: "{ssid}", '
                'BSSID: 00:11:22:33:44:55\n'
            )
            return subprocess.CompletedProcess(argv, 0, value.encode(), b"")

        if command[-3:] == ("cmd", "wifi", "help"):
            if self.wifi_control:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    b"connect-network <ssid> wpa2 <passphrase>\n",
                    b"",
                )
            return subprocess.CompletedProcess(argv, 1, b"", b"Unknown command\n")

        if "connect-network" in command:
            if self.join_ok:
                self.joined = True
                return subprocess.CompletedProcess(argv, 0, b"Connection initiated\n", b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"not supported\n")

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
                b"connected to 192.168.68.23:5555\n",
                b"",
            )

        raise AssertionError(f"unexpected argv: {command!r}")


def run(runner: BootstrapRunner, **overrides):
    return enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        config=dict(CONFIG, **overrides.pop("config", {})),
        state=overrides.pop("state", RUNNING_STATE),
        runner=runner,
        sleeper=lambda _seconds: None,
        **overrides,
    )


def test_stopped_hotspot_prevents_every_adb_subprocess() -> None:
    runner = BootstrapRunner()

    result = run(runner, state={"running": False, "phase": "stopped"})

    assert result["success"] is False
    assert result["result_code"] == RESULT_HOTSPOT_NOT_RUNNING
    assert runner.calls == []


def test_already_on_vrhotspot_is_verified_before_tcpip() -> None:
    runner = BootstrapRunner()

    result = run(runner)

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert result["data"]["target"] == "192.168.68.23:5555"
    assert result["data"]["subnet"] == "192.168.68.0/24"
    assert result["data"]["transport"] == "vrhotspot"
    tcp_index = runner.calls.index((ADB, "-s", "USB123", "tcpip", "5555"))
    assert any(
        call[-5:] == ("ip", "-4", "addr", "show", "wlan0")
        for call in runner.calls[:tcp_index]
    )
    assert any(
        call[-3:] == ("cmd", "wifi", "status")
        for call in runner.calls[:tcp_index]
    )
    assert not any("connect-network" in call for call in runner.calls)
    assert all(kwargs["shell"] is False for kwargs in runner.kwargs)


def test_other_network_is_joined_and_reverified_before_tcpip() -> None:
    runner = BootstrapRunner(
        initial_address="192.168.1.88",
        initial_gateway="192.168.1.1",
        initial_ssid="HomeNetwork",
    )

    result = run(runner)

    assert result["success"] is True
    join_call = next(call for call in runner.calls if "connect-network" in call)
    assert join_call[-3:] == ("VR-Hotspot", "wpa2", PASSPHRASE)
    tcp_index = runner.calls.index((ADB, "-s", "USB123", "tcpip", "5555"))
    join_index = runner.calls.index(join_call)
    assert join_index < tcp_index
    assert json.dumps(result).find(PASSPHRASE) == -1


def test_unavailable_wifi_control_enters_guided_fallback_without_tcpip() -> None:
    runner = BootstrapRunner(
        initial_address="10.0.0.25",
        initial_gateway="10.0.0.1",
        initial_ssid="OfficeWiFi",
        wifi_control=False,
    )

    result = run(runner)

    assert result["success"] is False
    assert result["result_code"] == RESULT_WIFI_CONTROL_UNAVAILABLE
    assert result["data"]["requires_manual_join"] is True
    assert result["data"]["ssid"] == "VR-Hotspot"
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)
    assert PASSPHRASE not in json.dumps(result)


def test_route_mismatch_never_enables_tcpip() -> None:
    runner = BootstrapRunner(
        initial_address="192.168.68.23",
        initial_gateway="192.168.68.254",
        initial_ssid="OtherNetwork",
        joined_address="192.168.68.23",
        joined_gateway="192.168.68.254",
    )

    result = run(runner)

    assert result["success"] is False
    assert result["result_code"] == RESULT_HEADSET_ROUTE_MISMATCH
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


def test_connect_retries_are_bounded() -> None:
    runner = BootstrapRunner(connect_failures=2)

    result = run(runner)

    assert result["success"] is True
    assert runner.connect_calls == 3


def test_invalid_network_serial_is_rejected_before_adb() -> None:
    runner = BootstrapRunner()

    result = enable_wireless_adb(
        "192.168.68.23:5555",
        tools_status=TOOLS_STATUS,
        config=CONFIG,
        state=RUNNING_STATE,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_INVALID_REQUEST
    assert runner.calls == []


def test_missing_tools_is_reported_without_exposing_credentials() -> None:
    result = enable_wireless_adb(
        "USB123",
        tools_status={"adb": {"path": None}},
        config=CONFIG,
        state=RUNNING_STATE,
        sleeper=lambda _seconds: None,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_TOOLS_UNAVAILABLE
    assert PASSPHRASE not in json.dumps(result)
