from vr_hotspotd import host_probes


def test_parse_iw_dev_facts_keeps_type_and_association_without_ssid_value():
    output = """
phy#1
    Interface wlan1
        ifindex 8
        type managed
        ssid Private Upstream Name
    Interface p2p-dev-wlan1
        type P2P-device
phy#0
    Interface wlan0
        type AP
"""

    facts = host_probes.parse_iw_dev_facts(output)

    assert facts == [
        {
            "ifname": "wlan1",
            "phy": "phy1",
            "interface_type": "managed",
            "ssid_present": True,
        },
        {
            "ifname": "p2p-dev-wlan1",
            "phy": "phy1",
            "interface_type": "P2P-device",
            "ssid_present": False,
        },
        {
            "ifname": "wlan0",
            "phy": "phy0",
            "interface_type": "AP",
            "ssid_present": False,
        },
    ]
    assert "Private Upstream Name" not in repr(facts)


def test_parse_iw_dev_facts_tolerates_a_malformed_phy_header():
    assert host_probes.parse_iw_dev_facts("phy#\n  Interface wlan9\n    type managed") == [
        {
            "ifname": "wlan9",
            "phy": None,
            "interface_type": "managed",
            "ssid_present": False,
        }
    ]


def test_snapshot_mode_parser_keeps_ap_evidence_from_later_mode_blocks():
    output = """
Supported interface modes:
    * managed
Band 1:
Supported interface modes:
    * AP
    * AP/VLAN
"""

    assert host_probes.parse_all_supported_interface_modes(output) == [
        "managed",
        "AP",
        "AP/VLAN",
    ]


def test_parse_iw_frequencies_keeps_curated_band_and_restriction_facts():
    output = """
Band 1:
    Frequencies:
        * 2412.0 MHz [1] (22.0 dBm)
Band 2:
    Frequencies:
        * 5180 MHz [36] (23.0 dBm)
        * 5260 MHz [52] (20.0 dBm) (no IR, radar detection)
Band 3:
    Frequencies:
        * 5955.0 MHz [1] (disabled)
"""

    assert host_probes.parse_iw_frequencies(output) == [
        {
            "frequency_mhz": 2412,
            "channel": 1,
            "band": "2.4ghz",
            "disabled": False,
            "no_ir": False,
            "dfs": False,
        },
        {
            "frequency_mhz": 5180,
            "channel": 36,
            "band": "5ghz",
            "disabled": False,
            "no_ir": False,
            "dfs": False,
        },
        {
            "frequency_mhz": 5260,
            "channel": 52,
            "band": "5ghz",
            "disabled": False,
            "no_ir": True,
            "dfs": True,
        },
        {
            "frequency_mhz": 5955,
            "channel": 1,
            "band": "6ghz",
            "disabled": True,
            "no_ir": False,
            "dfs": False,
        },
    ]


def test_parse_default_routes_preserves_command_order_without_selecting_policy():
    output = "\n".join(
        [
            "default via 192.0.2.1 dev enp4s0 proto dhcp metric 100",
            "default via 198.51.100.1 dev wlan0 proto dhcp metric 600",
            "unreachable 203.0.113.0/24 metric 4278198272",
        ]
    )

    assert host_probes.parse_default_routes(output) == [
        {
            "interface": "enp4s0",
            "gateway": "192.0.2.1",
            "metric": 100,
            "protocol": "dhcp",
        },
        {
            "interface": "wlan0",
            "gateway": "198.51.100.1",
            "metric": 600,
            "protocol": "dhcp",
        },
    ]
    assert host_probes.parse_default_uplink(output) == "enp4s0"
