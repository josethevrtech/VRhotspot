from dataclasses import replace
from unittest.mock import patch

import pytest

import vr_hotspotd.adapters.inventory as inventory
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


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


@pytest.mark.parametrize(
    ("ifname", "bus"),
    (("x0wlan1", "usb"), ("wlan1", "virtual")),
)
def test_snapshot_inventory_keeps_virtual_interface_filtering(ifname, bus):
    snapshot = make_host_facts_snapshot()
    snapshot = replace(
        snapshot,
        adapters=(replace(snapshot.adapters[0], ifname=ifname, bus=bus),),
    )

    result = inventory.get_adapters(host_facts_snapshot=snapshot)

    assert result["adapters"] == []
    assert result["recommended"] is None
