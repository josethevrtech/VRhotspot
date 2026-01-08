"""
Hardware-specific optimization profiles for known WiFi adapters.
"""
from typing import Dict, Optional


# Known adapter profiles with chipset-specific optimizations
ADAPTER_PROFILES: Dict[str, Dict[str, any]] = {
    # BrosTrend AXE3000 Tri-Band (recommended adapter)
    "brostrend_axe3000": {
        "chipset": "unknown",
        "vendor": "BrosTrend",
        "model": "AXE3000",
        "optimizations": {
            "optimized_no_virt": False,  # Works best with virtual interface
            "channel_width": "80",  # Supports 80MHz well
            "beacon_interval": 50,
            "short_guard_interval": True,
        },
    },
    # EDUP EP-AX1672
    "edup_ep_ax1672": {
        "chipset": "unknown",
        "vendor": "EDUP",
        "model": "EP-AX1672",
        "optimizations": {
            "optimized_no_virt": False,
            "channel_width": "80",
            "beacon_interval": 50,
            "short_guard_interval": True,
        },
    },
    # Panda Wireless PAU0F AXE3000
    "panda_pau0f_axe3000": {
        "chipset": "unknown",
        "vendor": "Panda Wireless",
        "model": "PAU0F AXE3000",
        "optimizations": {
            "optimized_no_virt": False,
            "channel_width": "80",
            "beacon_interval": 50,
            "short_guard_interval": True,
        },
    },
}


def detect_adapter_profile(adapter_info: Dict) -> Optional[Dict[str, any]]:
    """
    Detect and return adapter-specific profile based on adapter information.
    
    Args:
        adapter_info: Dictionary with adapter info (ifname, vendor, model, etc.)
    
    Returns:
        Profile dict with optimizations, or None if no match
    """
    ifname = (adapter_info.get("ifname") or "").lower()
    vendor = (adapter_info.get("vendor") or "").lower()
    model = (adapter_info.get("model") or "").lower()
    
    # Try to match by known patterns
    for profile_key, profile in ADAPTER_PROFILES.items():
        profile_vendor = (profile.get("vendor") or "").lower()
        profile_model = (profile.get("model") or "").lower()
        
        if profile_vendor in vendor or profile_model in model:
            return profile.get("optimizations", {})
    
    # Try USB ID matching if available
    usb_id = adapter_info.get("usb_id")
    if usb_id:
        # Could add USB ID matching here if we have that info
    
    return None


def apply_adapter_profile(cfg: Dict[str, any], adapter_info: Dict) -> Dict[str, any]:
    """
    Apply adapter-specific optimizations to config.
    
    Returns:
        Updated config dict
    """
    profile = detect_adapter_profile(adapter_info)
    if not profile:
        return cfg
    
    # Merge profile optimizations into config (don't override user settings if already set)
    updated = dict(cfg)
    for key, value in profile.items():
        if key not in updated or updated[key] is None or updated[key] == "":
            updated[key] = value
    
    return updated
