from unittest.mock import patch

import vr_hotspotd.adapters.inventory as inventory


def test_get_adapters_skips_virtual_ifname():
    with (
        patch.object(inventory, "_parse_iw_dev", return_value=[{"ifname": "x0wlan1", "phy": "phy0"}]),
        patch.object(inventory, "_parse_iw_reg_get", return_value={"global": {"country": "US", "raw_header": None}, "phys": {}}),
        patch.object(inventory, "_phy_supports_ap", return_value=True),
        patch.object(inventory, "_phy_supports_wifi6", return_value=False),
        patch.object(inventory, "_phy_supports_80mhz", return_value=True),
        patch.object(inventory, "_phy_band_support", return_value={"supports_2ghz": True, "supports_5ghz": True, "supports_6ghz": False}),
        patch.object(inventory, "_detect_bus_type", return_value="virtual"),
    ):
        result = inventory.get_adapters()
        assert result["adapters"] == []
        assert result["recommended"] is None
