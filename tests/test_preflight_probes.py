from vr_hotspotd import preflight


def test_preflight_run_preserves_aggregation_order_and_shape(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "_check_rfkill",
        lambda: (
            ["rfkill_blocked"],
            ["rfkill_warning"],
            {"checked": True},
        ),
    )
    monkeypatch.setattr(
        preflight,
        "_check_regdom",
        lambda country, adapter, band: (
            [f"regdom_error:{country}:{band}"],
            ["regdom_warning"],
            {"adapter": adapter["ifname"]},
        ),
    )
    monkeypatch.setattr(
        preflight,
        "_check_hostapd_features",
        lambda band, security: (
            [f"hostapd_error:{band}:{security}"],
            ["hostapd_warning"],
            {"sae": False, "he": False},
        ),
    )
    monkeypatch.setattr(
        preflight,
        "_check_subnet_conflicts",
        lambda gateway: (
            [f"subnet_error:{gateway}"],
            ["subnet_warning"],
            {"conflicts": ["route:eth0:10.42.0.0/24"]},
        ),
    )

    result = preflight.run(
        {
            "country": "US",
            "bridge_mode": False,
            "lan_gateway_ip": "10.42.0.1",
        },
        adapter={"ifname": "wlan1"},
        band="6ghz",
        ap_security="wpa3_sae",
        enable_internet=False,
    )

    assert result == {
        "errors": [
            "rfkill_blocked",
            "regdom_error:US:6ghz",
            "hostapd_error:6ghz:wpa3_sae",
            "subnet_error:10.42.0.1",
        ],
        "warnings": [
            "rfkill_warning",
            "regdom_warning",
            "hostapd_warning",
            "subnet_warning",
            "internet_disabled_no_nat",
        ],
        "details": {
            "rfkill": {"checked": True},
            "regdom": {"adapter": "wlan1"},
            "hostapd": {"sae": False, "he": False},
            "subnet": {"conflicts": ["route:eth0:10.42.0.0/24"]},
        },
    }


def test_hostapd_version_probe_preserves_capability_inference(monkeypatch):
    calls = []
    monkeypatch.setattr(
        preflight,
        "_resolve_hostapd_path",
        lambda: "/opt/vr-hotspot/vendor/hostapd",
    )

    def run(argv, timeout_s=1.2):
        calls.append((argv, timeout_s))
        return 0, "hostapd v2.11-devel\nSAE\nIEEE 802.11ax"

    monkeypatch.setattr(preflight, "_run", run)

    result = preflight._hostapd_caps()

    assert result == {
        "sae": True,
        "he": True,
        "raw": "hostapd v2.11-devel\nSAE\nIEEE 802.11ax",
    }
    assert calls == [
        (
            ["/opt/vr-hotspot/vendor/hostapd", "-vv"],
            1.2,
        )
    ]


def test_hostapd_version_probe_keeps_v_fallback_and_negative_caps(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "_resolve_hostapd_path", lambda: "/usr/sbin/hostapd")

    def run(argv, timeout_s=1.2):
        calls.append((argv, timeout_s))
        if argv[-1] == "-vv":
            return 1, ""
        return 0, "hostapd v2.9"

    monkeypatch.setattr(preflight, "_run", run)

    result = preflight._hostapd_caps()

    assert result == {
        "sae": False,
        "he": False,
        "raw": "hostapd v2.9",
    }
    assert [argv[-1] for argv, _timeout in calls] == ["-vv", "-v"]


def test_hostapd_feature_policy_keeps_unknown_as_warning(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "_hostapd_caps",
        lambda: {
            "sae": None,
            "he": None,
            "error": "hostapd_not_found",
        },
    )

    errors, warnings, details = preflight._check_hostapd_features(
        "6ghz",
        "wpa3_sae",
    )

    assert errors == []
    assert warnings == ["hostapd_sae_unknown", "hostapd_11ax_unknown"]
    assert details["error"] == "hostapd_not_found"


def test_subnet_probe_preserves_address_and_route_conflict_shapes(monkeypatch):
    responses = {
        ("/usr/sbin/ip", "-4", "-o", "addr", "show"): (
            0,
            "2: eth0    inet 10.42.0.23/24 brd 10.42.0.255 scope global eth0",
        ),
        ("/usr/sbin/ip", "-4", "route", "show"): (
            0,
            "\n".join(
                [
                    "default via 192.0.2.1 dev eth0",
                    "10.42.0.0/24 dev eth0 proto kernel",
                ]
            ),
        ),
    }
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/sbin/ip" if name == "ip" else None)
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda argv: responses[tuple(argv)],
    )

    errors, warnings, details = preflight._check_subnet_conflicts("10.42.0.1")

    assert errors == ["subnet_conflict"]
    assert warnings == []
    assert details == {
        "conflicts": [
            "addr:eth0:10.42.0.23",
            "route:eth0:10.42.0.0/24",
        ]
    }
