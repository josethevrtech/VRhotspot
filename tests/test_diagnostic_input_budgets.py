import io
import json
import struct
from email.message import Message

import pytest

from vr_hotspotd import api
from vr_hotspotd.diagnostics import limits
from vr_hotspotd.diagnostics import udp_latency


def _make_handler(path: str, body: dict, token: str = "secret"):
    raw = json.dumps(body).encode("utf-8")
    handler = api.APIHandler.__new__(api.APIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(raw))
    handler.headers["X-Api-Token"] = token
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.path = path
    handler._last_code = None

    def send_response(code, _message=None):
        handler._last_code = code

    handler.send_response = send_response
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def _post(monkeypatch, path: str, body: dict):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    handler = _make_handler(path, body)
    handler.do_POST()
    return handler, json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_ping_route_preserves_valid_inputs(monkeypatch):
    captured = {}

    def fake_run_ping(**kwargs):
        captured.update(kwargs)
        return {"target_ip": kwargs["target_ip"], "sent": 1, "received": 1}

    monkeypatch.setattr(api, "run_ping", fake_run_ping)
    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/ping",
        {
            "target_ip": "192.168.1.1",
            "duration_s": 5,
            "interval_ms": 50,
            "timeout_s": 2,
            "count": 25,
            "packet_size": 128,
        },
    )

    assert handler._last_code == 200
    assert captured == {
        "target_ip": "192.168.1.1",
        "duration_s": 5,
        "interval_ms": 50,
        "timeout_s": 2,
        "count": 25,
        "packet_size": 128,
    }
    assert payload["data"]["sent"] == 1
    assert payload["warnings"] == []


def test_ping_route_clamps_extreme_inputs(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api,
        "run_ping",
        lambda **kwargs: captured.update(kwargs) or {"sent": 0, "received": 0},
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/ping",
        {
            "target_ip": "192.168.1.1",
            "duration_s": 999,
            "interval_ms": 0,
            "timeout_s": 999,
            "count": 999_999,
            "packet_size": 999_999,
        },
    )

    assert handler._last_code == 200
    assert captured["duration_s"] == limits.DIAGNOSTIC_MAX_DURATION_S
    assert captured["interval_ms"] == limits.DIAGNOSTIC_MIN_INTERVAL_MS
    assert captured["timeout_s"] == limits.PING_MAX_REPLY_TIMEOUT_S
    assert captured["count"] == limits.DIAGNOSTIC_MAX_PACKET_COUNT
    assert captured["packet_size"] == limits.DIAGNOSTIC_MAX_PACKET_SIZE
    assert {
        "duration_s_clamped",
        "interval_ms_clamped",
        "timeout_s_clamped",
        "count_clamped",
        "packet_size_clamped",
    }.issubset(payload["warnings"])


def test_udp_route_preserves_valid_inputs(monkeypatch):
    captured = {}

    def fake_udp(**kwargs):
        captured.update(kwargs)
        return {"target_ip": kwargs["target_ip"], "sent": 3, "received": 3}

    monkeypatch.setattr(api, "run_udp_latency_test", fake_udp)
    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/udp_latency",
        {
            "target_ip": "192.168.1.2",
            "duration_s": 4,
            "interval_ms": 40,
            "target_port": 23456,
            "packet_size": 256,
            "count": 30,
        },
    )

    assert handler._last_code == 200
    assert captured == {
        "target_ip": "192.168.1.2",
        "target_port": 23456,
        "duration_s": 4,
        "interval_ms": 40,
        "packet_size": 256,
        "count": 30,
    }
    assert payload["data"]["received"] == 3
    assert payload["warnings"] == []


def test_udp_route_clamps_extreme_inputs_and_packets_alias(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api,
        "run_udp_latency_test",
        lambda **kwargs: captured.update(kwargs) or {"sent": 0, "received": 0},
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/udp_latency",
        {
            "target_ip": "192.168.1.2",
            "duration_s": 999,
            "interval_ms": 0,
            "target_port": 0,
            "packet_size": 999_999,
            "packets": 999_999,
        },
    )

    assert handler._last_code == 200
    assert captured["duration_s"] == limits.DIAGNOSTIC_MAX_DURATION_S
    assert captured["interval_ms"] == limits.DIAGNOSTIC_MIN_INTERVAL_MS
    assert captured["target_port"] == limits.UDP_MIN_PORT
    assert captured["packet_size"] == limits.DIAGNOSTIC_MAX_PACKET_SIZE
    assert captured["count"] == limits.DIAGNOSTIC_MAX_PACKET_COUNT
    assert {
        "duration_s_clamped",
        "interval_ms_clamped",
        "target_port_clamped",
        "packet_size_clamped",
        "count_clamped",
    }.issubset(payload["warnings"])


def test_udp_route_rejects_non_numeric_port_without_running_test(monkeypatch):
    monkeypatch.setattr(
        api,
        "run_udp_latency_test",
        lambda **_kwargs: pytest.fail("invalid UDP port reached diagnostic helper"),
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/udp_latency",
        {"target_ip": "192.168.1.2", "target_port": "not-a-port"},
    )

    assert handler._last_code == 400
    assert payload["result_code"] == "invalid_request"
    assert "invalid_diagnostic_params" in payload["warnings"]


class _FakeUdpSocket:
    def __init__(self):
        self.sent = 0
        self.last_payload = b""
        self.last_target = None
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendto(self, payload, target):
        self.sent += 1
        self.last_payload = payload
        self.last_target = target

    def recvfrom(self, _size):
        return self.last_payload, self.last_target

    def close(self):
        self.closed = True


def test_udp_helper_bounds_count_duration_size_interval_and_port(monkeypatch):
    fake_socket = _FakeUdpSocket()
    monkeypatch.setattr(udp_latency.socket, "socket", lambda *_args, **_kwargs: fake_socket)
    monkeypatch.setattr(udp_latency.time, "time", lambda: 0.0)
    monkeypatch.setattr(udp_latency.time, "sleep", lambda _seconds: None)

    result = udp_latency.run_udp_latency_test(
        "192.168.1.2",
        target_port=999_999,
        duration_s=999,
        interval_ms=0,
        packet_size=999_999,
        count=999_999,
    )

    assert result["target_port"] == limits.UDP_MAX_PORT
    assert result["duration_s"] == limits.DIAGNOSTIC_MAX_DURATION_S
    assert result["interval_ms"] == limits.DIAGNOSTIC_MIN_INTERVAL_MS
    assert result["sent"] == limits.DIAGNOSTIC_MAX_PACKET_COUNT
    assert len(fake_socket.last_payload) == limits.DIAGNOSTIC_MAX_PACKET_SIZE
    assert struct.unpack("!Q", fake_socket.last_payload[:8])[0] == result["sent"]
    assert fake_socket.last_target == ("192.168.1.2", limits.UDP_MAX_PORT)
    assert fake_socket.closed is True


class _FakeLoad:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.method = kwargs["method"]
        self.requested_mbps = kwargs["mbps"]
        self.effective_mbps = kwargs["mbps"]
        self.started = False
        self.notes = []
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        return None

    def info(self):
        return {
            "method": self.method,
            "requested_mbps": self.requested_mbps,
            "effective_mbps": self.effective_mbps,
            "notes": self.notes,
            "started": self.started,
        }


def _successful_ping(**kwargs):
    return {
        "target_ip": kwargs["target_ip"],
        "duration_s": kwargs["duration_s"],
        "interval_ms": kwargs["interval_ms"],
        "sent": 1,
        "received": 1,
        "packet_loss_pct": 0.0,
        "rtt_ms": {"min": 1.0, "avg": 1.0, "p50": 1.0, "p95": 1.0, "p99": 1.0},
        "samples_ms": [1.0],
    }


def test_ping_under_load_clamps_duration_intensity_and_iperf3_port(monkeypatch):
    _FakeLoad.instances.clear()
    ping_calls = []
    monkeypatch.setattr(api, "LoadGenerator", _FakeLoad)
    monkeypatch.setattr(api, "ping_available", lambda: True)
    monkeypatch.setattr(
        api,
        "run_ping",
        lambda **kwargs: ping_calls.append(kwargs) or _successful_ping(**kwargs),
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/ping_under_load",
        {
            "target_ip": "192.168.1.1",
            "duration_s": 999,
            "interval_ms": 999,
            "load": {
                "method": "iperf3",
                "mbps": 999_999,
                "iperf3_host": "example.com",
                "iperf3_port": 999_999,
            },
        },
    )

    assert handler._last_code == 200
    load_kwargs = _FakeLoad.instances[-1].kwargs
    assert load_kwargs["duration_s"] == limits.LOAD_MAX_DURATION_S
    assert load_kwargs["mbps"] == limits.LOAD_MAX_MBPS
    assert load_kwargs["iperf3_port"] == limits.LOAD_MAX_PORT
    assert load_kwargs["iperf3_host"] == "example.com"
    assert ping_calls[-1]["duration_s"] == limits.LOAD_MAX_DURATION_S
    assert ping_calls[-1]["interval_ms"] == limits.LOAD_MAX_INTERVAL_MS
    assert {
        "duration_s_clamped",
        "interval_ms_clamped",
        "mbps_clamped",
        "iperf3_port_clamped",
    }.issubset(payload["warnings"])


@pytest.mark.parametrize(
    "url",
    (
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/1",
        "rm -rf /tmp/vrhotspot-test",
    ),
)
def test_ping_under_load_rejects_unsupported_curl_urls_before_execution(monkeypatch, url):
    monkeypatch.setattr(
        api,
        "ping_available",
        lambda: pytest.fail("invalid curl URL reached ping availability check"),
    )
    monkeypatch.setattr(
        api,
        "LoadGenerator",
        lambda **_kwargs: pytest.fail("invalid curl URL reached load generator"),
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/ping_under_load",
        {
            "target_ip": "192.168.1.1",
            "load": {"method": "curl", "url": url},
        },
    )

    assert handler._last_code == 400
    assert payload["data"]["error"]["code"] == "invalid_params"


@pytest.mark.parametrize("host", ("-R", "bad host", "example.com;id", "a" * 254))
def test_ping_under_load_rejects_invalid_iperf3_hosts_before_execution(monkeypatch, host):
    monkeypatch.setattr(
        api,
        "ping_available",
        lambda: pytest.fail("invalid iperf3 host reached ping availability check"),
    )

    handler, payload = _post(
        monkeypatch,
        "/v1/diagnostics/ping_under_load",
        {
            "target_ip": "192.168.1.1",
            "load": {"method": "iperf3", "iperf3_host": host},
        },
    )

    assert handler._last_code == 400
    assert payload["data"]["error"]["code"] == "invalid_params"
