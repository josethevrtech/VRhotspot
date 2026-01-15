import vr_hotspotd.lifecycle as lifecycle


def test_validate_channel_for_band_2g_invalid() -> None:
    channel, warning = lifecycle._validate_channel_for_band("2.4ghz", 36, "US")
    assert channel == 6
    assert warning == "channel_invalid_for_band_overridden"


def test_validate_channel_for_band_2g_valid() -> None:
    channel, warning = lifecycle._validate_channel_for_band("2.4ghz", 11, "US")
    assert channel == 11
    assert warning is None


def test_validate_channel_for_band_5g_invalid() -> None:
    channel, warning = lifecycle._validate_channel_for_band("5ghz", 6, "US")
    assert channel == 36
    assert warning == "channel_invalid_for_band_overridden"


def test_validate_channel_for_band_5g_valid() -> None:
    channel, warning = lifecycle._validate_channel_for_band("5ghz", 36, "US")
    assert channel == 36
    assert warning is None
