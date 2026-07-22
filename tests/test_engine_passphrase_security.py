import io
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vr_hotspotd import state
from vr_hotspotd.engine import (
    hostapd6_engine,
    hostapd_bridge_engine,
    hostapd_nat_engine,
    supervisor,
)
from vr_hotspotd.engine.hostapd6_cmd import build_cmd_6ghz
from vr_hotspotd.engine.hostapd_bridge_cmd import build_cmd_bridge
from vr_hotspotd.engine.hostapd_nat_cmd import build_cmd_nat
from vr_hotspotd.engine.lnxrouter_cmd import build_cmd
from vr_hotspotd.engine.secret_io import read_passphrase


SECRET = "argv-secret-123"


def _lnxrouter_command(secret: str):
    return build_cmd(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        band_preference="5ghz",
        country="US",
        channel=36,
        no_virt=True,
        wifi6=True,
    )


def _nat_command(secret: str):
    return build_cmd_nat(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        band="5ghz",
        ap_security="wpa2",
        country="US",
        channel=36,
        no_virt=True,
        debug=False,
        wifi6=True,
    )


def _six_ghz_command(secret: str):
    return build_cmd_6ghz(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        country="US",
        channel=5,
        no_virt=True,
        debug=False,
    )


def _bridge_command(secret: str):
    return build_cmd_bridge(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        band="5ghz",
        ap_security="wpa2",
        country="US",
        channel=36,
        no_virt=True,
        debug=False,
        wifi6=True,
        bridge_name="vrbr0",
        bridge_uplink="eth0",
    )


class _RunningProcess:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")

    def poll(self):
        return None


@pytest.mark.parametrize(
    ("build_command", "fd_flag", "expected_arguments"),
    [
        (_lnxrouter_command, "--password-fd", ("--ap", "wlan1", "--freq-band", "5")),
        (_nat_command, "--passphrase-fd", ("--ap-ifname", "wlan1", "--band", "5ghz")),
        (_six_ghz_command, "--passphrase-fd", ("--ap-ifname", "wlan1", "--channel", "5")),
        (_bridge_command, "--passphrase-fd", ("--bridge-name", "vrbr0", "--bridge-uplink", "eth0")),
    ],
)
def test_supervisor_passes_each_engine_passphrase_by_fd_not_argv(
    monkeypatch,
    tmp_path,
    build_command,
    fd_flag,
    expected_arguments,
):
    command = build_command(SECRET)
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        inherited_fd = kwargs["pass_fds"][0]
        captured["fd"] = inherited_fd
        captured["passphrase"] = os.read(inherited_fd, 4096).decode("utf-8")
        return _RunningProcess()

    supervisor._ln_proc = None
    monkeypatch.setattr(supervisor, "_build_engine_env", lambda: {"PATH": "/usr/bin"})
    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)

    result = supervisor.start_engine(
        command,
        early_fail_window_s=0,
        firewalld_cfg={"firewalld_enabled": False},
    )

    assert result.ok is True
    assert captured["passphrase"] == SECRET
    assert SECRET not in captured["argv"]
    assert fd_flag in captured["argv"]
    assert captured["kwargs"]["close_fds"] is True
    assert "shell" not in captured["kwargs"]
    for argument in expected_arguments:
        assert argument in captured["argv"]
    with pytest.raises(OSError):
        os.fstat(captured["fd"])

    assert result.cmd == supervisor._redact_cmd(command)
    result_payload = json.dumps(result.__dict__, sort_keys=True)
    assert SECRET not in result_payload
    assert fd_flag not in result_payload
    assert "********" in result.cmd
    assert SECRET not in "\n".join(result.stdout_tail + result.stderr_tail)

    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state, "STATE_TMP", tmp_path / "state.json.tmp")
    state.update_state(
        engine={
            "pid": result.pid,
            "cmd": result.cmd,
            "started_ts": result.started_ts,
            "last_error": result.error,
        }
    )
    persisted = state.STATE_PATH.read_text(encoding="utf-8")
    assert SECRET not in persisted
    assert fd_flag not in persisted
    assert "********" in persisted

    supervisor._ln_proc = None


def test_supervisor_spawn_failure_closes_fd_and_returns_no_passphrase(monkeypatch):
    command = _nat_command(SECRET)
    captured = {}

    def fail_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["fd"] = kwargs["pass_fds"][0]
        raise OSError("synthetic spawn failure")

    supervisor._ln_proc = None
    monkeypatch.setattr(supervisor, "_build_engine_env", lambda: {"PATH": "/usr/bin"})
    monkeypatch.setattr(supervisor.subprocess, "Popen", fail_popen)

    result = supervisor.start_engine(
        command,
        early_fail_window_s=0,
        firewalld_cfg={"firewalld_enabled": False},
    )

    assert result.ok is False
    assert SECRET not in captured["argv"]
    assert SECRET not in json.dumps(result.__dict__, sort_keys=True)
    assert result.cmd == supervisor._redact_cmd(command)
    with pytest.raises(OSError):
        os.fstat(captured["fd"])
    supervisor._ln_proc = None


def test_passphrase_reader_consumes_and_closes_inherited_fd():
    read_fd, write_fd = os.pipe()
    os.write(write_fd, SECRET.encode("utf-8"))
    os.close(write_fd)

    value = read_passphrase(SimpleNamespace(passphrase=None, passphrase_fd=read_fd))

    assert value == SECRET
    with pytest.raises(OSError):
        os.fstat(read_fd)


def test_vendor_lnxrouter_reads_password_fd_without_password_in_argv():
    read_fd, write_fd = os.pipe()
    os.write(write_fd, SECRET.encode("utf-8"))
    os.close(write_fd)
    argv = [
        str(Path(__file__).resolve().parents[1] / "backend" / "vendor" / "bin" / "lnxrouter"),
        "--password-fd",
        str(read_fd),
        "--version",
    ]
    try:
        completed = subprocess.run(
            argv,
            pass_fds=(read_fd,),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(read_fd)

    assert completed.returncode == 0
    assert "0.8.1" in completed.stdout
    assert SECRET not in argv
    assert SECRET not in completed.stdout
    assert SECRET not in completed.stderr


def _write_nat_config(path: Path, secret: str) -> None:
    hostapd_nat_engine._write_hostapd_conf(
        path=str(path),
        ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        country="US",
        band="5ghz",
        channel=36,
        ap_security="wpa2",
        wifi6=True,
    )


def _write_six_ghz_config(path: Path, secret: str) -> None:
    hostapd6_engine._write_hostapd_6ghz_conf(
        path=str(path),
        ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        country="US",
        channel=5,
    )


def _write_bridge_config(path: Path, secret: str) -> None:
    hostapd_bridge_engine._write_hostapd_conf(
        path=str(path),
        ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=secret,
        country="US",
        band="5ghz",
        channel=36,
        ap_security="wpa2",
        wifi6=True,
        bridge="vrbr0",
    )


@pytest.mark.parametrize(
    "write_config",
    [_write_nat_config, _write_six_ghz_config, _write_bridge_config],
)
def test_secret_bearing_hostapd_configs_are_mode_0600(tmp_path, write_config):
    config_path = tmp_path / "hostapd.conf"

    write_config(config_path, SECRET)

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert SECRET in config_path.read_text(encoding="utf-8")


def test_6ghz_spawn_failure_removes_protected_secret_config(
    monkeypatch,
    mock_missing_system_commands,
    tmp_path,
    capsys,
):
    args = SimpleNamespace(
        ap_ifname="wlan1",
        ssid="VR-Hotspot",
        passphrase=SECRET,
        country="US",
        channel=5,
        no_virt=True,
        debug=False,
        gateway_ip="192.168.68.1",
        dhcp_start="192.168.68.10",
        dhcp_end="192.168.68.250",
        dhcp_dns="gateway",
        no_internet=True,
        channel_width="80",
        beacon_interval=50,
        dtim_period=1,
        short_guard_interval=True,
        tx_power=None,
    )
    config_dir = tmp_path / "vr-hotspotd-6ghz.TEST"
    config_dir.mkdir()
    captured = {}

    def fail_popen(command, **_kwargs):
        config_path = Path(command[-1])
        captured["path"] = config_path
        captured["mode"] = stat.S_IMODE(config_path.stat().st_mode)
        captured["content"] = config_path.read_text(encoding="utf-8")
        raise OSError("synthetic hostapd spawn failure")

    monkeypatch.setattr(
        hostapd6_engine.argparse.ArgumentParser,
        "parse_args",
        lambda self: args,
    )
    monkeypatch.setattr(
        hostapd6_engine.tempfile,
        "mkdtemp",
        lambda prefix: str(config_dir),
    )
    monkeypatch.setattr(
        hostapd6_engine,
        "_resolve_binary",
        lambda name, env_key: f"/usr/sbin/{name}",
    )
    monkeypatch.setattr(hostapd6_engine, "_maybe_set_regdom", lambda _country: None)
    monkeypatch.setattr(hostapd6_engine, "_iface_up", lambda _ifname: None)
    monkeypatch.setattr(hostapd6_engine, "_assign_ip", lambda _ifname, _cidr: None)
    monkeypatch.setattr(hostapd6_engine, "_default_uplink_iface", lambda: None)
    monkeypatch.setattr(hostapd6_engine, "_ensure_ctrl_interface_dir", lambda _path: None)
    monkeypatch.setattr(hostapd6_engine.subprocess, "Popen", fail_popen)

    with pytest.raises(OSError, match="synthetic hostapd spawn failure"):
        hostapd6_engine.main()

    assert captured["mode"] == 0o600
    assert SECRET in captured["content"]
    assert not captured["path"].exists()
    assert not config_dir.exists()
    output = capsys.readouterr()
    assert SECRET not in output.out
    assert SECRET not in output.err
    assert str(captured["path"]) not in output.out
    assert str(captured["path"]) not in output.err
