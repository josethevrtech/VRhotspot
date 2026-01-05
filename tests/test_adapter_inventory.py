import vr_hotspotd.adapters.inventory as inventory


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
