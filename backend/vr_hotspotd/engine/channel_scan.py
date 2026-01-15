"""
Channel scanning and interference detection for automatic channel selection.
"""
import subprocess
import shutil
from typing import Dict, List, Optional


def _iw_bin() -> Optional[str]:
    return shutil.which("iw") or ("/usr/sbin/iw" if __import__("os").path.exists("/usr/sbin/iw") else None)


def scan_channels(ifname: str, band: str = "5ghz") -> List[Dict[str, any]]:
    """
    Scan for interference on available channels.
    
    Returns:
        List of channel info dicts with interference metrics
    """
    iw = _iw_bin()
    if not iw:
        return []
    
    channels: List[Dict[str, any]] = []
    
    band_norm = str(band or "").lower().strip()
    if band_norm in ("2", "2g", "2ghz", "2.4", "2.4ghz"):
        band_norm = "2.4ghz"
    elif band_norm in ("5", "5g", "5ghz"):
        band_norm = "5ghz"
    elif band_norm in ("6", "6g", "6ghz", "6e"):
        band_norm = "6ghz"
    else:
        band_norm = ""

    try:
        # Use iw to scan for networks (shows interference)
        p = subprocess.run(
            [iw, "dev", ifname, "scan"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        
        if p.returncode != 0:
            return []
        
        # Parse scan results to count networks per channel
        channel_counts: Dict[int, int] = {}
        current_channel = None
        
        for line in p.stdout.splitlines():
            line = line.strip()
            if "freq:" in line.lower():
                try:
                    freq_str = line.split(":")[1].strip().split()[0]
                    freq_mhz = int(float(freq_str))
                    if band_norm == "2.4ghz" and not (2400 <= freq_mhz <= 2500):
                        current_channel = None
                        continue
                    if band_norm == "5ghz" and not (4900 <= freq_mhz <= 5900):
                        current_channel = None
                        continue
                    if band_norm == "6ghz" and not (5925 <= freq_mhz <= 7125):
                        current_channel = None
                        continue
                    # Convert frequency to channel
                    if 2400 <= freq_mhz <= 2500:
                        current_channel = int((freq_mhz - 2407) / 5)
                    elif 4900 <= freq_mhz <= 5900:
                        current_channel = int((freq_mhz - 5000) / 5)
                    elif 5925 <= freq_mhz <= 7125:
                        current_channel = int((freq_mhz - 5950) / 5)
                except Exception:
                    pass
            elif current_channel is not None:
                channel_counts[current_channel] = channel_counts.get(current_channel, 0) + 1
                current_channel = None
        
        # Build channel info
        for channel, count in channel_counts.items():
            channels.append({
                "channel": channel,
                "interference_count": count,
                "score": 100 - min(100, count * 10),  # Lower score = more interference
            })
    except Exception:
        pass
    
    return channels


def select_best_channel(ifname: str, band: str = "5ghz", current_channel: Optional[int] = None) -> Optional[int]:
    """
    Select the best channel with least interference.
    
    Returns:
        Best channel number, or None if scan failed
    """
    channels = scan_channels(ifname, band)
    if not channels:
        return current_channel  # Keep current if scan fails
    
    # Sort by score (highest = best)
    channels.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    # Prefer current channel if it's in top 3
    if current_channel:
        for i, ch_info in enumerate(channels[:3]):
            if ch_info.get("channel") == current_channel:
                return current_channel
    
    # Return best channel
    return channels[0].get("channel") if channels else current_channel
