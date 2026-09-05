"""Shared channel geometry; kernel/driver regulatory permission is a separate check."""

from typing import Optional, Tuple


def normalize_band(band: str) -> Optional[str]:
    value = str(band or "").lower().strip()
    for canonical, aliases in (
        ("2.4ghz", ("2", "2g", "2ghz", "2.4", "2.4ghz")),
        ("5ghz", ("5", "5g", "5ghz")),
        ("6ghz", ("6", "6g", "6ghz", "6e")),
    ):
        if value in aliases:
            return canonical
    return None


def normalize_width(band: str, width: object = None) -> int:
    value = str(width if width is not None else "auto").lower().strip()
    if value == "auto":
        return 20 if band == "2.4ghz" else 80
    if value not in ("20", "40", "80", "160"):
        raise ValueError("invalid_channel_width")
    # A 5/6 GHz profile may fall back to 2.4 GHz. Never carry VHT width into it.
    return 20 if band == "2.4ghz" and int(value) > 40 else int(value)


def channel_frequency(band: str, channel: int) -> Optional[int]:
    if isinstance(channel, bool) or not isinstance(channel, int):
        return None
    if band == "2.4ghz" and 1 <= channel <= 14:
        return 2484 if channel == 14 else 2407 + channel * 5
    if band == "5ghz" and (
        channel in range(36, 145, 4) or channel in range(149, 178, 4)
    ):
        return 5000 + channel * 5
    if band == "6ghz" and channel in range(1, 234, 4):
        return 5950 + channel * 5
    return None


def channel_block(band: str, primary: int, width: int) -> Optional[Tuple[int, ...]]:
    """Return every 20 MHz constituent; this does not authorize its use."""
    if channel_frequency(band, primary) is None or width not in (20, 40, 80, 160):
        return None
    if width == 20:
        return (primary,)
    if band == "2.4ghz":
        if width != 40 or primary == 14:
            return None
        secondary = primary + 4 if primary <= 7 else primary - 4
        return tuple(sorted((primary, secondary)))
    if band == "6ghz":
        count = width // 20
        first = 1 + ((primary - 1) // (count * 4)) * count * 4
        block = tuple(range(first, first + count * 4, 4))
        return block if block[-1] <= 233 else None
    if width == 40:
        starts = tuple(range(36, 65, 8)) + tuple(range(100, 145, 8)) + tuple(range(149, 178, 8))
    elif width == 80:
        starts = (36, 52, 100, 116, 132, 149, 165)
    else:
        starts = (36, 100, 149)
    for first in starts:
        block = tuple(range(first, first + width // 5, 4))
        if primary in block and all(channel_frequency(band, ch) is not None for ch in block):
            return block
    return None


def center_channel(band: str, primary: int, width: int) -> Optional[int]:
    block = channel_block(band, primary, width)
    return (block[0] + block[-1]) // 2 if block else None


def ht40_capability(band: str, primary: int, width: int) -> Optional[str]:
    if width < 40:
        return None
    block = channel_block(band, primary, 40)
    if not block:
        return None
    return "HT40+" if primary == block[0] else "HT40-"


def hostapd_oper_width(width: int) -> int:
    """hostapd VHT/HE enum: 0=20/40, 1=80, 2=160 (3 is 80+80)."""
    return {20: 0, 40: 0, 80: 1, 160: 2}[width]


def six_ghz_operating_class(width: int) -> int:
    return {20: 131, 40: 132, 80: 133, 160: 134}[width]
