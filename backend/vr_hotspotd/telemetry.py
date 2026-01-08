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

    snap = get_clients_snapshot(adapter_ifname)
    ts = now
    dt = (ts - _LAST_TS) if _LAST_TS else None

    clients_out = []
    rssis = []
    tx_rates = []
    loss_pcts = []

    for client in snap.get("clients", []):
        mac = (client.get("mac") or "").lower()
        prev = _LAST_SAMPLE.get(mac, {})

        cur_tx = client.get("tx_packets")
        cur_failed = client.get("tx_failed")
        cur_retries = client.get("tx_retries")
        cur_rx = client.get("rx_packets")

        d_tx = _delta(prev.get("tx_packets"), cur_tx)
        d_failed = _delta(prev.get("tx_failed"), cur_failed)
        d_retries = _delta(prev.get("tx_retries"), cur_retries)
        d_rx = _delta(prev.get("rx_packets"), cur_rx)

        loss_pct = _ratio(d_failed, (d_tx or 0) + (d_failed or 0))
        retry_pct = _ratio(d_retries, d_tx)

        tx_pps = (d_tx / dt) if (dt and d_tx is not None) else None
        rx_pps = (d_rx / dt) if (dt and d_rx is not None) else None

        rssi = client.get("signal_dbm")
        tx_rate = client.get("tx_bitrate_mbps")
        if isinstance(rssi, int):
            rssis.append(rssi)
        if isinstance(tx_rate, (int, float)):
            tx_rates.append(float(tx_rate))
        if loss_pct is not None:
            loss_pcts.append(loss_pct)

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
        }

    _LAST_TS = ts

    summary = {
        "client_count": len(clients_out),
        "rssi_avg_dbm": (sum(rssis) / len(rssis)) if rssis else None,
        "rssi_min_dbm": min(rssis) if rssis else None,
        "tx_bitrate_avg_mbps": (sum(tx_rates) / len(tx_rates)) if tx_rates else None,
        "tx_bitrate_max_mbps": max(tx_rates) if tx_rates else None,
        "loss_pct_avg": (sum(loss_pcts) / len(loss_pcts)) if loss_pcts else None,
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
