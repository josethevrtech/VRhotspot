"""Automatic VRhotspot Wi-Fi enrollment regressions.

On real Quest 3S hardware `cmd wifi help` does not advertise
`connect-network`, yet invoking it directly succeeds ("Connection
initiated") and the headset enrolls on VRhotspot.  The daemon therefore
never probes `cmd wifi help` or `list-scan-results`: capability is
determined by attempting the command and verifying the observed network
state.  Acknowledgment (exit 0 / "Connection initiated") is never treated
as enrollment; wireless ADB is enabled only after the SSID, address,
gateway, and route are verified against the active VRhotspot network.
"""

from __future__ import annotations

import json
import subprocess

from vr_hotspotd.devtools.adb_operations import (
    RESULT_INVALID_REQUEST,
    RESULT_OK,
    RESULT_TOOLS_UNAVAILABLE,
)
from vr_hotspotd.devtools.wireless_bootstrap import (
    FALLBACK_CONNECT_COMMAND_REJECTED,
    FALLBACK_MANUAL_JOIN_PENDING,
    FALLBACK_VERIFICATION_TIMEOUT,
    RESULT_HOTSPOT_NOT_RUNNING,
    RESULT_USB_DISCONNECTED,
    RESULT_WIFI_CONTROL_UNAVAILABLE,
    WIRELESS_JOIN_ATTEMPTS,
    WIRELESS_NETWORK_POLL_ATTEMPTS,
    WIRELESS_NETWORK_POLL_INTERVAL_S,
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
    """Simulates the headset exactly as observed on Quest 3S hardware.

    `cmd wifi help` and `list-scan-results` are deliberately unsupported:
    any such invocation raises, proving the daemon never requires them.
    """

    def __init__(
        self,
        *,
        authorized: bool = True,
        initial_address: str = "192.168.68.23",
        initial_gateway: str = "192.168.68.1",
        initial_ssid: str = "VR-Hotspot",
        joined_address: str = "192.168.68.211",
        joined_gateway: str = "192.168.68.1",
        joined_ssid: str = "VR-Hotspot",
        join_acks: tuple = (True, True),
        join_effective_attempt: int | None = 1,
        join_visible_after_checks: int = 0,
        usb_drop_after_join: bool = False,
        tcpip_ok: bool = True,
        connect_failures: int = 0,
    ) -> None:
        self.authorized = authorized
        self.initial = (initial_address, initial_gateway, initial_ssid)
        self.target = (joined_address, joined_gateway, joined_ssid)
        self.join_acks = join_acks
        self.join_effective_attempt = join_effective_attempt
        self.join_visible_after_checks = join_visible_after_checks
        self.usb_drop_after_join = usb_drop_after_join
        self.tcpip_ok = tcpip_ok
        self.connect_failures = connect_failures
        self.join_calls = 0
        self.join_done = False
        self.visible_delay = 0
        self.connect_calls = 0
        self.calls: list[tuple[str, ...]] = []
        self.kwargs: list[dict] = []

    def network(self) -> tuple[str, str, str]:
        if self.join_done and self.visible_delay == 0:
            return self.target
        return self.initial

    def __call__(self, argv, **kwargs):
        command = tuple(argv)
        self.calls.append(command)
        self.kwargs.append(kwargs)

        if command[-1] == "get-state":
            if not self.authorized:
                return subprocess.CompletedProcess(argv, 1, b"", b"unauthorized\n")
            if self.usb_drop_after_join and self.join_calls > 0:
                return subprocess.CompletedProcess(
                    argv, 1, b"", b"error: device not found\n"
                )
            return subprocess.CompletedProcess(argv, 0, b"device\n", b"")

        if command[-2:] == ("getprop", "ro.product.model"):
            return subprocess.CompletedProcess(argv, 0, b"Quest_3S\n", b"")

        if command[-5:] == ("ip", "-4", "addr", "show", "wlan0"):
            # DHCP lease propagation: the joined network becomes visible only
            # after `join_visible_after_checks` post-join inspections.
            if self.join_done and self.visible_delay > 0:
                self.visible_delay -= 1
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

        if "connect-network" in command:
            self.join_calls += 1
            if (
                self.join_effective_attempt is not None
                and self.join_calls >= self.join_effective_attempt
                and not self.join_done
            ):
                self.join_done = True
                self.visible_delay = self.join_visible_after_checks
            index = min(self.join_calls, len(self.join_acks)) - 1
            if self.join_acks[index]:
                return subprocess.CompletedProcess(
                    argv, 0, b"Connection initiated\n", b""
                )
            return subprocess.CompletedProcess(
                argv, 1, b"", b"Exception: unknown command 'connect-network'\n"
            )

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
            address, _gateway, _ssid = self.network()
            return subprocess.CompletedProcess(
                argv,
                0,
                f"connected to {address}:5555\n".encode(),
                b"",
            )

        raise AssertionError(f"unexpected argv: {command!r}")


OTHER_NETWORK = {
    "initial_address": "192.168.1.88",
    "initial_gateway": "192.168.1.1",
    "initial_ssid": "HomeNetwork",
}


def run(runner: BootstrapRunner, *, sleeps: list | None = None, **overrides):
    recorded = sleeps if sleeps is not None else []
    return enable_wireless_adb(
        "USB123",
        tools_status=TOOLS_STATUS,
        config=dict(CONFIG, **overrides.pop("config", {})),
        state=overrides.pop("state", RUNNING_STATE),
        runner=runner,
        sleeper=recorded.append,
        **overrides,
    )


def join_calls(runner: BootstrapRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if "connect-network" in call]


def assert_never_probes_capability(runner: BootstrapRunner) -> None:
    assert not any("help" in call for call in runner.calls)
    assert not any("list-scan-results" in call for call in runner.calls)


def assert_passphrase_confined_to_join_argv(runner: BootstrapRunner) -> None:
    for call in runner.calls:
        if "connect-network" not in call:
            assert PASSPHRASE not in call


def test_stopped_hotspot_prevents_every_adb_subprocess() -> None:
    runner = BootstrapRunner()

    result = run(runner, state={"running": False, "phase": "stopped"})

    assert result["success"] is False
    assert result["result_code"] == RESULT_HOTSPOT_NOT_RUNNING
    assert runner.calls == []


def test_already_on_vrhotspot_skips_enrollment_and_verifies_before_tcpip() -> None:
    runner = BootstrapRunner()

    result = run(runner)

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert result["data"]["target"] == "192.168.68.23:5555"
    assert result["data"]["subnet"] == "192.168.68.0/24"
    assert result["data"]["transport"] == "vrhotspot"
    assert result["data"]["automatic_join_attempted"] is False
    assert result["data"]["automatic_join_attempts"] == 0
    assert result["data"]["automatic_join_succeeded"] is False
    tcp_index = runner.calls.index((ADB, "-s", "USB123", "tcpip", "5555"))
    assert any(
        call[-5:] == ("ip", "-4", "addr", "show", "wlan0")
        for call in runner.calls[:tcp_index]
    )
    assert any(
        call[-3:] == ("cmd", "wifi", "status")
        for call in runner.calls[:tcp_index]
    )
    assert join_calls(runner) == []
    assert_never_probes_capability(runner)
    assert all(kwargs["shell"] is False for kwargs in runner.kwargs)


def test_direct_connect_network_succeeds_without_help_probe() -> None:
    """connect-network absent from help output must not matter: the daemon
    invokes the command directly with `-r none` and verifies the result."""
    runner = BootstrapRunner(**OTHER_NETWORK)

    result = run(runner)

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert result["data"]["automatic_join_attempted"] is True
    assert result["data"]["automatic_join_attempts"] == 1
    assert result["data"]["automatic_join_succeeded"] is True
    assert result["data"]["ip"] == "192.168.68.211"
    assert "requires_manual_join" not in result["data"]
    (join_call,) = join_calls(runner)
    assert join_call[-5:] == ("VR-Hotspot", "wpa2", PASSPHRASE, "-r", "none")
    tcp_index = runner.calls.index((ADB, "-s", "USB123", "tcpip", "5555"))
    assert runner.calls.index(join_call) < tcp_index
    assert_never_probes_capability(runner)
    assert PASSPHRASE not in json.dumps(result)


def test_acknowledgment_without_verified_network_never_enables_tcpip() -> None:
    """Exit 0 / "Connection initiated" is acknowledgment only.  Without a
    verified VRhotspot network state, wireless ADB stays disabled and the
    manual fallback appears only after both attempts."""
    runner = BootstrapRunner(**OTHER_NETWORK, join_effective_attempt=None)

    result = run(runner)

    assert result["success"] is False
    assert result["result_code"] == RESULT_WIFI_CONTROL_UNAVAILABLE
    assert result["data"]["requires_manual_join"] is True
    assert result["data"]["fallback_reason"] == FALLBACK_VERIFICATION_TIMEOUT
    assert result["data"]["automatic_join_attempted"] is True
    assert result["data"]["automatic_join_attempts"] == WIRELESS_JOIN_ATTEMPTS
    assert result["data"]["automatic_join_succeeded"] is False
    assert len(join_calls(runner)) == WIRELESS_JOIN_ATTEMPTS
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)
    inspections = [
        call for call in runner.calls
        if call[-5:] == ("ip", "-4", "addr", "show", "wlan0")
    ]
    assert len(inspections) == (
        1 + WIRELESS_JOIN_ATTEMPTS * WIRELESS_NETWORK_POLL_ATTEMPTS
    )
    assert PASSPHRASE not in json.dumps(result)


def test_polling_window_covers_at_least_25_seconds_per_attempt() -> None:
    assert 25.0 <= (
        (WIRELESS_NETWORK_POLL_ATTEMPTS - 1) * WIRELESS_NETWORK_POLL_INTERVAL_S
    ) <= 30.0


def test_delayed_dhcp_is_awaited_within_single_attempt() -> None:
    runner = BootstrapRunner(**OTHER_NETWORK, join_visible_after_checks=6)
    sleeps: list = []

    result = run(runner, sleeps=sleeps)

    assert result["success"] is True
    assert result["data"]["automatic_join_attempts"] == 1
    assert result["data"]["automatic_join_succeeded"] is True
    assert result["data"]["ip"] == "192.168.68.211"
    assert sleeps.count(WIRELESS_NETWORK_POLL_INTERVAL_S) >= 5


def test_first_attempt_timeout_second_attempt_succeeds_without_fallback() -> None:
    runner = BootstrapRunner(**OTHER_NETWORK, join_effective_attempt=2)

    result = run(runner)

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert result["data"]["automatic_join_attempts"] == 2
    assert result["data"]["automatic_join_succeeded"] is True
    assert "requires_manual_join" not in result["data"]
    assert len(join_calls(runner)) == 2


def test_rejected_connect_command_falls_back_after_both_attempts() -> None:
    runner = BootstrapRunner(
        initial_address="10.0.0.25",
        initial_gateway="10.0.0.1",
        initial_ssid="OfficeWiFi",
        join_acks=(False, False),
        join_effective_attempt=None,
    )

    result = run(runner)

    assert result["success"] is False
    assert result["result_code"] == RESULT_WIFI_CONTROL_UNAVAILABLE
    assert result["data"]["requires_manual_join"] is True
    assert result["data"]["fallback_reason"] == FALLBACK_CONNECT_COMMAND_REJECTED
    assert result["data"]["ssid"] == "VR-Hotspot"
    assert len(join_calls(runner)) == WIRELESS_JOIN_ATTEMPTS
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)
    assert_never_probes_capability(runner)
    assert PASSPHRASE not in json.dumps(result)


def test_wrong_ssid_after_join_never_enables_tcpip() -> None:
    runner = BootstrapRunner(**OTHER_NETWORK, joined_ssid="NeighborNet")

    result = run(runner)

    assert result["success"] is False
    assert result["data"]["requires_manual_join"] is True
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


def test_address_outside_subnet_after_join_never_enables_tcpip() -> None:
    runner = BootstrapRunner(**OTHER_NETWORK, joined_address="192.168.50.7")

    result = run(runner)

    assert result["success"] is False
    assert result["data"]["requires_manual_join"] is True
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


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
    assert result["data"]["requires_manual_join"] is True
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


def test_usb_disconnect_during_enrollment_stops_cleanly() -> None:
    runner = BootstrapRunner(**OTHER_NETWORK, usb_drop_after_join=True)

    result = run(runner)

    assert result["success"] is False
    assert result["result_code"] == RESULT_USB_DISCONNECTED
    assert result["data"].get("requires_manual_join") is None
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)
    assert PASSPHRASE not in json.dumps(result)


def test_manual_polling_mode_skips_connect_network() -> None:
    """Background rechecks during the manual fallback pass auto_join=False
    and must never re-run connect-network."""
    runner = BootstrapRunner(**OTHER_NETWORK)

    result = run(runner, auto_join=False)

    assert result["success"] is False
    assert result["result_code"] == RESULT_WIFI_CONTROL_UNAVAILABLE
    assert result["data"]["requires_manual_join"] is True
    assert result["data"]["fallback_reason"] == FALLBACK_MANUAL_JOIN_PENDING
    assert join_calls(runner) == []
    assert not any(call[-2:] == ("tcpip", "5555") for call in runner.calls)


def test_manual_polling_mode_still_completes_once_verified() -> None:
    runner = BootstrapRunner()

    result = run(runner, auto_join=False)

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert join_calls(runner) == []


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


def test_passphrase_confined_to_join_argv_in_every_scenario() -> None:
    """The secret appears only inside the fixed connect-network argv and in
    no public response of any outcome."""
    scenarios = [
        BootstrapRunner(),
        BootstrapRunner(**OTHER_NETWORK),
        BootstrapRunner(**OTHER_NETWORK, join_effective_attempt=None),
        BootstrapRunner(**OTHER_NETWORK, join_acks=(False, False),
                        join_effective_attempt=None),
        BootstrapRunner(**OTHER_NETWORK, usb_drop_after_join=True),
        BootstrapRunner(tcpip_ok=False),
    ]
    for runner in scenarios:
        result = run(runner)
        assert PASSPHRASE not in json.dumps(result)
        assert_passphrase_confined_to_join_argv(runner)
        assert all(kwargs["shell"] is False for kwargs in runner.kwargs)
