import io
import json
from email.message import Message

from vr_hotspotd import api


_LOAD_INSTANCES = []


class _FakeLoad:
    def __init__(self, *args, **kwargs):
        self.started = False
        self.stopped = 0
        self.requested_mbps = kwargs.get("mbps", 0.0)
        self.effective_mbps = self.requested_mbps
        self.method = kwargs.get("method", "curl")
        self.notes = []
        _LOAD_INSTANCES.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped += 1

    def info(self):
        return {
            "method": self.method,
            "requested_mbps": float(self.requested_mbps),
            "effective_mbps": float(self.effective_mbps) if self.started else 0.0,
            "notes": list(self.notes),
            "started": bool(self.started),
        }


def _make_handler(path: str, body: dict, token: str):
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

    def send_header(_key, _value):
        return

    def end_headers():
        return

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    return handler


def test_ping_under_load_flow_aborts_on_loss(monkeypatch):
    fake_load = _FakeLoad
    loss_ping = {
        "target_ip": "192.168.1.1",
        "duration_s": 10,
        "interval_ms": 20,
        "sent": 100,
        "received": 90,
        "packet_loss_pct": 10.0,
        "rtt_ms": {"min": 5.0, "avg": 10.0, "p50": 10.0, "p95": 20.0, "p99": 30.0, "p99_9": 40.0},
        "samples_ms": [],
    }

    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "secret")
    monkeypatch.setattr(api, "LoadGenerator", fake_load)
    monkeypatch.setattr(api, "ping_available", lambda: True)
    monkeypatch.setattr(api, "run_ping", lambda **_kwargs: loss_ping)

    handler = _make_handler(
        "/v1/diagnostics/ping_under_load",
        {"target_ip": "192.168.1.1", "load": {"method": "curl", "mbps": 150}},
        "secret",
    )
    handler.do_POST()

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler._last_code == 200
    assert payload["result_code"] == "ok"
    assert "load_aborted_due_to_loss" in payload["warnings"]
    assert payload["data"]["classification"]["grade"] == "unusable"
    assert _LOAD_INSTANCES and _LOAD_INSTANCES[0].stopped >= 1
