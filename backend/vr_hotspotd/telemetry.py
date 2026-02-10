from __future__ import annotations

import time
from typing import Any, Dict, Optional

from vr_hotspotd.diagnostics.clients import get_clients_snapshot


_LAST_SAMPLE: Dict[str, Dict[str, Any]] = {}
_LAST_TS: Optional[float] = None
_LAST_RESULT: Optional[Dict[str, Any]] = None


def _delta(prev: Optional[int], cur: Optional[int]) -> Optional[int]:
    if prev is None or cur is None:
        return None
    d = cur - prev
    return d if d >= 0 else None


def _ratio(num: Optional[int], denom: Optional[int]) -> Optional[float]:
    if num is None or denom is None or denom <= 0:
        return None
    return (float(num) / float(denom)) * 100.0


def get_snapshot(
    *,
    adapter_ifname: Optional[str],
    ap_interface_hint: Optional[str] = None,
    enabled: bool = True,
    interval_s: float = 2.0,
) -> Dict[str, Any]:
    global _LAST_TS, _LAST_RESULT
    if not enabled:
        return {"enabled": False}

    now = time.time()
    if _LAST_RESULT is not None and _LAST_TS is not None:
        if interval_s > 0 and (now - _LAST_TS) < interval_s:
            return _LAST_RESULT

    snap = get_clients_snapshot(adapter_ifname, ap_interface_hint=ap_interface_hint)
    ts = now
    dt = (ts - _LAST_TS) if _LAST_TS else None

    clients_out = []
    rssis = []
    tx_rates = []
    tx_mbps_values = []
    rx_mbps_values = []
    loss_pcts = []
    quality_scores = []

    for client in snap.get("clients", []):
        mac = (client.get("mac") or "").lower()
        prev = _LAST_SAMPLE.get(mac, {})

        cur_tx = client.get("tx_packets")
        cur_failed = client.get("tx_failed")
        cur_retries = client.get("tx_retries")
        cur_rx = client.get("rx_packets")
        cur_tx_bytes = client.get("tx_bytes")
        cur_rx_bytes = client.get("rx_bytes")

        d_tx = _delta(prev.get("tx_packets"), cur_tx)
        d_failed = _delta(prev.get("tx_failed"), cur_failed)
        d_retries = _delta(prev.get("tx_retries"), cur_retries)
        d_rx = _delta(prev.get("rx_packets"), cur_rx)
        d_tx_bytes = _delta(prev.get("tx_bytes"), cur_tx_bytes)
        d_rx_bytes = _delta(prev.get("rx_bytes"), cur_rx_bytes)

        loss_pct = _ratio(d_failed, (d_tx or 0) + (d_failed or 0))
        retry_pct = _ratio(d_retries, d_tx)

        tx_pps = (d_tx / dt) if (dt and d_tx is not None) else None
        rx_pps = (d_rx / dt) if (dt and d_rx is not None) else None
        
        # Bandwidth tracking (bytes per second)
        tx_bps = (d_tx_bytes * 8 / dt) if (dt and d_tx_bytes is not None) else None  # bits per second
        rx_bps = (d_rx_bytes * 8 / dt) if (dt and d_rx_bytes is not None) else None  # bits per second
        tx_mbps = (tx_bps / 1_000_000) if tx_bps is not None else None
        rx_mbps = (rx_bps / 1_000_000) if rx_bps is not None else None
        if isinstance(tx_mbps, (int, float)):
            tx_mbps_values.append(float(tx_mbps))
        if isinstance(rx_mbps, (int, float)):
            rx_mbps_values.append(float(rx_mbps))

        rssi = client.get("signal_dbm")
        tx_rate = client.get("tx_bitrate_mbps")
        if isinstance(rssi, int):
            rssis.append(rssi)
        if isinstance(tx_rate, (int, float)):
            tx_rates.append(float(tx_rate))
        if loss_pct is not None:
            loss_pcts.append(loss_pct)
        
        # Connection quality score (0-100, higher is better)
        # Based on RSSI, loss, retry rate, and bitrate
        quality_score = None
        if rssi is not None:
            # RSSI component (0-40 points): -30dBm = 40, -90dBm = 0
            rssi_score = max(0, min(40, 40 + (rssi + 30) * 0.67))
            
            # Loss component (0-30 points): 0% = 30, 5%+ = 0
            loss_score = max(0, min(30, 30 - (loss_pct or 0) * 6))
            
            # Retry component (0-20 points): 0% = 20, 20%+ = 0
            retry_score = max(0, min(20, 20 - (retry_pct or 0)))
            
            # Bitrate component (0-10 points): 100+ Mbps = 10, <10 Mbps = 0
            bitrate_score = 0
            if tx_rate is not None:
                bitrate_score = max(0, min(10, (tx_rate / 10)))
            
            quality_score = rssi_score + loss_score + retry_score + bitrate_score
            quality_scores.append(quality_score)

        clients_out.append(
            {
                "mac": mac or None,
                "ip": client.get("ip"),
                "signal_dbm": rssi,
                "signal_avg_dbm": client.get("signal_avg_dbm"),
                "tx_bitrate_mbps": tx_rate,
                "rx_bitrate_mbps": client.get("rx_bitrate_mbps"),
                "tx_retries": client.get("tx_retries"),
                "tx_failed": client.get("tx_failed"),
                "tx_packets": client.get("tx_packets"),
                "rx_packets": client.get("rx_packets"),
                "retry_pct": retry_pct,
                "loss_pct": loss_pct,
                "tx_pps": tx_pps,
                "rx_pps": rx_pps,
                "tx_mbps": tx_mbps,
                "rx_mbps": rx_mbps,
                "quality_score": quality_score,
                "inactive_ms": client.get("inactive_ms"),
                "connected_time_s": client.get("connected_time_s"),
                "source": client.get("source"),
            }
        )

        _LAST_SAMPLE[mac] = {
            "tx_packets": cur_tx,
            "tx_failed": cur_failed,
            "tx_retries": cur_retries,
            "rx_packets": cur_rx,
            "tx_bytes": cur_tx_bytes,
            "rx_bytes": cur_rx_bytes,
        }

    _LAST_TS = ts

    summary = {
        "client_count": len(clients_out),
        "rssi_avg_dbm": (sum(rssis) / len(rssis)) if rssis else None,
        "rssi_min_dbm": min(rssis) if rssis else None,
        "tx_bitrate_avg_mbps": (sum(tx_rates) / len(tx_rates)) if tx_rates else None,
        "tx_bitrate_max_mbps": max(tx_rates) if tx_rates else None,
        "tx_mbps_total": (sum(tx_mbps_values) if tx_mbps_values else None),
        "rx_mbps_total": (sum(rx_mbps_values) if rx_mbps_values else None),
        "loss_pct_avg": (sum(loss_pcts) / len(loss_pcts)) if loss_pcts else None,
        "quality_score_avg": (sum(quality_scores) / len(quality_scores)) if quality_scores else None,
        "quality_score_min": min(quality_scores) if quality_scores else None,
    }

    result = {
        "enabled": True,
        "ts": int(ts),
        "ap_interface": snap.get("ap_interface"),
        "clients": clients_out,
        "summary": summary,
        "warnings": snap.get("warnings", []),
        "sources": snap.get("sources", {}),
    }
    _LAST_RESULT = result
    return result
