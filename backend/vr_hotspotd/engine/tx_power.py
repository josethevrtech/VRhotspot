"""Explicit startup transmit-power control; the driver owns automatic power."""
import math
import shutil
import subprocess
from typing import Optional, Tuple


def _iw_bin() -> Optional[str]:
    return shutil.which("iw") or ("/usr/sbin/iw" if __import__("os").path.exists("/usr/sbin/iw") else None)


def get_tx_power(ifname: str) -> Optional[float]:
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
        
        if p.returncode != 0:
            return None
        for line in p.stdout.splitlines():
            if "txpower" in line.lower():
                # Parse: "txpower 20.00 dBm" or "txpower 20 dBm"
                parts = line.split()
                for i, part in enumerate(parts):
                    if "txpower" in part.lower() and i + 1 < len(parts):
                        try:
                            power_str = parts[i + 1]
                            power_dbm = float(power_str)
                            if math.isfinite(power_dbm):
                                return power_dbm
                        except (ValueError, IndexError, OverflowError):
                            pass
    except Exception:
        pass
    
    return None


def tx_power_mbm(power_dbm: Optional[float]) -> Optional[int]:
    """Validate whole dBm in the 0–30 range and convert to iw's mBm units.

    None means driver-managed automatic power, not an RSSI-driven control loop.
    The kernel and driver remain responsible for enforcing regulatory limits.
    """
    if power_dbm is None:
        return None
    if (
        isinstance(power_dbm, bool)
        or not isinstance(power_dbm, (int, float))
        or not 0 <= power_dbm <= 30
        or not math.isfinite(power_dbm)
        or int(power_dbm) != power_dbm
    ):
        raise ValueError("invalid_tx_power: expected an integer from 0 to 30 dBm or None")
    # https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html
    return int(power_dbm) * 100


def set_tx_power(ifname: str, power_dbm: Optional[float]) -> Tuple[bool, str]:
    """Apply a startup setting in dBm, or restore driver auto with None.

    Call only while preparing a session. Do not periodically change radio power
    from RSSI samples while a headset is streaming. A successful command means
    the driver accepted the request; it does not promise a measured RF power.
    """
    try:
        power_mbm = tx_power_mbm(power_dbm)
    except ValueError as exc:
        return False, str(exc)
    iw = _iw_bin()
    if not iw:
        return False, "iw_not_found"
    
    try:
        command = [iw, "dev", ifname, "set", "txpower"]
        command += ["auto"] if power_mbm is None else ["fixed", str(power_mbm)]
        p = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        
        if p.returncode == 0:
            return True, "ok"
        return False, (p.stderr or "").strip() or (p.stdout or "").strip() or f"iw_failed_rc_{p.returncode}"
    except Exception as e:
        return False, str(e)
