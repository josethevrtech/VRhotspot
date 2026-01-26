from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vr_hotspotd.diagnostics import ping


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
