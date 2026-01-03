import math
import re
import shutil
import subprocess
from typing import Dict, List, Optional

_MAX_SAMPLES = 2000


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
    duration_s: int = 10,
    interval_ms: int = 20,
    timeout_s: int = 2,
) -> dict:
    if not target_ip:
        return {"error": {"code": "invalid_target", "message": "target_ip is required"}}

    ping_bin = shutil.which("ping")
    if not ping_bin:
        return {"error": {"code": "ping_not_found", "message": "ping not found in PATH"}}

    try:
        interval_s = max(1, int(interval_ms)) / 1000.0
        cmd = [
            ping_bin,
            "-n",
            "-i",
            f"{interval_s:.3f}",
            "-w",
            str(int(duration_s)),
            "-W",
            str(int(timeout_s)),
            target_ip,
        ]
    except Exception as exc:
        return {"error": {"code": "ping_failed", "message": str(exc)}}

    proc = subprocess.run(cmd, capture_output=True, text=True)
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

    return {
        "target_ip": target_ip,
        "duration_s": int(duration_s),
        "interval_ms": int(interval_ms),
        "sent": int(sent),
        "received": int(received),
        "packet_loss_pct": float(packet_loss_pct),
        "rtt_ms": rtt,
        "samples_ms": samples,
    }
