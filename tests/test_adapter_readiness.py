from vr_hotspotd.adapters import readiness


def _adapter(**overrides):
    item = {
        "id": "wlan1",
        "ifname": "wlan1",
        "phy": "phy1",
        "driver": "mt7921u",
        "bus": "usb",
        "supports_ap": True,
        "supports_wifi6": True,
        "supports_2ghz": True,
        "supports_5ghz": True,
        "supports_6ghz": False,
        "supports_80mhz": True,
        "regdom": {
            "country": "US",
            "source": "global",
            "global_country": "US",
        },
    }
    item.update(overrides)
    return item


def _model(adapters, global_country="US"):
    return readiness.build_readiness_model(
        {
            "global_regdom": {"country": global_country, "raw": f"country {global_country}: DFS-FCC"},
            "recommended": adapters[0]["ifname"] if adapters else None,
            "adapters": adapters,
        }
    )


def test_good_usb_5ghz_80mhz_ap_adapter():
    model = _model([_adapter()])

    assert model["recommended"] == "wlan1"
    assert model["basic_mode_recommended"] == "wlan1"
    adapter = model["adapters"][0]
    assert adapter["readiness_state"] == "good_for_vr"
    assert adapter["six_ghz_state"] == "not_supported"
    assert adapter["recommendation_score"] > 0
    assert adapter["basic_mode_visibility"]["selectable"] is True
    assert {
        "supports_ap_mode",
        "usb_adapter",
        "supports_5ghz",
        "supports_80mhz",
        "regdom_valid",
        "basic_mode_visible",
    }.issubset(adapter["reason_codes"])


def test_wifi_6e_adapter_6ghz_blocked_by_regdomain():
    model = _model(
        [
            _adapter(
                ifname="wlan2",
                id="wlan2",
                supports_6ghz=True,
                supports_160mhz=True,
                regdom={
                    "country": "00",
                    "source": "global",
                    "global_country": "00",
                    "raw_global": "country 00: DFS-UNSET",
                },
            )
        ],
        global_country="00",
    )

    adapter = model["adapters"][0]
    assert adapter["readiness_state"] == "good_for_vr"
    assert adapter["six_ghz_state"] == "blocked_by_regdomain"
    assert "supports_6ghz" in adapter["reason_codes"]
    assert "regdom_global_or_unknown" in adapter["reason_codes"]
    assert "regdom_no_ir_blocks_6ghz" in adapter["reason_codes"]
    assert adapter["channel_width_hints"]["supports_160mhz"] is True


def test_internal_wlan0_deprioritized_when_usb_adapter_exists():
    model = _model(
        [
            _adapter(
                ifname="wlan0",
                id="wlan0",
                driver="iwlwifi",
                bus="pci",
            ),
            _adapter(ifname="wlan1", id="wlan1"),
        ]
    )

    by_ifname = {adapter["interface"]: adapter for adapter in model["adapters"]}
    assert model["recommended"] == "wlan1"
    assert model["basic_mode_recommended"] == "wlan1"
    assert by_ifname["wlan0"]["readiness_state"] == "not_recommended"
    assert by_ifname["wlan0"]["basic_mode_visibility"]["visible"] is False
    assert by_ifname["wlan0"]["basic_mode_visibility"]["reason"] == "internal_deprioritized"
    assert "wlan0_deprioritized" in by_ifname["wlan0"]["reason_codes"]


def test_no_adapter_found_summary():
    model = _model([])

    assert model["recommended"] is None
    assert model["basic_mode_recommended"] is None
    assert model["adapters"] == []
    assert model["summary"]["readiness_state"] == "unsupported"
    assert model["summary"]["six_ghz_state"] == "unknown"
    assert model["summary"]["recommendation_score"] == 0
    assert model["summary"]["reason_codes"] == ["no_adapter_found"]


def test_adapter_missing_ap_mode_is_unsupported():
    model = _model([_adapter(supports_ap=False)])

    adapter = model["adapters"][0]
    assert model["recommended"] is None
    assert adapter["readiness_state"] == "unsupported"
    assert adapter["basic_mode_visibility"]["selectable"] is False
    assert adapter["basic_mode_visibility"]["reason"] == "missing_ap_mode"
    assert "missing_ap_mode" in adapter["reason_codes"]


def test_adapter_missing_80mhz_is_not_recommended():
    model = _model([_adapter(supports_80mhz=False)])

    adapter = model["adapters"][0]
    assert adapter["readiness_state"] == "not_recommended"
    assert adapter["basic_mode_visibility"]["selectable"] is False
    assert adapter["basic_mode_visibility"]["reason"] == "missing_80mhz"
    assert "missing_80mhz" in adapter["reason_codes"]
    assert adapter["recommendation_score"] < _model([_adapter()])["adapters"][0]["recommendation_score"]
