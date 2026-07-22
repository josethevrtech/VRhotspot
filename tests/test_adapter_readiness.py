from dataclasses import replace

from vr_hotspotd import host_facts
from vr_hotspotd.adapters import readiness
from tests.host_facts_snapshot_factory import make_host_facts_snapshot


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


def test_readiness_consumes_injected_host_facts_snapshot():
    snapshot = make_host_facts_snapshot()

    model = readiness.build_readiness_model(host_facts_snapshot=snapshot)

    assert model["recommended"] == "wlan1"
    assert model["basic_mode_recommended"] == "wlan1"
    assert model["adapters"][0]["readiness_state"] == "good_for_vr"
    assert model["adapters"][0]["supports_ap_mode"] is True


def test_snapshot_readiness_matches_known_good_legacy_inventory():
    snapshot = make_host_facts_snapshot()
    legacy_inventory = {
        "global_regdom": {
            "country": "US",
            "raw": "country US: DFS-FCC",
        },
        "recommended": "wlan1",
        "adapters": [
            {
                "id": "wlan1",
                "ifname": "wlan1",
                "phy": "phy1",
                "bus": "usb",
                "supports_ap": True,
                "supports_wifi6": True,
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_6ghz": False,
                "supports_80mhz": True,
                "regdom": {
                    "country": "US",
                    "source": "self-managed",
                    "global_country": "US",
                    "raw_phy": "country US: DFS-FCC",
                    "raw_global": "country US: DFS-FCC",
                },
            }
        ],
    }

    legacy = readiness.build_readiness_model(legacy_inventory)
    snapshot_backed = readiness.build_readiness_model(
        host_facts_snapshot=snapshot,
    )

    assert snapshot_backed == legacy


def test_partial_phy_failure_cannot_produce_a_readiness_pass():
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
    snapshot = replace(
        snapshot,
        iw_phys=(failed_phy,),
        adapters=(
            replace(
                snapshot.adapters[0],
                supports_ap=None,
                supports_2ghz=None,
                supports_5ghz=None,
                supports_6ghz=None,
                supports_80mhz=None,
                supports_wifi6=None,
            ),
        ),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.phy.phy1",
                kind="timeout",
                message="read-only command timed out",
                exit_status=124,
            ),
        ),
    )

    model = readiness.build_readiness_model(host_facts_snapshot=snapshot)
    adapter = model["adapters"][0]

    assert model["recommended"] is None
    assert model["basic_mode_recommended"] is None
    assert adapter["supports_ap_mode"] is None
    assert adapter["readiness_state"] == "unsupported"
    assert adapter["recommendation_score"] <= 20
    assert "ap_mode_unknown" in adapter["reason_codes"]


def test_unknown_regulatory_facts_do_not_produce_good_readiness():
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

    model = readiness.build_readiness_model(host_facts_snapshot=snapshot)
    adapter = model["adapters"][0]

    assert model["basic_mode_recommended"] is None
    assert adapter["readiness_state"] == "usable_with_limitations"
    assert adapter["basic_mode_visibility"]["selectable"] is False
    assert "regdom_global_or_unknown" in adapter["reason_codes"]


def test_unknown_non_adapter_host_facts_are_not_projected_as_passes_or_scored():
    snapshot = make_host_facts_snapshot()
    baseline = readiness.build_readiness_model(host_facts_snapshot=snapshot)
    unknown_firewall = replace(
        snapshot.firewall,
        backends=tuple(
            replace(
                backend,
                functional_active=None,
                service_state=None,
                service_active=None,
            )
            for backend in snapshot.firewall.backends
        ),
        selected_backend="unknown",
        rationale="no_firewall_detected",
    )
    snapshot = replace(
        snapshot,
        default_uplink=replace(
            snapshot.default_uplink,
            selected_interface=None,
            routes=(),
        ),
        network_manager=replace(
            snapshot.network_manager,
            nmcli_running=None,
            service_state=None,
            service_active=None,
        ),
        iwd=replace(
            snapshot.iwd,
            service_state=None,
            service_active=None,
        ),
        firewall=unknown_firewall,
        probe_errors=(
            host_facts.ProbeError(
                probe_id="network.default_uplink",
                kind="timeout",
                message="read-only command timed out",
                exit_status=124,
            ),
            host_facts.ProbeError(
                probe_id="network_manager.nmcli",
                kind="parse",
                message="nmcli state unknown",
                exit_status=0,
            ),
            host_facts.ProbeError(
                probe_id="iwd.service",
                kind="nonzero",
                message="service state unknown",
                exit_status=3,
            ),
            host_facts.ProbeError(
                probe_id="firewall.firewalld.functional",
                kind="permission",
                message="firewall state unavailable",
                exit_status=126,
            ),
        ),
    )

    model = readiness.build_readiness_model(host_facts_snapshot=snapshot)

    assert model == baseline
    serialized = repr(model)
    assert "default_uplink" not in serialized
    assert "network_manager" not in serialized
    assert "iwd" not in serialized
    assert "firewall" not in serialized


def test_missing_iw_dev_snapshot_returns_existing_no_adapter_readiness_shape():
    snapshot = make_host_facts_snapshot()
    snapshot = replace(
        snapshot,
        iw_dev=replace(snapshot.iw_dev, interfaces=()),
        iw_phys=(),
        adapters=(),
        probe_errors=(
            host_facts.ProbeError(
                probe_id="iw.dev",
                kind="missing",
                message="required tool is unavailable: iw",
                exit_status=None,
            ),
        ),
    )

    model = readiness.build_readiness_model(host_facts_snapshot=snapshot)

    assert model["recommended"] is None
    assert model["basic_mode_recommended"] is None
    assert model["adapters"] == []
    assert model["summary"]["readiness_state"] == "unsupported"
    assert model["summary"]["reason_codes"] == ["no_adapter_found"]
