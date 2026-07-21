import math
from typing import Any


DIAGNOSTIC_MIN_DURATION_S = 1
DIAGNOSTIC_MAX_DURATION_S = 20
DIAGNOSTIC_MIN_INTERVAL_MS = 10
DIAGNOSTIC_MAX_INTERVAL_MS = 1000
DIAGNOSTIC_MIN_PACKET_COUNT = 1
DIAGNOSTIC_MAX_PACKET_COUNT = 2000
DIAGNOSTIC_MIN_PACKET_SIZE = 16
DIAGNOSTIC_MAX_PACKET_SIZE = 1472

PING_DEFAULT_DURATION_S = 10
PING_DEFAULT_INTERVAL_MS = 20
PING_DEFAULT_PACKET_SIZE = 56
PING_DEFAULT_REPLY_TIMEOUT_S = 2
PING_MIN_REPLY_TIMEOUT_S = 1
PING_MAX_REPLY_TIMEOUT_S = 5
PING_SUBPROCESS_GRACE_S = 2.0

UDP_DEFAULT_DURATION_S = 10
UDP_DEFAULT_INTERVAL_MS = 20
UDP_DEFAULT_PACKET_SIZE = 64
UDP_DEFAULT_PORT = 12345
UDP_MIN_PORT = 1
UDP_MAX_PORT = 65535

LOAD_MIN_DURATION_S = 3
LOAD_MAX_DURATION_S = DIAGNOSTIC_MAX_DURATION_S
LOAD_MIN_INTERVAL_MS = 10
LOAD_MAX_INTERVAL_MS = 200
LOAD_DEFAULT_MBPS = 150.0
LOAD_MIN_MBPS = 10.0
LOAD_MAX_MBPS = 400.0
LOAD_DEFAULT_IPERF3_PORT = 5201
LOAD_MIN_PORT = 1
LOAD_MAX_PORT = 65535
LOAD_MAX_URL_LENGTH = 2048
LOAD_MAX_HOST_LENGTH = 253
LOAD_PROCESS_GRACE_S = 2


def clamp_int(value: Any, *, min_value: int, max_value: int) -> int:
    parsed = int(value)
    return max(min_value, min(max_value, parsed))


def clamp_float(value: Any, *, min_value: float, max_value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("value must be finite")
    return max(min_value, min(max_value, parsed))


def packet_count_for_budget(duration_s: int, interval_ms: int) -> int:
    count = math.ceil(int(duration_s) * 1000 / int(interval_ms))
    return clamp_int(
        count,
        min_value=DIAGNOSTIC_MIN_PACKET_COUNT,
        max_value=DIAGNOSTIC_MAX_PACKET_COUNT,
    )
