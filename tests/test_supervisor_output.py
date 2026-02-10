from collections import deque

from vr_hotspotd.engine.supervisor import _merge_head_tail


def test_merge_head_tail_preserves_supervisor_notes_with_zero_count():
    tail = deque(["[supervisor] hostapd_select system (vht_supported)"], maxlen=200)
    merged = _merge_head_tail([], tail, 0, 200)
    assert merged == ["[supervisor] hostapd_select system (vht_supported)"]


def test_merge_head_tail_uses_tail_when_stream_count_is_small():
    tail = deque(["line1", "line2"], maxlen=200)
    merged = _merge_head_tail([], tail, 2, 200)
    assert merged == ["line1", "line2"]
