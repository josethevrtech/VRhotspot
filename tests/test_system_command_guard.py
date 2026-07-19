import os
from pathlib import Path
import subprocess

import pytest


ALLOW_ENV = "VR_HOTSPOT_TEST_ALLOW_REAL_SYSTEM_COMMANDS"
BLOCKED_COMMANDS = (
    "nmcli",
    "iw",
    "iwctl",
    "rfkill",
    "systemctl",
    "firewall-cmd",
    "sudo",
    "ip",
    "iptables",
    "nft",
    "hostapd",
    "dnsmasq",
)


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_guard_blocks_every_system_command(
    command,
    block_real_system_commands,
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    harmless_missing_command = tmp_path / command

    with pytest.raises(AssertionError) as exc_info:
        subprocess.run([harmless_missing_command, "--version"], check=False)

    assert f"'{command}'" in str(exc_info.value)
    block_real_system_commands.clear()


@pytest.mark.parametrize(
    "execution_path",
    (
        "subprocess.run",
        "subprocess.check_output",
        "subprocess.check_call",
        "subprocess.Popen",
        "os.system",
        "os.popen",
    ),
)
def test_guard_covers_common_execution_paths(
    execution_path,
    block_real_system_commands,
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    harmless_missing_command = tmp_path / "nmcli"
    module_name, function_name = execution_path.split(".")
    module = subprocess if module_name == "subprocess" else os
    execute = getattr(module, function_name)

    with pytest.raises(AssertionError, match="blocked real system command 'nmcli'"):
        execute([harmless_missing_command] if module is subprocess else str(harmless_missing_command))

    block_real_system_commands.clear()


@pytest.mark.parametrize(
    "command",
    (
        "/usr/bin/nmcli device disconnect wlan0",
        "echo safe; /usr/sbin/rfkill block wifi",
        "bash -c 'systemctl stop NetworkManager'",
    ),
)
def test_guard_checks_shell_command_positions(
    command,
    block_real_system_commands,
    monkeypatch,
):
    monkeypatch.delenv(ALLOW_ENV, raising=False)

    with pytest.raises(AssertionError):
        os.system(command)

    block_real_system_commands.clear()


def test_guard_allows_only_explicit_environment_escape_hatch(monkeypatch, tmp_path):
    harmless_command = tmp_path / "nmcli"
    harmless_command.write_text("#!/bin/sh\nprintf allowed\n", encoding="utf-8")
    harmless_command.chmod(0o755)
    monkeypatch.setenv(ALLOW_ENV, "1")

    completed = subprocess.run(
        [Path(harmless_command)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "allowed"
