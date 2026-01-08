"""
Transmit power control with auto-adjustment based on RSSI telemetry.
"""
import shutil
import subprocess
from typing import Optional, Tuple


def _iw_bin() -> Optional[str]:
    return shutil.which("iw") or ("/usr/sbin/iw" if __import__("os").path.exists("/usr/sbin/iw") else None)


def get_tx_power(ifname: str) -> Optional[int]:
    """Get current transmit power in dBm."""
    iw = _iw_bin()
    if not iw:
        return None
    
    try:
        p = subprocess.run(
            [iw, "dev", ifname, "info"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        
        for line in p.stdout.splitlines():
            if "txpower" in line.lower():
                # Parse: "txpower 20.00 dBm" or "txpower 20 dBm"
                parts = line.split()
                for i, part in enumerate(parts):
                    if "txpower" in part.lower() and i + 1 < len(parts):
                        try:
                            power_str = parts[i + 1]
                            return int(float(power_str))
                        except (ValueError, IndexError):
                            pass
    except Exception:
        pass
    
    return None


def set_tx_power(ifname: str, power_dbm: int) -> Tuple[bool, str]:
    """Set transmit power in dBm."""
    iw = _iw_bin()
    if not iw:
        return False, "iw_not_found"
    
    try:
        p = subprocess.run(
            [iw, "dev", ifname, "set", "txpower", "fixed", str(power_dbm)],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        
        if p.returncode == 0:
            return True, "ok"
        return False, p.stderr.strip() or "unknown_error"
    except Exception as e:
        return False, str(e)


def auto_adjust_tx_power(ifname: str, rssi_dbm: Optional[int], current_power: Optional[int] = None) -> Optional[int]:
    """
    Auto-adjust TX power based on RSSI.
    
    Logic:
    - If RSSI > -50 dBm: reduce power (too strong, may cause interference)
    - If RSSI < -80 dBm: increase power (too weak)
    - Otherwise: keep current or use optimal
    
    Returns:
        Recommended TX power in dBm, or None if no adjustment needed
    """
    if rssi_dbm is None:
        return None
    
    if current_power is None:
        current_power = get_tx_power(ifname) or 20  # Default to 20 dBm
    
    # Adjust based on RSSI
    if rssi_dbm > -50:
        # Signal too strong, reduce power
        new_power = max(1, current_power - 3)
    elif rssi_dbm < -80:
        # Signal too weak, increase power
        new_power = min(30, current_power + 3)  # Max typically 30 dBm
    else:
        # Signal is good, no change needed
        return None
    
    if new_power != current_power:
        return new_power
    
    return None
