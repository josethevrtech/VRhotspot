import vr_hotspotd.wifi_probe as wifi_probe


IW_LIST_SAMPLE = """
Wiphy phy0
  Supported interface modes:
     * managed
     * AP
  Band 2:
    Frequencies:
      * 5180 MHz [36] (23.0 dBm)
      * 5200 MHz [40] (23.0 dBm)
      * 5220 MHz [44] (23.0 dBm)
      * 5240 MHz [48] (23.0 dBm)
      * 5260 MHz [52] (23.0 dBm) (radar detection)
      * 5280 MHz [56] (23.0 dBm) (radar detection)
      * 5300 MHz [60] (23.0 dBm) (radar detection)
      * 5320 MHz [64] (23.0 dBm) (radar detection)
    VHT Capabilities (0x0):
      Supported Channel Width: 160 MHz, 80+80 MHz
"""


def _inventory():
    return {"adapters": [{"ifname": "wlan0", "phy": "phy0"}]}


def test_probe_80mhz_candidates_non_dfs(monkeypatch):
    monkeypatch.setattr(wifi_probe, "_run_iw_list", lambda: (0, IW_LIST_SAMPLE))
    monkeypatch.setattr(wifi_probe, "_run_iw_reg_get", lambda: (0, "country US: DFS-FCC"))

    res = wifi_probe.probe_5ghz_80(
        "wlan0",
        inventory=_inventory(),
        allow_dfs=False,
    )
    assert res["errors"] == []
    candidates = res["candidates"]
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand["primary_channel"] == 36
    assert cand["center_channel"] == 42
    assert "non_dfs" in cand["flags"]


def test_probe_80mhz_candidates_with_dfs(monkeypatch):
    monkeypatch.setattr(wifi_probe, "_run_iw_list", lambda: (0, IW_LIST_SAMPLE))
    monkeypatch.setattr(wifi_probe, "_run_iw_reg_get", lambda: (0, "country US: DFS-FCC"))

    res = wifi_probe.probe_5ghz_80(
        "wlan0",
        inventory=_inventory(),
        allow_dfs=True,
    )
    candidates = res["candidates"]
    assert len(candidates) == 2
    assert "non_dfs" in candidates[0]["flags"]
    assert "dfs" in candidates[1]["flags"]
    assert candidates[1]["center_channel"] == 58


def test_probe_prefers_primary_channel(monkeypatch):
    monkeypatch.setattr(wifi_probe, "_run_iw_list", lambda: (0, IW_LIST_SAMPLE))
    monkeypatch.setattr(wifi_probe, "_run_iw_reg_get", lambda: (0, "country US: DFS-FCC"))

    res = wifi_probe.probe_5ghz_80(
        "wlan0",
        inventory=_inventory(),
        allow_dfs=False,
        preferred_primary_channel=44,
    )
    cand = res["candidates"][0]
    assert cand["primary_channel"] == 44


def test_probe_requires_dfs_when_only_dfs_available(monkeypatch):
    iw_list_dfs_only = """
Wiphy phy0
  Supported interface modes:
     * managed
     * AP
  Band 2:
    Frequencies:
      * 5260 MHz [52] (23.0 dBm) (radar detection)
      * 5280 MHz [56] (23.0 dBm) (radar detection)
      * 5300 MHz [60] (23.0 dBm) (radar detection)
      * 5320 MHz [64] (23.0 dBm) (radar detection)
    VHT Capabilities (0x0):
      Supported Channel Width: 160 MHz, 80+80 MHz
"""
    monkeypatch.setattr(wifi_probe, "_run_iw_list", lambda: (0, iw_list_dfs_only))
    monkeypatch.setattr(wifi_probe, "_run_iw_reg_get", lambda: (0, "country US: DFS-FCC"))

    res = wifi_probe.probe_5ghz_80(
        "wlan0",
        inventory=_inventory(),
        allow_dfs=False,
    )
    codes = {err["code"] for err in res["errors"]}
    assert "dfs_required_but_disabled" in codes
    assert "non_dfs_80mhz_channels_unavailable" in codes
