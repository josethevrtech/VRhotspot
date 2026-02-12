from unittest.mock import patch

import vr_hotspotd.lifecycle as lifecycle


def test_normalize_ap_adapter_maps_virtual_to_physical():
    inv = {"adapters": [{"ifname": "wlan1", "supports_ap": True}]}
    with patch("vr_hotspotd.lifecycle.os.path.exists", return_value=True):
        assert lifecycle._normalize_ap_adapter("x0wlan1", inv) == "wlan1"
        assert lifecycle._normalize_ap_adapter("x1wlan1", inv) == "wlan1"


def test_normalize_ap_adapter_keeps_unknown_virtual():
    inv = {"adapters": [{"ifname": "wlan1", "supports_ap": True}]}
    assert lifecycle._normalize_ap_adapter("x0missing", inv) == "x0missing"


def test_normalize_ap_adapter_falls_back_to_recommended_when_stale_ifname():
    inv = {
        "adapters": [
            {"ifname": "wlxNEW123456789", "supports_ap": True, "bus": "usb"},
        ],
        "recommended": "wlxNEW123456789",
    }
    with patch("vr_hotspotd.lifecycle.os.path.exists", return_value=False):
        assert lifecycle._normalize_ap_adapter("wlxOLD123456789", inv) == "wlxNEW123456789"
