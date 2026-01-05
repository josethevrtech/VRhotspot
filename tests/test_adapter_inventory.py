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
