from types import SimpleNamespace

from vr_hotspotd.diagnostics import load


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
