from vr_hotspotd import os_release


def test_apply_platform_overrides_pop_increases_timeout_at_default():
    cfg = {"ap_ready_timeout_s": 6.0}
    info = {
        "id": "pop",
        "id_like": "ubuntu debian",
        "name": "Pop!_OS",
    }
    out, warnings = os_release.apply_platform_overrides(cfg, info)
    assert out["ap_ready_timeout_s"] == 14.0
    assert "platform_pop_increased_ap_ready_timeout" in warnings


def test_apply_platform_overrides_pop_respects_custom_higher_timeout():
    cfg = {"ap_ready_timeout_s": 15.0}
    info = {
        "id": "pop",
        "id_like": "ubuntu debian",
        "name": "Pop!_OS",
    }
    out, warnings = os_release.apply_platform_overrides(cfg, info)
    assert out["ap_ready_timeout_s"] == 15.0
    assert "platform_pop_increased_ap_ready_timeout" not in warnings
