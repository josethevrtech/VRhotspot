"""Bounded, opt-in synthetic UDP echo round-trip measurement (not VR latency)."""

import ipaddress
import secrets
import select
import socket
import struct
import time
from threading import Event
from typing import Dict, List, Optional

from vr_hotspotd.diagnostics import limits


_HEADER = struct.Struct("!QQ16s")  # Sequence, monotonic send timestamp, session nonce.
_REPLY_GRACE_NS = 1_000_000_000
_CANCEL_POLL_NS = 100_000_000
_MAX_RECEIVE_DATAGRAMS = limits.DIAGNOSTIC_MAX_PACKET_COUNT * 4
_PERCENTILE_MIN_SAMPLES = {"p50": 2, "p95": 20, "p99": 100, "p99_9": 1000}


def _percentile(data: List[float], percentile: float, minimum: int) -> Optional[float]:
    """Interpolate observations only when the requested tail has enough samples."""
    if len(data) < minimum:
        return None
    position = percentile / 100.0 * (len(data) - 1)
    lower = int(position)
    upper = min(lower + 1, len(data) - 1)
    return data[lower] + (data[upper] - data[lower]) * (position - lower)


def _strict_clamp(value, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid diagnostic parameters")
    parsed = int(value)
    if isinstance(value, float) and value != parsed:
        raise ValueError("diagnostic parameters must be whole numbers")
    return max(minimum, min(maximum, parsed))


def run_udp_latency_test(
    target_ip: str,
    target_port: int = limits.UDP_DEFAULT_PORT,
    duration_s: int = limits.UDP_DEFAULT_DURATION_S,
    interval_ms: int = limits.UDP_DEFAULT_INTERVAL_MS,
    packet_size: int = limits.UDP_DEFAULT_PACKET_SIZE,
    count: Optional[int] = None,
    *,
    cancel_event: Optional[Event] = None,
) -> Dict:
    """Measure echoed payloads from one explicit IPv4 peer, without generating load.

    The target must already run an authorized byte-for-byte UDP echo endpoint.
    This does not use a streaming service's port, fall back to ICMP, estimate
    one-way delay, or start an echo server. Sending and receiving share an event
    loop but are independently paced: a missing reply cannot delay the next
    probe. The send window is at most duration_s; outstanding replies receive
    at most one additional second. Cancellation is checked at least every 100ms.
    """
    try:
        if not isinstance(target_ip, str):
            raise ValueError("target_ip must be an IPv4 address string")
        address = ipaddress.IPv4Address(target_ip)
        if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
            raise ValueError("target must be a unicast IPv4 address")
        target_ip = str(address)
    except (ipaddress.AddressValueError, ValueError, TypeError):
        return {"error": {"code": "invalid_target", "message": "A unicast IPv4 target_ip is required"}}

    try:
        target_port = _strict_clamp(target_port, minimum=limits.UDP_MIN_PORT, maximum=limits.UDP_MAX_PORT)
        duration_s = _strict_clamp(
            duration_s, minimum=limits.DIAGNOSTIC_MIN_DURATION_S, maximum=limits.DIAGNOSTIC_MAX_DURATION_S
        )
        interval_ms = _strict_clamp(
            interval_ms, minimum=limits.DIAGNOSTIC_MIN_INTERVAL_MS, maximum=limits.DIAGNOSTIC_MAX_INTERVAL_MS
        )
        packet_size = _strict_clamp(
            packet_size, minimum=_HEADER.size, maximum=limits.DIAGNOSTIC_MAX_PACKET_SIZE
        )
        packet_count = (
            limits.packet_count_for_budget(duration_s, interval_ms)
            if count is None
            else _strict_clamp(
                count, minimum=limits.DIAGNOSTIC_MIN_PACKET_COUNT, maximum=limits.DIAGNOSTIC_MAX_PACKET_COUNT
            )
        )
        if cancel_event is not None and not callable(getattr(cancel_event, "is_set", None)):
            raise ValueError("cancel_event must provide is_set()")
    except (TypeError, ValueError, OverflowError) as exc:
        return {"error": {"code": "invalid_params", "message": str(exc)}}

    target = (target_ip, target_port)
    nonce = secrets.token_bytes(16)
    padding = bytes(packet_size - _HEADER.size)
    interval_ns = interval_ms * 1_000_000
    late_threshold_ms = interval_ms * 2
    sent_times: List[int] = []
    received_samples: Dict[int, float] = {}
    duplicates = reordered = late = foreign = invalid = datagrams = 0
    highest_received = 0
    scheduling_delays_ms: List[float] = []
    start_ns = time.monotonic_ns()
    send_deadline_ns = start_ns + duration_s * 1_000_000_000
    next_send_ns = start_ns
    cancelled = False
    failure = None
    stop_reason = "complete"
    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        # A fixed receive buffer also bounds queued untrusted data during a run.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        while True:
            now_ns = time.monotonic_ns()
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                stop_reason = "cancelled"
                break

            can_send = len(sent_times) < packet_count and now_ns < send_deadline_ns
            if can_send and now_ns >= next_send_ns:
                sequence = len(sent_times) + 1
                payload = _HEADER.pack(sequence, now_ns, nonce) + padding
                try:
                    written = sock.sendto(payload, target)
                    if written != len(payload):
                        raise OSError("incomplete UDP datagram send")
                except OSError as exc:
                    failure = {"code": "udp_send_failed", "message": str(exc)}
                    stop_reason = "send_failed"
                    break
                sent_times.append(now_ns)
                scheduling_delays_ms.append(max(0.0, (now_ns - next_send_ns) / 1_000_000))
                # Do not compensate for scheduler stalls by emitting a burst.
                next_send_ns = now_ns + interval_ns

            now_ns = time.monotonic_ns()
            can_send = len(sent_times) < packet_count and now_ns < send_deadline_ns
            receive_deadline_ns = min(
                send_deadline_ns + _REPLY_GRACE_NS,
                (sent_times[-1] if sent_times else start_ns) + _REPLY_GRACE_NS,
            )
            if not can_send and (len(received_samples) == len(sent_times) or now_ns >= receive_deadline_ns):
                stop_reason = "count_reached" if len(sent_times) >= packet_count else "duration_elapsed"
                break

            wake_ns = min(
                next_send_ns if can_send else receive_deadline_ns,
                send_deadline_ns if can_send else receive_deadline_ns,
                now_ns + _CANCEL_POLL_NS,
            )
            readable, _, _ = select.select([sock], [], [], max(0, wake_ns - now_ns) / 1_000_000_000)
            if not readable:
                continue
            try:
                data, peer = sock.recvfrom(limits.DIAGNOSTIC_MAX_PACKET_SIZE + 1)
            except (BlockingIOError, InterruptedError):
                continue
            received_ns = time.monotonic_ns()
            if not can_send and received_ns > receive_deadline_ns:
                stop_reason = "reply_deadline_elapsed"
                break
            datagrams += 1
            if datagrams > _MAX_RECEIVE_DATAGRAMS:
                failure = {"code": "receive_budget_exhausted", "message": "Too many incoming UDP datagrams; test stopped"}
                stop_reason = "receive_budget_exhausted"
                break
            if peer[:2] != target:
                foreign += 1
                continue
            if len(data) != packet_size:
                invalid += 1
                continue
            sequence, echoed_ns, echoed_nonce = _HEADER.unpack_from(data)
            if (
                not 1 <= sequence <= len(sent_times)
                or echoed_nonce != nonce
                or echoed_ns != sent_times[sequence - 1]
                or data != _HEADER.pack(sequence, echoed_ns, nonce) + padding
                or received_ns < echoed_ns
            ):
                invalid += 1
                continue
            if sequence in received_samples:
                duplicates += 1
                continue
            sample_ms = (received_ns - echoed_ns) / 1_000_000
            received_samples[sequence] = sample_ms
            late += int(sample_ms > late_threshold_ms)
            reordered += int(sequence < highest_received)
            highest_received = max(highest_received, sequence)
    except OSError as exc:
        failure = {"code": "udp_test_failed", "message": str(exc)}
        stop_reason = "socket_failed"
    finally:
        if sock is not None:
            sock.close()

    elapsed_s = max(0, time.monotonic_ns() - start_ns) / 1_000_000_000
    sent = len(sent_times)
    received = len(received_samples)
    samples = [sample for _, sample in sorted(received_samples.items())]
    samples_sorted = sorted(samples)
    consecutive_deltas = [
        abs(received_samples[sequence] - received_samples[sequence - 1])
        for sequence in sorted(received_samples)
        if sequence - 1 in received_samples
    ]
    longest_missing = missing_run = missing_bursts = 0
    for sequence in range(1, sent + 1):
        if sequence not in received_samples:
            missing_bursts += int(missing_run == 0)
            missing_run += 1
            longest_missing = max(longest_missing, missing_run)
        else:
            missing_run = 0
    result = {
        "target_ip": target_ip,
        "target_port": target_port,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
        "packet_size": packet_size,
        "sent": sent,
        "received": received,
        "packet_loss_pct": 100.0 * (sent - received) / sent if sent else 0.0,
        "rtt_ms": {
            "min": min(samples_sorted) if samples else None,
            "avg": sum(samples) / received if samples else None,
            "max": max(samples_sorted) if samples else None,
            **{
                name: _percentile(samples_sorted, percentile, _PERCENTILE_MIN_SAMPLES[name])
                for name, percentile in (("p50", 50), ("p95", 95), ("p99", 99), ("p99_9", 99.9))
            },
        },
        # Backward-compatible name; not synchronized one-way IP delay variation.
        "jitter_ms": {"avg": sum(consecutive_deltas) / len(consecutive_deltas) if consecutive_deltas else None},
        "samples_ms": samples,
        "measurement": "synthetic_udp_echo_rtt",
        "jitter_definition": "mean_absolute_rtt_difference_between_consecutive_received_probe_sequences",
        "percentile_min_samples": dict(_PERCENTILE_MIN_SAMPLES),
        "elapsed_s": elapsed_s,
        "reply_grace_s": _REPLY_GRACE_NS / 1_000_000_000,
        "late_reply_threshold_ms": late_threshold_ms,
        "late_replies": late,
        "out_of_order_replies": reordered,
        "duplicate_replies": duplicates,
        "foreign_peer_replies": foreign,
        "invalid_replies": invalid,
        "missing_packets": sent - received,
        "missing_bursts": missing_bursts,
        "max_missing_burst_packets": longest_missing,
        "max_missing_burst_nominal_ms": longest_missing * interval_ms,
        "max_scheduling_delay_ms": max(scheduling_delays_ms, default=0.0),
        "sent_payload_bytes": sent * packet_size,
        "requested_packets": packet_count,
        "cancelled": cancelled,
        "stop_reason": stop_reason,
        "warnings": ["echo_endpoint_required", "synthetic_rtt_not_vr_motion_to_photon_latency"],
    }
    if failure:
        result["error"] = failure
    elif not samples and not cancelled:
        result["error"] = {
            "code": "no_samples",
            "message": "No verified UDP echoes. Confirm an authorized byte-for-byte echo endpoint; silence alone does not establish network loss.",
        }
    return result
