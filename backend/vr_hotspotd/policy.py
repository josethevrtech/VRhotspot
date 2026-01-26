"""
VR Hotspot Policy Constants

Single source of truth for Basic Mode VR requirements and system defaults.
These reflect the minimum hardware/configuration requirements for acceptable
VR streaming performance.
"""

from typing import Literal

# =============================================================================
# Basic Mode VR Requirements
# =============================================================================
# These are the absolute minimums for VR streaming in Basic Mode.
# Users wanting to bypass these can switch to Advanced/Pro Mode.

# Required WiFi band for VR streaming
BASIC_MODE_REQUIRED_BAND: Literal["5ghz"] = "5ghz"

# Required minimum channel width in MHz (VHT80/HE80)
BASIC_MODE_REQUIRED_WIDTH_MHZ: int = 80

# Required PHY capability tier for VR streaming
# "wifi5+" means WiFi 5 (802.11ac) or better with VHT80/HE80 support
# "wifi6+" would mean WiFi 6 (802.11ax) or better
BASIC_MODE_REQUIRED_PHY: Literal["wifi5+", "wifi6+"] = "wifi5+"


# =============================================================================
# Error Codes
# =============================================================================
# Basic Mode enforcement error codes (must match wifi_probe.ERROR_REMEDIATIONS)

ERROR_BASIC_MODE_REQUIRES_5GHZ = "basic_mode_requires_5ghz"
ERROR_BASIC_MODE_REQUIRES_80MHZ_ADAPTER = "basic_mode_requires_80mhz_adapter"
ERROR_NM_INTERFACE_MANAGED = "nm_interface_managed"


# =============================================================================
# Helper Functions
# =============================================================================

def adapter_meets_basic_mode_requirements(adapter: dict) -> bool:
    """
    Check if an adapter meets Basic Mode VR requirements.
    
    Args:
        adapter: Adapter dict from inventory with capabilities
        
    Returns:
        True if adapter meets all Basic Mode requirements
    """
    if not adapter:
        return False
    
    # Must support AP mode
    if not adapter.get("supports_ap"):
        return False
    
    # Must support the required band
    if BASIC_MODE_REQUIRED_BAND == "5ghz" and not adapter.get("supports_5ghz"):
        return False
    
    # Must support the required channel width
    if BASIC_MODE_REQUIRED_WIDTH_MHZ >= 80 and not adapter.get("supports_80mhz"):
        return False
    
    # PHY capability check
    if BASIC_MODE_REQUIRED_PHY == "wifi6+":
        # Require HE (802.11ax) capability
        if not adapter.get("supports_he") and not adapter.get("supports_wifi6"):
            return False
    # "wifi5+" is satisfied by VHT80/HE80 which we already checked above
    
    return True


def get_basic_mode_requirements_summary() -> dict:
    """
    Get a summary of Basic Mode requirements for UI/logging.
    
    Returns:
        Dict with human-readable requirement descriptions
    """
    return {
        "band": BASIC_MODE_REQUIRED_BAND,
        "width_mhz": BASIC_MODE_REQUIRED_WIDTH_MHZ,
        "phy": BASIC_MODE_REQUIRED_PHY,
        "description": (
            f"Basic VR Mode requires {BASIC_MODE_REQUIRED_BAND.upper()} band, "
            f"{BASIC_MODE_REQUIRED_WIDTH_MHZ}MHz channel width, and a "
            f"{BASIC_MODE_REQUIRED_PHY} capable adapter."
        ),
    }
