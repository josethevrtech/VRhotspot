from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vr_hotspotd.diagnostics import ping
from vr_hotspotd.diagnostics import limits


class TestDiagnosticsPing(unittest.TestCase):
    def test_ping_parsing_percentiles(self):
        output = """64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=10.0 ms
64 bytes from 192.168.1.1: icmp_seq=2 ttl=64 time=20.0 ms
64 bytes from 192.168.1.1: icmp_seq=3 ttl=64 time=30.0 ms
64 bytes from 192.168.1.1: icmp_seq=4 ttl=64 time=40.0 ms

4 packets transmitted, 4 received, 0% packet loss, time 3005ms
"""

        with patch.object(ping.shutil, "which", return_value="/bin/ping"), patch.object(
            ping.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=output, stderr=""),
        ):
            res = ping.run_ping("192.168.1.1", duration_s=1, interval_ms=20, timeout_s=1)

        self.assertEqual(res["sent"], 4)
        self.assertEqual(res["received"], 4)
        self.assertEqual(res["packet_loss_pct"], 0.0)

        rtt = res["rtt_ms"]
        self.assertEqual(rtt["min"], 10.0)
        self.assertEqual(rtt["avg"], 25.0)
        self.assertAlmostEqual(rtt["p50"], 25.0, places=6)
        self.assertAlmostEqual(rtt["p95"], 38.5, places=6)
        self.assertAlmostEqual(rtt["p99"], 39.7, places=6)
        self.assertAlmostEqual(rtt["p99_9"], 39.97, places=6)

    def test_ping_command_and_subprocess_timeout_are_bounded(self):
        captured = {}
        output = "1 packets transmitted, 1 received, 0% packet loss\n"

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        with patch.object(ping.shutil, "which", return_value="/bin/ping"), patch.object(
            ping.subprocess,
            "run",
            fake_run,
        ):
            res = ping.run_ping(
                "192.168.1.1",
                duration_s=999,
                interval_ms=0,
                timeout_s=999,
                count=999_999,
                packet_size=999_999,
            )

        cmd = captured["cmd"]
        assert cmd[cmd.index("-c") + 1] == str(limits.DIAGNOSTIC_MAX_PACKET_COUNT)
        assert cmd[cmd.index("-i") + 1] == "0.010"
        assert cmd[cmd.index("-w") + 1] == str(limits.DIAGNOSTIC_MAX_DURATION_S)
        assert cmd[cmd.index("-W") + 1] == str(limits.PING_MAX_REPLY_TIMEOUT_S)
        assert cmd[cmd.index("-s") + 1] == str(limits.DIAGNOSTIC_MAX_PACKET_SIZE)
        assert captured["kwargs"]["timeout"] == (
            limits.DIAGNOSTIC_MAX_DURATION_S + limits.PING_SUBPROCESS_GRACE_S
        )
        assert res["duration_s"] == limits.DIAGNOSTIC_MAX_DURATION_S
        assert res["interval_ms"] == limits.DIAGNOSTIC_MIN_INTERVAL_MS

    def test_ping_subprocess_timeout_returns_structured_error(self):
        def fake_run(cmd, **kwargs):
            raise ping.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        with patch.object(ping.shutil, "which", return_value="/bin/ping"), patch.object(
            ping.subprocess,
            "run",
            fake_run,
        ):
            res = ping.run_ping("192.168.1.1", duration_s=3)

        assert res["error"]["code"] == "ping_timeout"
