"""
UDP latency diagnostics for VR streaming testing.
VR streaming typically uses UDP, so this provides more relevant metrics than TCP ping.
"""
import socket
import time
import struct
from typing import Dict, List, Optional, Tuple


def run_udp_latency_test(
    target_ip: str,
    target_port: int = 12345,
    duration_s: int = 10,
    interval_ms: int = 20,
    packet_size: int = 64,
) -> Dict:
    """
    Run UDP latency test by sending UDP packets and measuring round-trip time.
    This requires a UDP echo server on the target (or we can use a simpler approach).
    
    For VR hotspot, we'll use a simpler approach: send UDP packets and measure
    one-way latency if possible, or use ICMP as fallback.
    """
    if not target_ip:
        return {"error": {"code": "invalid_target", "message": "target_ip is required"}}
    
    samples: List[float] = []
    sent = 0
    received = 0
    start_time = time.time()
    deadline = start_time + duration_s
    interval_s = max(0.001, interval_ms / 1000.0)
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(interval_s * 2)
        
        seq = 0
        while time.time() < deadline:
            send_time = time.time()
            seq += 1
            
            # Send UDP packet with sequence number and timestamp
            payload = struct.pack("!Qd", seq, send_time)  # 8 bytes seq + 8 bytes timestamp
            payload += b"\x00" * (packet_size - len(payload))  # Pad to packet_size
            
            try:
                sock.sendto(payload, (target_ip, target_port))
                sent += 1
                
                # Try to receive echo (if target supports it)
                try:
                    data, addr = sock.recvfrom(packet_size + 16)
                    recv_time = time.time()
                    if len(data) >= 16:
                        recv_seq, send_ts = struct.unpack("!Qd", data[:16])
                        if recv_seq == seq:
                            rtt = (recv_time - send_ts) * 1000  # Convert to ms
                            samples.append(rtt)
                            received += 1
                except socket.timeout:
                    # No echo received, that's okay for one-way test
                    pass
            except Exception as e:
                return {"error": {"code": "udp_send_failed", "message": str(e)}}
            
            # Sleep until next interval
            next_send = send_time + interval_s
            sleep_time = next_send - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        sock.close()
    except Exception as e:
        return {"error": {"code": "udp_test_failed", "message": str(e)}}
    
    # Calculate statistics
    if not samples:
        return {
            "target_ip": target_ip,
            "target_port": target_port,
            "duration_s": duration_s,
            "interval_ms": interval_ms,
            "sent": sent,
            "received": received,
            "packet_loss_pct": 100.0 if sent > 0 else 0.0,
            "error": {"code": "no_samples", "message": "No valid latency samples collected"},
        }
    
    samples_sorted = sorted(samples)
    rtt_min = min(samples_sorted)
    rtt_avg = sum(samples_sorted) / len(samples_sorted)
    
    # Calculate percentiles
    def _percentile(data: List[float], p: float) -> float:
        if not data:
            return 0.0
        k = (p / 100.0) * (len(data) - 1)
        f = int(k)
        c = f + 1 if f < len(data) - 1 else f
        if f == c:
            return data[f]
        return data[f] + (data[c] - data[f]) * (k - f)
    
    # Calculate jitter (inter-packet delay variation)
    jitter = None
    if len(samples) >= 2:
        delays = [abs(samples[i] - samples[i-1]) for i in range(1, len(samples))]
        jitter = sum(delays) / len(delays) if delays else None
    
    packet_loss_pct = 100.0 * (sent - received) / sent if sent > 0 else 0.0
    
    return {
        "target_ip": target_ip,
        "target_port": target_port,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
        "sent": sent,
        "received": received,
        "packet_loss_pct": float(packet_loss_pct),
        "rtt_ms": {
            "min": float(rtt_min),
            "avg": float(rtt_avg),
            "p50": float(_percentile(samples_sorted, 50.0)),
            "p95": float(_percentile(samples_sorted, 95.0)),
            "p99": float(_percentile(samples_sorted, 99.0)),
            "p99_9": float(_percentile(samples_sorted, 99.9)),
        },
        "jitter_ms": {
            "avg": float(jitter) if jitter is not None else None,
        },
        "samples_ms": samples,
    }
