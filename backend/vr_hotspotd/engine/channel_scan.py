"""Conservative, pre-session channel selection. Never scan a busy/shared radio."""

import math
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from vr_hotspotd import host_probes
from vr_hotspotd.engine.channel_geometry import (
    channel_block,
    channel_frequency,
    normalize_band,
    normalize_width,
)

_IFNAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,14}$")
_FREQUENCY = re.compile(r"^(\s*)\*\s*(\d+)\s+MHz\s+\[(\d+)\](.*)$")
_MAX_PHY_INTERFACES = 8


def _iw_bin() -> Optional[str]:
    return shutil.which("iw") or ("/usr/sbin/iw" if os.path.exists("/usr/sbin/iw") else None)


def _run_iw(iw: str, *args: str, timeout: float = 2.0) -> Optional[str]:
    result = host_probes.run_command(
        [iw, *args], timeout_s=timeout, env={**os.environ, "LC_ALL": "C"},
    )
    return result.stdout if result.exit_status == 0 else None


def _idle_phy(iw: str, ifname: str) -> Tuple[Optional[str], bool]:
    """Unknown inventory/link state is not permission to disturb a radio."""
    output = _run_iw(iw, "dev")
    if output is None:
        return None, False
    # The shared parser intentionally describes named netdevs only. Refuse an
    # inventory containing an unnamed interface rather than silently omit it.
    if "Unnamed/non-netdev interface" in output:
        return None, False
    interfaces = host_probes.parse_iw_dev_facts(output)
    if any(not entry["phy"] for entry in interfaces):
        return None, False
    targets = [entry for entry in interfaces if entry["ifname"] == ifname]
    if len(targets) != 1 or not targets[0]["phy"]:
        return None, False
    phy = targets[0]["phy"]
    if not re.fullmatch(r"phy\d+", phy):
        return None, False
    peers = [entry for entry in interfaces if entry["phy"] == phy]
    if not peers or len(peers) > _MAX_PHY_INTERFACES:
        return phy, False
    for peer in peers:
        # Even dormant AP/P2P-device interfaces are refused: we cannot prove that
        # another owner is not about to use them. No state changes are made.
        if (
            str(peer["interface_type"] or "").lower() not in ("managed", "p2p-client")
            or peer.get("ssid_present")
            or not isinstance(peer["ifname"], str)
            or not _IFNAME.fullmatch(peer["ifname"])
        ):
            return phy, False
        link = _run_iw(iw, "dev", peer["ifname"], "link")
        if link is None or link.strip() != "Not connected.":
            return phy, False
    return phy, True


def _parse_frequencies(output: str, band: str) -> Dict[int, Dict[str, Any]]:
    """Read exact-PHY permissions, including multiline restrictions."""
    entries: Dict[int, Dict[str, Any]] = {}
    current = None
    indent = 0
    for raw in output.splitlines():
        match = _FREQUENCY.match(raw)
        if match:
            frequency, channel = int(match[2]), int(match[3])
            current = None
            if channel_frequency(band, channel) == frequency:
                current = {"frequency_mhz": frequency, "flags": match[4].lower()}
                entries[channel] = current
                indent = len(match[1].expandtabs())
        elif current is not None:
            padding = len(raw.expandtabs()) - len(raw.expandtabs().lstrip())
            if raw.strip() and padding > indent:
                current["flags"] += " " + raw.strip().lower()
            else:
                current = None
    return entries


def _eligible_candidates(output: str, band: str, width: int) -> List[Dict[str, Any]]:
    frequencies = _parse_frequencies(output, band)
    legal = {
        ch: entry for ch, entry in frequencies.items()
        if not re.search(
            r"disabled|no[ _-]?ir|passive.scan|radar|\bdfs\b|no\s*20\s*mhz|no\s*ofdm",
            entry["flags"],
        )
        and not (band == "6ghz" and re.search(r"no\s*he\b", entry["flags"]))
    }
    candidates: List[Dict[str, Any]] = []
    for primary in sorted(legal):
        # Channel 14 requires 802.11b, unlike our 2.4 GHz AP configuration.
        if band == "2.4ghz" and primary == 14:
            continue
        # PSC primaries 5,21,...,229 improve 6 GHz discovery. Every constituent
        # of the full bonded channel must still be permitted.
        if band == "6ghz" and (primary - 5) % 16:
            continue
        block = channel_block(band, primary, width)
        if not block or any(ch not in legal for ch in block):
            continue
        if width >= 80 and any(
            re.search(r"no\s*80\s*mhz", legal[ch]["flags"]) for ch in block
        ):
            continue
        if width >= 160 and any(
            re.search(r"no\s*160\s*mhz", legal[ch]["flags"]) for ch in block
        ):
            continue
        if width >= 40:
            pair = channel_block(band, primary, 40)
            if not pair or re.search(
                r"no\s*ht40\+" if primary == pair[0] else r"no\s*ht40-",
                legal[primary]["flags"],
            ):
                continue
        candidates.append({
            "channel": primary, "frequency_mhz": legal[primary]["frequency_mhz"],
            "width_mhz": width, "block_channels": list(block),
            "center_frequency_mhz": (
                legal[block[0]]["frequency_mhz"] + legal[block[-1]]["frequency_mhz"]
            ) / 2,
            "interference_count": None, "interference_weight": None, "score": None,
            "scan_status": "unavailable", "score_basis": "overlapping_bss_heuristic",
        })
    return candidates


def _bss_spectrum(block: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"^\s*freq:\s*(\d+)\s*$", block, re.M)
    if not match:
        return None
    primary = int(match[1])
    if 2400 <= primary <= 2500:
        band = "2.4ghz"
    elif 5000 <= primary < 5925:
        band = "5ghz"
    elif 5925 <= primary <= 7125:
        band = "6ghz"
    else:
        return None
    width, center, second = 20, float(primary), None
    offset = re.search(r"secondary channel offset:\s*(above|below)", block, re.I)
    if offset:
        width, center = 40, primary + (10 if offset[1].lower() == "above" else -10)
    # iw uses segment 1/2 labels, unlike hostapd's segment 0/1.
    segments = re.findall(r"center freq(?:uency)? segment\s*([12]):\s*(\d+)", block, re.I)
    centers: Dict[int, int] = {}
    for index, value in segments:
        centers.setdefault(int(index), int(value))
    wide = re.search(r"channel width:\s*\d+\s*\((80\+80|160|80)\s*MHz\)", block, re.I)
    if wide and centers.get(1):
        base = 5950 if band == "6ghz" else 5000
        width = 80 if wide[1] == "80+80" else int(wide[1])
        center = base + 5 * centers[1]
        if wide[1] == "80+80" and centers.get(2):
            second = base + 5 * centers[2]
        # Revised VHT signaling: width=1, seg1=80-center, seg2=160-center.
        elif width == 80 and centers.get(2) and abs(centers[1] - centers[2]) == 8:
            width, center = 160, base + 5 * centers[2]
    # Current iw's HE 6 GHz operation information uses textual widths and
    # center segments 0/1, unlike its legacy VHT output above.
    he = re.search(r"6 GHz Operation Information:\s*0x[^\n]*(.*)", block, re.S | re.I)
    if band == "6ghz" and he:
        detail = re.split(r"\n\s*EHT Operation", he[1], maxsplit=1, flags=re.I)[0]
        he_width = re.search(r"Channel Width:\s*(20|40|80|80\+80 or 160) MHz", detail, re.I)
        seg0 = re.search(r"Center Frequency Segment 0:\s*(\d+)", detail, re.I)
        seg1 = re.search(r"Center Frequency Segment 1:\s*(\d+)", detail, re.I)
        if he_width and seg0:
            c0, c1 = int(seg0[1]), int(seg1[1]) if seg1 else 0
            second = None
            if he_width[1] == "80+80 or 160":
                if not c1:
                    return None  # Cannot safely interpret the advertised wide BSS.
                if abs(c1 - c0) == 8:
                    width, center = 160, 5950 + c1 * 5
                else:
                    width, center, second = 80, 5950 + c0 * 5, 5950 + c1 * 5
            else:
                width, center = int(he_width[1]), 5950 + c0 * 5
    signal = re.search(r"^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", block, re.M | re.I)
    strength = float(signal[1]) if signal else -60.0
    weight = 1.0 + min(60.0, max(0.0, strength + 90.0)) / 60.0
    intervals = [(center - width / 2, center + width / 2)]
    if second is not None:
        intervals.append((second - 40, second + 40))
    return {"band": band, "intervals": intervals, "weight": weight}


def _score_candidates(candidates: List[Dict[str, Any]], output: str, band: str) -> None:
    # One BSS is counted once, even with multiple IEs or two 80 MHz segments.
    networks = [_bss_spectrum(block) for block in re.split(r"(?m)^BSS\s+", output)[1:]]
    if (output.strip() and not networks) or any(network is None for network in networks):
        for candidate in candidates:
            candidate["scan_status"] = "unparseable"
        return
    for candidate in candidates:
        half = candidate["width_mhz"] / 2
        low = candidate["center_frequency_mhz"] - half
        high = candidate["center_frequency_mhz"] + half
        count, weight = 0, 0.0
        for network in networks:
            if not network or network["band"] != band:
                continue
            overlap = sum(
                max(0.0, min(high, end) - max(low, start))
                for start, end in network["intervals"]
            )
            if overlap > 0:
                count += 1
                weight += overlap / 20.0 * network["weight"]
        candidate.update(
            interference_count=count, interference_weight=weight,
            score=100.0 / (1.0 + weight), scan_status="ok",
        )


def scan_channels(
    ifname: str, band: str = "5ghz", width_mhz: object = None,
) -> List[Dict[str, Any]]:
    """Return legal full-width candidates, including unobserved idle channels.

    A missing score is unknown, not a clean channel. Only an idle exact PHY is
    scanned. This never changes channels, interface state, regulatory settings,
    or saved configuration; only use results before AP startup.
    """
    band = normalize_band(band)
    if band is None or not isinstance(ifname, str) or not _IFNAME.fullmatch(ifname):
        return []
    try:
        width = normalize_width(band, width_mhz)
    except ValueError:
        return []
    iw = _iw_bin()
    if not iw:
        return []
    phy, idle = _idle_phy(iw, ifname)
    if not phy:
        return []
    capabilities = _run_iw(iw, "phy", phy, "info", timeout=3.0)
    if capabilities is None:
        return []
    candidates = _eligible_candidates(capabilities, band, width)
    if not candidates:
        return []
    if not idle:
        for candidate in candidates:
            candidate["scan_status"] = "radio_active_or_unknown"
        return candidates
    # Another manager may connect while capabilities are being read. Recheck
    # immediately before scanning. Lifecycle serializes this daemon's starts;
    # a foreign network manager can still race any userspace snapshot.
    if _idle_phy(iw, ifname) != (phy, True):
        for candidate in candidates:
            candidate["scan_status"] = "radio_state_changed"
        return candidates
    frequencies = sorted({
        channel_frequency(band, ch)
        for candidate in candidates for ch in candidate["block_channels"]
    })
    output = _run_iw(
        iw, "dev", ifname, "scan", "freq", *map(str, frequencies), timeout=8.0,
    )
    if output is not None:
        _score_candidates(candidates, output, band)
    return candidates


def select_best_channel(
    ifname: str, band: str = "5ghz", current_channel: Optional[int] = None,
    width_mhz: object = None,
) -> Optional[int]:
    """Only prefer current on an equivalent best tie, never on a 'top three'.

    On failure keep current only if exact-PHY permission verified its entire
    block. Otherwise return None for normal startup's validated fallback;
    never fabricate regulatory permission.
    """
    channels = scan_channels(ifname, band, width_mhz)
    current = next((row for row in channels if row["channel"] == current_channel), None)
    scored = [
        row for row in channels
        if row.get("scan_status") == "ok" and row.get("score") is not None
    ]
    if not scored:
        return current["channel"] if current else None
    best = max(row["score"] for row in scored)
    tied = [row for row in scored if math.isclose(row["score"], best, rel_tol=1e-12)]
    if current is not None and current in tied:
        return current["channel"]
    return min(row["channel"] for row in tied)
