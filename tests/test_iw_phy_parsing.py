import vr_hotspotd.lifecycle as lifecycle


def test_parse_supported_interface_modes() -> None:
    iw_text = """
    Wiphy phy0
        Supported interface modes:
         * managed
         * AP
         * AP/VLAN
    """
    assert lifecycle._parse_supported_interface_modes(iw_text) is True

    iw_text_no_ap = """
    Supported interface modes:
     * managed
     * monitor
    """
    assert lifecycle._parse_supported_interface_modes(iw_text_no_ap) is False

    iw_text_missing = "Wiphy phy0"
    assert lifecycle._parse_supported_interface_modes(iw_text_missing) is None


def test_parse_ap_managed_concurrency() -> None:
    iw_text = """
    valid interface combinations:
     * #{ managed } <= 1, #{ AP, P2P-client, P2P-GO } <= 1,
       total <= 2, #channels <= 1
    """
    assert lifecycle._parse_ap_managed_concurrency(iw_text) is True

    iw_text_limited = """
    valid interface combinations:
     * #{ managed } <= 1, total <= 1, #channels <= 1
    """
    assert lifecycle._parse_ap_managed_concurrency(iw_text_limited) is False

    iw_text_no_ap = """
    valid interface combinations:
     * #{ managed } <= 1, #{ P2P-client } <= 1, total <= 2, #channels <= 1
    """
    assert lifecycle._parse_ap_managed_concurrency(iw_text_no_ap) is False

    iw_text_missing = "Wiphy phy0"
    assert lifecycle._parse_ap_managed_concurrency(iw_text_missing) is None
