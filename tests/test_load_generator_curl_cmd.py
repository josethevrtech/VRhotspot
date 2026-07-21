from types import SimpleNamespace

import pytest

from vr_hotspotd.diagnostics import load
from vr_hotspotd.diagnostics import limits


def test_curl_cmd_contains_limit_and_output(monkeypatch):
    captured = {}

    def fake_popen(cmd, **_kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda timeout=None: None, kill=lambda: None)

    monkeypatch.setattr(load.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None)
    monkeypatch.setattr(load.subprocess, "Popen", fake_popen)

    gen = load.LoadGenerator(
        method="curl",
        mbps=100.0,
        duration_s=10,
        url="https://example.com/file",
        iperf3_host="",
        iperf3_port=5201,
    )
    gen.start()

    cmd = captured["cmd"]
    assert "--limit-rate" in cmd
    assert "--max-time" in cmd
    assert "/dev/null" in cmd
    assert cmd[cmd.index("--limit-rate") + 1] == str(int(100.0 * 1_000_000 / 8.0))
    assert cmd[cmd.index("--max-time") + 1] == "12"
    assert "-L" not in cmd
    assert "--location" not in cmd
    assert cmd[1] == "--disable"
    assert "--globoff" in cmd
    assert "--no-location" in cmd
    assert cmd[cmd.index("--max-redirs") + 1] == "0"
    assert cmd[cmd.index("--proto") + 1] == "=http,https"


@pytest.mark.parametrize(
    "url",
    (
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/1",
        "$(touch /tmp/vrhotspot-test)",
        "https://example.com; touch /tmp/vrhotspot-test",
    ),
)
def test_curl_url_rejects_unsupported_or_shell_like_values(url):
    with pytest.raises(ValueError):
        load.validate_curl_url(url)


def test_invalid_curl_url_never_starts_a_process(monkeypatch):
    monkeypatch.setattr(load.shutil, "which", lambda name: "/usr/bin/curl")

    def fail_popen(*_args, **_kwargs):
        pytest.fail("invalid curl URL reached subprocess.Popen")

    monkeypatch.setattr(load.subprocess, "Popen", fail_popen)
    gen = load.LoadGenerator(
        method="curl",
        mbps=100.0,
        duration_s=10,
        url="file:///etc/passwd",
        iperf3_host="",
        iperf3_port=5201,
    )

    gen.start()

    assert gen.started is False
    assert "load_params_invalid" in gen.notes


def test_iperf3_command_inputs_are_bounded(monkeypatch):
    captured = {}

    def fake_popen(cmd, **_kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            poll=lambda: None,
            terminate=lambda: None,
            wait=lambda timeout=None: None,
            kill=lambda: None,
        )

    monkeypatch.setattr(
        load.shutil,
        "which",
        lambda name: "/usr/bin/iperf3" if name == "iperf3" else None,
    )
    monkeypatch.setattr(load.subprocess, "Popen", fake_popen)
    gen = load.LoadGenerator(
        method="iperf3",
        mbps=999_999.0,
        duration_s=999,
        url="",
        iperf3_host="example.com",
        iperf3_port=999_999,
    )

    gen.start()

    cmd = captured["cmd"]
    assert cmd[cmd.index("-t") + 1] == str(limits.LOAD_MAX_DURATION_S)
    assert cmd[cmd.index("-p") + 1] == str(limits.LOAD_MAX_PORT)
    assert cmd[cmd.index("-b") + 1] == f"{limits.LOAD_MAX_MBPS}M"
    assert gen.requested_mbps == limits.LOAD_MAX_MBPS


@pytest.mark.parametrize(
    "host",
    ("", "-R", "bad host", "example.com;id", "a" * 254),
)
def test_iperf3_host_rejects_invalid_or_option_like_values(host):
    with pytest.raises(ValueError):
        load.validate_network_host(host)
