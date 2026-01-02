from types import SimpleNamespace

import pytest

from vr_hotspotd.diagnostics import ping


def test_ping_parsing_percentiles(monkeypatch):
    output = """64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=10.0 ms
64 bytes from 192.168.1.1: icmp_seq=2 ttl=64 time=20.0 ms
64 bytes from 192.168.1.1: icmp_seq=3 ttl=64 time=30.0 ms
64 bytes from 192.168.1.1: icmp_seq=4 ttl=64 time=40.0 ms

4 packets transmitted, 4 received, 0% packet loss, time 3005ms
"""

    monkeypatch.setattr(ping.shutil, "which", lambda _name: "/bin/ping")
    monkeypatch.setattr(
        ping.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )

    res = ping.run_ping("192.168.1.1", duration_s=1, interval_ms=20, timeout_s=1)
    assert res["sent"] == 4
    assert res["received"] == 4
    assert res["packet_loss_pct"] == 0.0

    rtt = res["rtt_ms"]
    assert rtt["min"] == 10.0
    assert rtt["avg"] == 25.0
    assert rtt["p50"] == pytest.approx(25.0)
    assert rtt["p95"] == pytest.approx(38.5, rel=1e-6)
    assert rtt["p99"] == pytest.approx(39.7, rel=1e-6)
    assert rtt["p99_9"] == pytest.approx(39.97, rel=1e-6)
