import heapq
import socket
import struct
import threading
import time

import pytest

from vr_hotspotd.diagnostics import udp_latency as udp


class Simulation:
    def __init__(self, monkeypatch, reply=None):
        self.now = 10_000_000_000
        self.sent = []
        self.queue = []
        self.serial = 0
        self.closed = False
        self.reply = reply
        self.fail_send = False
        self.fail_receive = False
        self.stall_ns = 0
        self.nonces = []
        monkeypatch.setattr(udp.time, "monotonic_ns", lambda: self.now)
        monkeypatch.setattr(udp.time, "time", lambda: pytest.fail("wall clock used for RTT"))
        monkeypatch.setattr(udp.socket, "socket", lambda *_args: self)
        monkeypatch.setattr(udp.select, "select", self.select)

    def setblocking(self, blocking):
        assert blocking is False

    def setsockopt(self, *_args):
        pass

    def close(self):
        self.closed = True

    def sendto(self, data, peer):
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append((self.now, data, peer))
        if self.reply:
            self.reply(self, len(self.sent), data, peer)
        return len(data)

    def enqueue(self, delay_ms, data, peer):
        self.serial += 1
        heapq.heappush(self.queue, (self.now + round(delay_ms * 1_000_000), self.serial, data, peer))

    def select(self, readers, _writers, _errors, timeout):
        deadline = self.now + round(timeout * 1_000_000_000)
        if self.stall_ns:
            self.now += self.stall_ns
            self.stall_ns = 0
            return [], [], []
        if self.queue and self.queue[0][0] <= deadline:
            self.now = max(self.now, self.queue[0][0])
            return readers, [], []
        self.now = deadline
        return [], [], []

    def recvfrom(self, size):
        if self.fail_receive:
            raise OSError("receive failed")
        _, _, data, peer = heapq.heappop(self.queue)
        return data[:size], peer


def test_delayed_reordered_echo_does_not_block_sending(monkeypatch):
    def reply(sim, sequence, data, peer):
        sim.enqueue(100 if sequence == 1 else 2, data, peer)

    sim = Simulation(monkeypatch, reply)
    result = udp.run_udp_latency_test("192.168.1.2", count=5, interval_ms=20)

    assert [stamp - sim.sent[0][0] for stamp, _, _ in sim.sent] == [0, 20_000_000, 40_000_000, 60_000_000, 80_000_000]
    assert result["sent"] == result["received"] == 5
    assert result["samples_ms"] == [100.0, 2.0, 2.0, 2.0, 2.0]
    assert result["out_of_order_replies"] == 1
    assert result["late_replies"] == 1
    assert result["elapsed_s"] == pytest.approx(0.1)
    assert sim.closed


def test_spoofed_duplicate_and_modified_echoes_cannot_change_rtt(monkeypatch):
    def reply(sim, sequence, data, peer):
        if sequence == 1:
            sim.enqueue(1, data, ("192.168.1.9", peer[1]))
            sim.enqueue(2, data, (peer[0], peer[1] + 1))
            sim.enqueue(3, data[:16] + bytes([data[16] ^ 1]) + data[17:], peer)
            sim.enqueue(4, data[:-1] + b"x", peer)
            sim.enqueue(5, data[:8] + struct.pack("!Q", 1) + data[16:], peer)
            sim.enqueue(6, struct.pack("!Q", 0) + data[8:], peer)
            sim.enqueue(7, struct.pack("!Q", 1000) + data[8:], peer)
            sim.enqueue(8, data[:-1], peer)
            sim.enqueue(9, data + b"x", peer)
            sim.enqueue(10, data, peer)
            sim.enqueue(11, data, peer)
        else:
            sim.enqueue(10, data, peer)

    Simulation(monkeypatch, reply)
    result = udp.run_udp_latency_test("192.168.1.2", count=2, interval_ms=20)
    assert result["samples_ms"] == [10, 10]
    assert result["received"] == 2
    assert result["foreign_peer_replies"] == 2
    assert result["invalid_replies"] == 7
    assert result["duplicate_replies"] == 1
    assert result["packet_loss_pct"] == 0


def test_missing_bursts_and_jitter_do_not_bridge_unknown_sequences(monkeypatch):
    def reply(sim, sequence, data, peer):
        if sequence in {1, 4, 5, 8}:
            sim.enqueue(sequence, data, peer)

    Simulation(monkeypatch, reply)
    result = udp.run_udp_latency_test("192.168.1.2", count=9, interval_ms=20)
    assert result["missing_packets"] == 5
    assert result["missing_bursts"] == 3
    assert result["max_missing_burst_packets"] == 2
    assert result["max_missing_burst_nominal_ms"] == 40
    assert result["jitter_ms"]["avg"] == 1
    assert result["elapsed_s"] == pytest.approx(1.16)


def test_silent_endpoint_has_bounded_deadline_and_explanatory_error(monkeypatch):
    sim = Simulation(monkeypatch)
    result = udp.run_udp_latency_test("192.168.1.2", duration_s=1, interval_ms=10)
    assert result["sent"] == 100
    assert result["received"] == 0
    assert result["elapsed_s"] == pytest.approx(1.99)
    assert result["error"]["code"] == "no_samples"
    assert "endpoint" in result["error"]["message"]
    assert all(value is None for value in result["rtt_ms"].values())
    assert result["jitter_ms"]["avg"] is None
    assert sim.closed


@pytest.mark.parametrize("sample_count,p95,p99,p99_9", [(1, False, False, False), (19, False, False, False), (20, True, False, False), (99, True, False, False), (100, True, True, False), (999, True, True, False), (1000, True, True, True)])
def test_percentiles_require_enough_observations(monkeypatch, sample_count, p95, p99, p99_9):
    Simulation(monkeypatch, lambda sim, seq, data, peer: sim.enqueue(1, data, peer))
    result = udp.run_udp_latency_test("192.168.1.2", count=sample_count, duration_s=20, interval_ms=10)
    assert result["received"] == sample_count
    for name, available in (("p95", p95), ("p99", p99), ("p99_9", p99_9)):
        assert (result["rtt_ms"][name] is not None) == available
    assert result["rtt_ms"]["max"] == 1


def test_cancel_interrupts_silence_with_partial_results(monkeypatch):
    sim = Simulation(monkeypatch)
    initial = sim.now

    class Cancel:
        def is_set(self):
            return sim.now - initial >= 50_000_000

    result = udp.run_udp_latency_test("192.168.1.2", interval_ms=1000, cancel_event=Cancel())
    assert result["cancelled"] is True
    assert result["stop_reason"] == "cancelled"
    assert result["sent"] == 1
    assert result["elapsed_s"] <= 0.1
    assert "error" not in result
    assert sim.closed


def test_pre_cancelled_run_sends_nothing(monkeypatch):
    sim = Simulation(monkeypatch)
    cancelled = threading.Event()
    cancelled.set()
    result = udp.run_udp_latency_test("192.168.1.2", cancel_event=cancelled)
    assert result["sent"] == 0
    assert sim.sent == []
    assert result["cancelled"] is True


def test_scheduler_stall_never_triggers_catch_up_burst(monkeypatch):
    sim = Simulation(monkeypatch, lambda sim, seq, data, peer: sim.enqueue(1, data, peer))
    sim.stall_ns = 150_000_000
    result = udp.run_udp_latency_test("192.168.1.2", count=4, interval_ms=20)
    stamps = [stamp for stamp, _, _ in sim.sent]
    assert all(right - left >= 20_000_000 for left, right in zip(stamps, stamps[1:]))
    assert result["max_scheduling_delay_ms"] == 130


def test_receive_flood_has_hard_datagram_budget(monkeypatch):
    monkeypatch.setattr(udp, "_MAX_RECEIVE_DATAGRAMS", 20)

    def reply(sim, _sequence, data, peer):
        for _ in range(30):
            sim.enqueue(0, data, ("192.168.1.9", peer[1]))

    sim = Simulation(monkeypatch, reply)
    result = udp.run_udp_latency_test("192.168.1.2", count=1)
    assert result["error"]["code"] == "receive_budget_exhausted"
    assert result["foreign_peer_replies"] == 20
    assert sim.closed


@pytest.mark.parametrize("stage", ["send", "receive"])
def test_socket_errors_close_socket_and_preserve_counters(monkeypatch, stage):
    sim = Simulation(monkeypatch, lambda sim, seq, data, peer: sim.enqueue(1, data, peer))
    sim.fail_send = stage == "send"
    sim.fail_receive = stage == "receive"
    result = udp.run_udp_latency_test("192.168.1.2", count=2)
    assert result["error"]["code"] == ("udp_send_failed" if stage == "send" else "udp_test_failed")
    assert result["sent"] == (0 if stage == "send" else 1)
    assert sim.closed


@pytest.mark.parametrize("target", ["", "example.com", "::1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "192.168.1.2;id", None, True, 123])
def test_invalid_target_never_opens_socket(monkeypatch, target):
    monkeypatch.setattr(udp.socket, "socket", lambda *_args: pytest.fail("socket opened"))
    assert udp.run_udp_latency_test(target)["error"]["code"] == "invalid_target"


@pytest.mark.parametrize("field,value", [("count", True), ("count", 1.5), ("duration_s", float("nan")), ("interval_ms", float("inf")), ("packet_size", "invalid"), ("target_port", None), ("cancel_event", object())])
def test_invalid_parameters_never_open_socket(monkeypatch, field, value):
    monkeypatch.setattr(udp.socket, "socket", lambda *_args: pytest.fail("socket opened"))
    assert udp.run_udp_latency_test("192.168.1.2", **{field: value})["error"]["code"] == "invalid_params"


def test_minimum_payload_and_nonce_change_per_session(monkeypatch):
    sim = Simulation(monkeypatch, lambda sim, seq, data, peer: sim.enqueue(1, data, peer))
    first = udp.run_udp_latency_test("192.168.1.2", count=1, packet_size=16)
    second = udp.run_udp_latency_test("192.168.1.2", count=1, packet_size=16)
    assert first["packet_size"] == second["packet_size"] == 32
    assert first["received"] == second["received"] == 1
    assert sim.sent[0][1][16:32] != sim.sent[1][1][16:32]


def test_real_loopback_echo_endpoint():
    # No radios or external endpoints: this exercises the actual socket/event loop.
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(0.1)
    stop = threading.Event()

    def echo():
        while not stop.is_set():
            try:
                data, peer = server.recvfrom(1500)
                server.sendto(data, peer)
            except socket.timeout:
                continue

    worker = threading.Thread(target=echo, daemon=True)
    worker.start()
    try:
        started = time.monotonic()
        result = udp.run_udp_latency_test("127.0.0.1", target_port=server.getsockname()[1], count=6, interval_ms=20)
        assert result["sent"] == result["received"] == 6
        assert result["packet_loss_pct"] == 0
        assert result["rtt_ms"]["min"] >= 0
        assert result["elapsed_s"] <= 2
        assert time.monotonic() - started <= 2
        assert result["measurement"] == "synthetic_udp_echo_rtt"
    finally:
        stop.set()
        worker.join(timeout=1)
        server.close()
    assert not worker.is_alive()
