from dataclasses import replace

import pytest

from vr_hotspotd import host_facts
import vr_hotspotd.adapters.inventory as inventory
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


def test_he_iftypes_ap_true():
    iw_out = """
Wiphy phy0
  Band 1:
    HE Iftypes: AP, STA, P2P-client
"""
    assert inventory._he_iftypes_has_ap(iw_out) is True
    assert inventory._supports_wifi6_from_iw(iw_out) is True


def test_he_iftypes_ap_false():
    iw_out = """
Wiphy phy0
  Band 1:
    HE Iftypes: STA, P2P-client
"""
    assert inventory._he_iftypes_has_ap(iw_out) is False
    assert inventory._supports_wifi6_from_iw(iw_out) is False


def test_wifi6_fallback_80211ax_marker():
    iw_out = """
Wiphy phy0
  Band 1:
    Some capabilities: IEEE 802.11ax supported
"""
    assert inventory._he_iftypes_has_ap(iw_out) is None
    assert inventory._supports_wifi6_from_iw(iw_out) is True


def test_wifi6_false_without_markers():
    iw_out = """
Wiphy phy0
  Band 1:
    Some capabilities: IEEE 802.11ac supported
"""
    assert inventory._he_iftypes_has_ap(iw_out) is None
    assert inventory._supports_wifi6_from_iw(iw_out) is False


def test_phy_supports_ap_detects_ap(monkeypatch):
    iw_out = """
Wiphy phy0
  Supported interface modes:
    * managed
    * AP
    * monitor
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    assert inventory._phy_supports_ap("phy0") is True


def test_phy_supports_ap_detects_ap_vlan(monkeypatch):
    iw_out = """
Wiphy phy0
  Supported interface modes:
    * AP/VLAN
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    assert inventory._phy_supports_ap("phy0") is True


def test_phy_supports_ap_false_when_missing(monkeypatch):
    iw_out = """
Wiphy phy0
  Supported interface modes:
    * managed
    * monitor
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    assert inventory._phy_supports_ap("phy0") is False


def test_band_support_parses_decimal_2ghz(monkeypatch):
    iw_out = """
Wiphy phy0
  Band 1:
    Frequencies:
      * 2412.0 MHz [1] (22.0 dBm)
      * 2437.0 MHz [6] (disabled)
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    caps = inventory._phy_band_support("phy0")
    assert caps["supports_2ghz"] is True
    assert caps["supports_5ghz"] is False
    assert caps["supports_6ghz"] is False


def test_band_support_parses_decimal_5ghz(monkeypatch):
    iw_out = """
Wiphy phy0
  Band 2:
    Frequencies:
      * 5180.0 MHz [36] (23.0 dBm)
      * 5200.0 MHz [40] (disabled)
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    caps = inventory._phy_band_support("phy0")
    assert caps["supports_2ghz"] is False
    assert caps["supports_5ghz"] is True
    assert caps["supports_6ghz"] is False


def test_band_support_parses_decimal_6ghz(monkeypatch):
    iw_out = """
Wiphy phy0
  Band 3:
    Frequencies:
      * 5955.0 MHz [1] (disabled)
      * 5975.0 MHz [5] (23.0 dBm)
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    caps = inventory._phy_band_support("phy0")
    assert caps["supports_2ghz"] is False
    assert caps["supports_5ghz"] is False
    assert caps["supports_6ghz"] is True


def test_band_support_skips_no_ir(monkeypatch):
    iw_out = """
Wiphy phy0
  Band 1:
    Frequencies:
      * 2412.0 MHz [1] (no IR)
      * 2437.0 MHz [6] (no-IR)
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    caps = inventory._phy_band_support("phy0")
    assert caps["supports_2ghz"] is False
    assert caps["supports_5ghz"] is False
    assert caps["supports_6ghz"] is False


def test_band_support_6ghz_false_without_6ghz_freqs(monkeypatch):
    iw_out = """
Wiphy phy0
  Band 1:
    Frequencies:
      * 2412.0 MHz [1] (22.0 dBm)
  Band 2:
    Frequencies:
      * 5180.0 MHz [36] (23.0 dBm)
"""
    monkeypatch.setattr(inventory, "_run_iw", lambda _args: iw_out)
    caps = inventory._phy_band_support("phy0")
    assert caps["supports_2ghz"] is True
    assert caps["supports_5ghz"] is True
    assert caps["supports_6ghz"] is False


def test_inventory_consumes_injected_snapshot_without_direct_probes(monkeypatch):
    snapshot = make_host_facts_snapshot()

    def fail_probe(*_args, **_kwargs):
        pytest.fail("snapshot-backed inventory must not run a legacy probe")

    for name in (
        "_parse_iw_dev",
        "_parse_iw_reg_get",
        "_phy_supports_ap",
        "_phy_supports_wifi6",
        "_phy_supports_80mhz",
        "_phy_band_support",
        "_detect_bus_type",
    ):
        monkeypatch.setattr(inventory, name, fail_probe)

    result = inventory.get_adapters(host_facts_snapshot=snapshot)

    assert set(result) == {"global_regdom", "recommended", "adapters", "notes"}
    assert result["recommended"] == "wlan1"
    assert result["adapters"][0]["supports_ap"] is True
    assert result["adapters"][0]["supports_5ghz"] is True
    assert result["adapters"][0]["supports_80mhz"] is True


def test_snapshot_inventory_matches_known_good_legacy_output(monkeypatch):
    snapshot = make_host_facts_snapshot()
    monkeypatch.setattr(
        inventory,
        "_parse_iw_dev",
        lambda: [{"ifname": "wlan1", "phy": "phy1"}],
    )
    monkeypatch.setattr(
        inventory,
        "_parse_iw_reg_get",
        lambda: {
            "global": {
                "country": "US",
                "raw_header": "country US: DFS-FCC",
            },
            "phys": {
                "phy1": {
                    "country": "US",
                    "source": "self-managed",
                    "raw_header": "country US: DFS-FCC",
                }
            },
        },
    )
    monkeypatch.setattr(inventory, "_phy_supports_ap", lambda _phy: True)
    monkeypatch.setattr(inventory, "_phy_supports_wifi6", lambda _phy: True)
    monkeypatch.setattr(inventory, "_phy_supports_80mhz", lambda _phy: True)
    monkeypatch.setattr(
        inventory,
        "_phy_band_support",
        lambda _phy: {
            "supports_2ghz": True,
            "supports_5ghz": True,
            "supports_6ghz": False,
        },
    )
    monkeypatch.setattr(inventory, "_detect_bus_type", lambda _ifname: "usb")

    legacy = inventory.get_adapters()
    snapshot_backed = inventory.get_adapters(host_facts_snapshot=snapshot)

    assert snapshot_backed == legacy


def test_partial_phy_failure_keeps_adapter_but_capabilities_unknown():
    snapshot = make_host_facts_snapshot()
    failed_phy = replace(
        snapshot.iw_phys[0],
        interface_modes_known=False,
        supported_interface_modes=(),
        supports_ap=None,
        supports_2ghz=None,
        supports_5ghz=None,
        supports_6ghz=None,
        supports_80mhz=None,
        supports_wifi6=None,
        supports_ap_managed_concurrency=None,
        frequencies=(),
    )
    failed_adapter = replace(
        snapshot.adapters[0],
        supports_ap=None,
        supports_2ghz=None,
        supports_5ghz=None,
        supports_6ghz=None,
        supports_80mhz=None,
        supports_wifi6=None,
    )
    snapshot = replace(
        snapshot,
        iw_phys=(failed_phy,),
        adapters=(failed_adapter,),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.phy.phy1",
                kind="parse",
                message="supported interface mode facts were not found",
                exit_status=0,
            ),
        ),
    )

    result = inventory.get_adapters(host_facts_snapshot=snapshot)
    adapter = result["adapters"][0]

    assert result["recommended"] is None
    assert adapter["supports_ap"] is None
    assert adapter["supports_2ghz"] is None
    assert adapter["supports_5ghz"] is None
    assert adapter["supports_6ghz"] is None
    assert adapter["supports_80mhz"] is None
    assert adapter["supports_wifi6"] is None
    assert "no_ap_mode" in adapter["warnings"]


def test_partial_iw_dev_failure_keeps_successful_adapter_facts():
    snapshot = make_host_facts_snapshot()
    incomplete_interface = host_facts.IwInterfaceFacts(
        ifname="wlan9",
        phy=None,
        interface_type="managed",
        ssid_present=False,
    )
    incomplete_adapter = replace(
        snapshot.adapters[0],
        ifname="wlan9",
        phy=None,
        bus="unknown",
        supports_ap=None,
        supports_2ghz=None,
        supports_5ghz=None,
        supports_6ghz=None,
        supports_80mhz=None,
        supports_wifi6=None,
        regulatory_country=None,
        regulatory_source=None,
    )
    snapshot = replace(
        snapshot,
        iw_dev=replace(
            snapshot.iw_dev,
            interfaces=(*snapshot.iw_dev.interfaces, incomplete_interface),
        ),
        adapters=(*snapshot.adapters, incomplete_adapter),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.dev",
                kind="parse",
                message="interface wlan9 has no valid phy identifier",
                exit_status=0,
            ),
        ),
    )

    result = inventory.get_adapters(host_facts_snapshot=snapshot)
    by_ifname = {item["ifname"]: item for item in result["adapters"]}

    assert result["error"] == "snapshot_iw_dev_unavailable"
    assert result["recommended"] == "wlan1"
    assert by_ifname["wlan1"]["supports_ap"] is True
    assert by_ifname["wlan9"]["supports_ap"] is None
    assert "no_ap_mode" in by_ifname["wlan9"]["warnings"]


@pytest.mark.parametrize("kind", ("missing", "parse"))
def test_missing_or_malformed_iw_dev_uses_legacy_error_shape(kind):
    snapshot = make_host_facts_snapshot()
    snapshot = replace(
        snapshot,
        iw_dev=replace(snapshot.iw_dev, interfaces=()),
        iw_phys=(),
        adapters=(),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.dev",
                kind=kind,
                message="iw dev facts unavailable",
                exit_status=None if kind == "missing" else 0,
            ),
        ),
    )

    assert inventory.get_adapters(host_facts_snapshot=snapshot) == {
        "error": "snapshot_iw_dev_unavailable",
        "adapters": [],
        "recommended": None,
        "global_regdom": None,
    }


def test_unknown_regulatory_snapshot_stays_conservative():
    snapshot = make_host_facts_snapshot()
    snapshot = replace(
        snapshot,
        regulatory=replace(
            snapshot.regulatory,
            global_country=None,
            global_raw_header=None,
            phys=(),
        ),
        adapters=(
            replace(
                snapshot.adapters[0],
                regulatory_country=None,
                regulatory_source=None,
            ),
        ),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.regulatory",
                kind="parse",
                message="regulatory output contained no country facts",
                exit_status=0,
            ),
        ),
    )

    result = inventory.get_adapters(host_facts_snapshot=snapshot)
    adapter = result["adapters"][0]

    assert result["global_regdom"] == {"country": "unknown", "raw": None}
    assert adapter["regdom"] == {
        "country": "unknown",
        "source": "unknown",
        "global_country": "unknown",
        "raw_phy": None,
        "raw_global": None,
    }
    assert "regdom_global_or_unknown_may_limit_ap_or_5ghz_or_6ghz" in adapter[
        "warnings"
    ]


def test_snapshot_projection_keeps_legacy_bus_vocabulary():
    snapshot = make_host_facts_snapshot()
    snapshot = replace(
        snapshot,
        adapters=(replace(snapshot.adapters[0], bus="platform"),),
    )

    result = inventory.get_adapters(host_facts_snapshot=snapshot)

    assert result["adapters"][0]["bus"] == "unknown"
