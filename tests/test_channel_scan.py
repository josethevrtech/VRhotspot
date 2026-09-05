from types import SimpleNamespace
import subprocess

import pytest

from vr_hotspotd.engine import channel_scan as scan
from vr_hotspotd.engine.channel_geometry import (
    channel_block, channel_frequency, center_channel, normalize_width,
)


def phy_text(channels, band="5ghz", flags=None):
    flags = flags or {}
    return "Wiphy phy0\n\tBand 1:\n\t\tFrequencies:\n" + "\n".join(
        f"\t\t\t* {channel_frequency(band, ch)} MHz [{ch}] (20.0 dBm) {flags.get(ch, '')}"
        for ch in channels
    )


def inventory(peer="", target_type="managed", other_phy=""):
    return (
        f"phy#0\n\tInterface wlan0\n\t\tifindex 3\n\t\ttype {target_type}\n"
        + peer + other_phy
    )


def bss(freq, extra="", identity="00:11:22:33:44:55"):
    return f"BSS {identity}(on wlan0)\n\tfreq: {freq}\n\tsignal: -60.00 dBm\n{extra}"


def mock_radio(monkeypatch, *, capabilities=None, dev=None, link="Not connected.",
               output="", failure=None):
    calls = []
    monkeypatch.setattr(scan, "_iw_bin", lambda: "/usr/sbin/iw")
    capabilities = capabilities if capabilities is not None else phy_text(
        (36, 40, 44, 48, 149, 153, 157, 161)
    )
    dev = inventory() if dev is None else dev
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["timeout"] <= 8
        assert kwargs["env"]["LC_ALL"] == "C"
        args = argv[1:]
        if failure and failure(args):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if args == ["dev"]:
            value = dev() if callable(dev) else dev
        elif args == ["phy", "phy0", "info"]:
            value = capabilities
        elif args[-1] == "link":
            value = link(args[1]) if callable(link) else link
        elif args[:3] == ["dev", "wlan0", "scan"]:
            value = output
        else:
            raise AssertionError(f"unexpected command: {argv}")
        return SimpleNamespace(returncode=0, stdout=value, stderr="")
    monkeypatch.setattr(scan.host_probes.subprocess, "run", run)
    return calls


def scanned(calls):
    return [argv for argv, _ in calls if "scan" in argv]


@pytest.mark.parametrize("band,primary,width,expected", [
    ("5ghz", 36, 80, (36, 40, 44, 48)),
    ("5ghz", 48, 80, (36, 40, 44, 48)),
    ("5ghz", 177, 80, (165, 169, 173, 177)),
    ("5ghz", 179, 80, None),
    ("5ghz", 40, 40, (36, 40)),
    ("5ghz", 44, 40, (44, 48)),
    ("5ghz", 161, 160, tuple(range(149, 178, 4))),
    ("5ghz", 132, 160, None),
    ("6ghz", 5, 20, (5,)),
    ("6ghz", 5, 40, (1, 5)),
    ("6ghz", 21, 80, (17, 21, 25, 29)),
    ("6ghz", 229, 80, None),
    ("6ghz", 233, 20, (233,)),
    ("6ghz", 237, 20, None),
    ("6ghz", 5, 160, tuple(range(1, 30, 4))),
    ("2.4ghz", 1, 40, (1, 5)),
    ("2.4ghz", 11, 40, (7, 11)),
    ("2.4ghz", 14, 40, None),
])
def test_shared_geometry(band, primary, width, expected):
    assert channel_block(band, primary, width) == expected
    if expected:
        assert center_channel(band, primary, width) == (expected[0] + expected[-1]) // 2


@pytest.mark.parametrize("band,width,expected", [
    ("2.4ghz", None, 20), ("2.4ghz", "auto", 20),
    ("2.4ghz", "80", 20), ("5ghz", None, 80),
    ("6ghz", "auto", 80), ("5ghz", "40", 40),
])
def test_width_defaults(band, width, expected):
    assert normalize_width(band, width) == expected


@pytest.mark.parametrize("flag", [
    "(disabled)", "(no IR)", "(NO-IR)", "(no_IR)", "(radar detection)",
    "(passive scanning)", "(no 20MHz)", "(no OFDM)", "(DFS)",
])
def test_every_constituent_must_allow_ap(flag):
    output = phy_text((36, 40, 44, 48), flags={44: flag})
    assert scan._eligible_candidates(output, "5ghz", 80) == []
    assert [row["channel"] for row in scan._eligible_candidates(output, "5ghz", 20)] == [36, 40, 48]


@pytest.mark.parametrize("flag,width", [("(no 80MHz)", 80), ("(no 160MHz)", 160)])
def test_whole_block_bandwidth_restrictions(flag, width):
    assert scan._eligible_candidates(
        phy_text(range(36, 65, 4), flags={48: flag}), "5ghz", width
    ) == ([] if width == 160 else scan._eligible_candidates(phy_text((52, 56, 60, 64)), "5ghz", 80))


def test_missing_secondary_is_not_80mhz_candidate():
    assert scan._eligible_candidates(phy_text((36, 40, 44)), "5ghz", 80) == []


def test_ht40_direction_flag_does_not_remove_other_primary():
    rows = scan._eligible_candidates(phy_text((36, 40), flags={36: "(no HT40+)"}), "5ghz", 40)
    assert [row["channel"] for row in rows] == [40]


def test_multiline_restriction_and_frequency_mapping():
    output = phy_text((36, 40, 44, 48), flags={44: "\n\t\t\t\t(no IR)"})
    assert scan._eligible_candidates(output, "5ghz", 80) == []
    assert scan._parse_frequencies("* 5180 MHz [149] (20.0 dBm)", "5ghz") == {}
    assert channel_frequency("2.4ghz", 14) == 2484
    assert scan._parse_frequencies("* 2484 MHz [14] (20.0 dBm)", "2.4ghz")[14]["frequency_mhz"] == 2484


@pytest.mark.parametrize("width,last", [(20, 229), (40, 229), (80, 213), (160, 213)])
def test_6ghz_psc_and_upper_band_edge(width, last):
    rows = scan._eligible_candidates(phy_text(range(1, 234, 4), "6ghz"), "6ghz", width)
    assert rows[0]["channel"] == 5
    assert rows[-1]["channel"] == last
    assert all((row["channel"] - 5) % 16 == 0 for row in rows)
    assert all(max(row["block_channels"]) <= 233 for row in rows)


@pytest.mark.parametrize("width", [20, 40])
def test_24ghz_width_and_channel14_exclusion(width):
    rows = scan._eligible_candidates(phy_text(range(1, 15), "2.4ghz"), "2.4ghz", width)
    assert rows
    assert 14 not in [row["channel"] for row in rows]


def test_idle_channels_are_candidates_and_current_only_wins_equal_score(monkeypatch):
    calls = mock_radio(monkeypatch, output=bss(5180))
    assert scan.select_best_channel("wlan0", "5ghz", 36) == 149
    assert scan.select_best_channel("wlan0", "5ghz", 157) == 157
    assert scanned(calls)
    assert set(scanned(calls)[0][5:]) == {"5180", "5200", "5220", "5240", "5745", "5765", "5785", "5805"}


def test_empty_success_is_clean_but_error_is_unknown(monkeypatch):
    mock_radio(monkeypatch)
    assert scan.select_best_channel("wlan0", current_channel=44) == 44
    assert scan.select_best_channel("wlan0") == 36
    mock_radio(monkeypatch, failure=lambda args: "scan" in args)
    assert scan.select_best_channel("wlan0", current_channel=44) == 44
    assert scan.select_best_channel("wlan0", current_channel=52) is None
    assert scan.select_best_channel("wlan0") is None


@pytest.mark.parametrize("output", ["garbage", "BSS broken\n\tno frequency here\n"])
def test_unparseable_scan_is_not_clean(monkeypatch, output):
    mock_radio(monkeypatch, output=output)
    assert scan.select_best_channel("wlan0") is None
    assert scan.scan_channels("wlan0")[0]["scan_status"] == "unparseable"


@pytest.mark.parametrize("dev,link", [
    (inventory(target_type="AP"), "Not connected."),
    (inventory(), "Connected to 00:11:22:33:44:55"),
    (inventory(), ""),
    (inventory(), "unexpected"),
    (inventory(peer="\tInterface wlan1\n\t\ttype AP\n"), "Not connected."),
    (inventory(peer="\tInterface wlan1\n\t\ttype monitor\n"), "Not connected."),
    (inventory(peer="\tUnnamed/non-netdev interface\n\t\ttype P2P-device\n"), "Not connected."),
    (inventory(peer="\tInterface wlan1\n\t\ttype managed\n"), lambda name: "Connected to xx" if name == "wlan1" else "Not connected."),
    ("Interface wlan0\n type managed", "Not connected."),
    ("", "Not connected."),
])
def test_busy_unknown_or_shared_radio_never_scans(monkeypatch, dev, link):
    calls = mock_radio(monkeypatch, dev=dev, link=link)
    assert scan.select_best_channel("wlan0") is None
    assert scanned(calls) == []


def test_other_phy_activity_is_not_shared(monkeypatch):
    calls = mock_radio(monkeypatch, dev=inventory(other_phy="phy#1\n\tInterface other\n\t\ttype AP\n"))
    assert scan.select_best_channel("wlan0") == 36
    assert len(scanned(calls)) == 1


def test_link_timeout_never_scans(monkeypatch):
    calls = mock_radio(monkeypatch, failure=lambda args: args[-1] == "link")
    assert scan.select_best_channel("wlan0") is None
    assert scanned(calls) == []


def test_second_inventory_guard_catches_new_connection(monkeypatch):
    states = iter((inventory(), inventory(target_type="AP")))
    calls = mock_radio(monkeypatch, dev=lambda: next(states))
    rows = scan.scan_channels("wlan0")
    assert rows[0]["scan_status"] == "radio_state_changed"
    assert scanned(calls) == []


@pytest.mark.parametrize("ifname,band,width", [
    ("-bad", "5ghz", 80), ("wlan0", "unknown", 80), ("wlan0", "5ghz", 320),
    ("name too long to be an interface", "5ghz", 80),
])
def test_bad_arguments_do_not_probe(monkeypatch, ifname, band, width):
    calls = mock_radio(monkeypatch)
    assert scan.scan_channels(ifname, band, width) == []
    assert calls == []


def test_missing_iw_does_not_claim_current_is_legal(monkeypatch):
    monkeypatch.setattr(scan, "_iw_bin", lambda: None)
    assert scan.select_best_channel("wlan0", current_channel=36) is None


def test_80mhz_bss_overlaps_all_constituents_once():
    rows = scan._eligible_candidates(phy_text((36, 40, 44, 48, 149, 153, 157, 161)), "5ghz", 20)
    extra = "\tVHT operation:\n\t\t* channel width: 1 (80 MHz)\n\t\t* center freq segment 1: 42\n\t\t* center freq segment 2: 0\n"
    scan._score_candidates(rows, bss(5180, extra), "5ghz")
    assert [row["channel"] for row in rows if row["interference_count"] == 1] == [36, 40, 44, 48]
    assert all(row["interference_count"] <= 1 for row in rows)


@pytest.mark.parametrize("extra,expected", [
    ("", [(2402, 2422)]),
    ("secondary channel offset: above\n", [(2402, 2442)]),
    ("secondary channel offset: below\n", [(2382, 2422)]),
])
def test_ht_spectrum(extra, expected):
    assert scan._bss_spectrum(bss(2412, extra))["intervals"] == expected


def test_24ghz_adjacent_overlap_is_not_same_channel_only():
    rows = scan._eligible_candidates(phy_text(range(1, 12), "2.4ghz"), "2.4ghz", 20)
    scan._score_candidates(rows, bss(2412), "2.4ghz")
    assert [row["channel"] for row in rows if row["interference_count"]] == [1, 2, 3, 4]
    assert rows[0]["score"] < rows[1]["score"] < rows[2]["score"]


@pytest.mark.parametrize("width,seg1,seg2,expected", [
    ("2 (160 MHz)", 50, 0, [(5170, 5330)]),
    ("1 (80 MHz)", 42, 50, [(5170, 5330)]),
    ("3 (80+80 MHz)", 42, 155, [(5170, 5250), (5735, 5815)]),
])
def test_wide_vht_spectrum(width, seg1, seg2, expected):
    extra = f"channel width: {width}\ncenter freq segment 1: {seg1}\ncenter freq segment 2: {seg2}\n"
    assert scan._bss_spectrum(bss(5180, extra))["intervals"] == expected


@pytest.mark.parametrize("width,seg0,seg1,expected", [
    ("20", 5, 0, [(5965, 5985)]),
    ("40", 3, 0, [(5945, 5985)]),
    ("80", 7, 0, [(5945, 6025)]),
    ("80+80 or 160", 7, 15, [(5945, 6105)]),
    ("80+80 or 160", 7, 39, [(5945, 6025), (6105, 6185)]),
])
def test_6ghz_he_spectrum(width, seg0, seg1, expected):
    extra = (
        "HE Operation:\n 6 GHz Operation Information: 0x0502070000\n"
        f"  Channel Width: {width} MHz\n"
        f"  Center Frequency Segment 0: {seg0}\n"
        f"  Center Frequency Segment 1: {seg1}\n"
    )
    assert scan._bss_spectrum(bss(5975, extra))["intervals"] == expected
