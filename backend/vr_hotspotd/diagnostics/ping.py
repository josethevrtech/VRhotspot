import math
import re
import shutil
import subprocess
from typing import Dict, List, Optional

from vr_hotspotd.diagnostics import limits

_MAX_SAMPLES = limits.DIAGNOSTIC_MAX_PACKET_COUNT


def ping_available() -> bool:
    return shutil.which("ping") is not None


def _percentile(sorted_samples: List[float], percent: float) -> Optional[float]:
    if not sorted_samples:
        return None
    if percent <= 0:
        return float(sorted_samples[0])
    if percent >= 100:
        return float(sorted_samples[-1])

    k = (percent / 100.0) * (len(sorted_samples) - 1)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return float(sorted_samples[f])
    return float(sorted_samples[f] + (sorted_samples[c] - sorted_samples[f]) * (k - f))


def _parse_ping_output(text: str) -> Dict[str, Optional[float]]:
    time_re = re.compile(r"time[=<]([0-9.]+)\s*ms")
    summary_re = re.compile(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+received,\s+(\d+)%\s+packet loss"
    )

    samples: List[float] = []
    sent = None
    received = None
    loss = None

    for line in text.splitlines():
        for match in time_re.findall(line):
            try:
                samples.append(float(match))
            except Exception:
                continue

        m = summary_re.search(line)
        if m:
            try:
                sent = int(m.group(1))
                received = int(m.group(2))
                loss = float(m.group(3))
            except Exception:
                pass

    if len(samples) > _MAX_SAMPLES:
        samples = samples[-_MAX_SAMPLES:]

    return {
        "samples": samples,
        "sent": sent,
        "received": received,
        "loss": loss,
    }


def run_ping(
    target_ip: str,
    duration_s: int = limits.PING_DEFAULT_DURATION_S,
    interval_ms: int = limits.PING_DEFAULT_INTERVAL_MS,
    timeout_s: int = limits.PING_DEFAULT_REPLY_TIMEOUT_S,
    count: Optional[int] = None,
    packet_size: int = limits.PING_DEFAULT_PACKET_SIZE,
) -> dict:
    if not target_ip:
        return {"error": {"code": "invalid_target", "message": "target_ip is required"}}

    try:
        duration_s = limits.clamp_int(
            duration_s,
            min_value=limits.DIAGNOSTIC_MIN_DURATION_S,
            max_value=limits.DIAGNOSTIC_MAX_DURATION_S,
        )
        interval_ms = limits.clamp_int(
            interval_ms,
            min_value=limits.DIAGNOSTIC_MIN_INTERVAL_MS,
            max_value=limits.DIAGNOSTIC_MAX_INTERVAL_MS,
        )
        reply_timeout_s = limits.clamp_int(
            timeout_s,
            min_value=limits.PING_MIN_REPLY_TIMEOUT_S,
            max_value=limits.PING_MAX_REPLY_TIMEOUT_S,
        )
        packet_size = limits.clamp_int(
            packet_size,
            min_value=limits.DIAGNOSTIC_MIN_PACKET_SIZE,
            max_value=limits.DIAGNOSTIC_MAX_PACKET_SIZE,
        )
        packet_count = (
            limits.packet_count_for_budget(duration_s, interval_ms)
            if count is None
            else limits.clamp_int(
                count,
                min_value=limits.DIAGNOSTIC_MIN_PACKET_COUNT,
                max_value=limits.DIAGNOSTIC_MAX_PACKET_COUNT,
            )
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return {"error": {"code": "invalid_params", "message": str(exc)}}

    ping_bin = shutil.which("ping")
    if not ping_bin:
        return {"error": {"code": "ping_not_found", "message": "ping not found in PATH"}}

    interval_s = interval_ms / 1000.0
    cmd = [
        ping_bin,
        "-n",
        "-c",
        str(packet_count),
        "-i",
        f"{interval_s:.3f}",
        "-w",
        str(duration_s),
        "-W",
        str(reply_timeout_s),
        "-s",
        str(packet_size),
        target_ip,
    ]

    subprocess_timeout_s = duration_s + limits.PING_SUBPROCESS_GRACE_S
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": {
                "code": "ping_timeout",
                "message": "ping exceeded the diagnostic execution budget",
            }
        }
    except OSError as exc:
        return {"error": {"code": "ping_failed", "message": str(exc)}}

    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    if proc.returncode != 0:
        lower = output.lower()
        if "permission" in lower or "operation not permitted" in lower:
            return {"error": {"code": "ping_not_permitted", "message": output.strip()}}

    parsed = _parse_ping_output(output)
    samples = parsed["samples"]

    sent = parsed["sent"]
    received = parsed["received"]
    loss = parsed["loss"]

    if sent is None or received is None or loss is None:
        sent = len(samples)
        received = len(samples)
        loss = 0.0

    if proc.returncode != 0 and sent == 0 and received == 0:
        return {"error": {"code": "ping_failed", "message": output.strip() or "ping failed"}}

    rtt_sorted = sorted(samples)
    rtt_min = min(rtt_sorted) if rtt_sorted else None
    rtt_avg = (sum(rtt_sorted) / len(rtt_sorted)) if rtt_sorted else None

    # Calculate jitter (inter-packet delay variation)
    jitter = None
    jitter_avg = None
    jitter_max = None
    if len(samples) >= 2:
        # Jitter is the mean deviation of consecutive packet delay differences
        delays = []
        for i in range(1, len(samples)):
            delay_diff = abs(samples[i] - samples[i-1])
            delays.append(delay_diff)
        if delays:
            jitter = sum(delays) / len(delays)  # Average jitter
            jitter_avg = jitter
            jitter_max = max(delays) if delays else None

    rtt = {
        "min": rtt_min,
        "avg": rtt_avg,
        "p50": _percentile(rtt_sorted, 50.0),
        "p95": _percentile(rtt_sorted, 95.0),
        "p99": _percentile(rtt_sorted, 99.0),
        "p99_9": _percentile(rtt_sorted, 99.9),
    }

    packet_loss_pct = 0.0
    if sent and sent > 0:
        packet_loss_pct = max(0.0, min(100.0, 100.0 * (sent - received) / sent))

    result = {
        "target_ip": target_ip,
        "duration_s": int(duration_s),
        "interval_ms": int(interval_ms),
        "sent": int(sent),
        "received": int(received),
        "packet_loss_pct": float(packet_loss_pct),
        "rtt_ms": rtt,
        "samples_ms": samples,
    }
    
    # Add jitter metrics
    if jitter is not None:
        result["jitter_ms"] = {
            "avg": float(jitter_avg) if jitter_avg is not None else None,
            "max": float(jitter_max) if jitter_max is not None else None,
        }

    return result
