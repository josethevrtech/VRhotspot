import os
import sys


# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def _common_start_mocks(monkeypatch, cfg):
    import vr_hotspotd.lifecycle as lifecycle

    state = {"phase": "stopped"}

    def fake_update_state(**kwargs):
        state.update(kwargs)
        state.setdefault("phase", "stopped")
        return dict(state)

    monkeypatch.setattr(lifecycle, "ensure_config_file", lambda: None)
    monkeypatch.setattr(lifecycle, "_repair_impl", lambda correlation_id="start": None)
    monkeypatch.setattr(lifecycle, "load_state", lambda: {"phase": "stopped"})
    monkeypatch.setattr(lifecycle, "update_state", fake_update_state)
    monkeypatch.setattr(lifecycle, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        lifecycle,
        "get_adapters",
        lambda: {
            "adapters": [
                {
                    "ifname": "wlan1",
                    "phy": "phy1",
                    "bus": "usb",
                    "supports_ap": True,
                    "supports_5ghz": True,
                    "supports_80mhz": True,
                    "supports_wifi6": False,
                }
            ],
            "recommended": "wlan1",
        },
    )
    monkeypatch.setattr(lifecycle, "_nm_gate_check", lambda _ifname: None)
    monkeypatch.setattr(lifecycle, "_ensure_iface_up", lambda _ifname: True)
    monkeypatch.setattr(
        lifecycle,
        "_prepare_ap_interface",
        lambda _ifname, force_nm_disconnect=False: [],
    )
    monkeypatch.setattr(
        lifecycle.wifi_probe,
        "detect_firewall_backends",
        lambda: {"selected_backend": "nftables"},
    )
    monkeypatch.setattr(
        lifecycle.os_release,
        "read_os_release",
        lambda: {"id": "pop", "id_like": "ubuntu debian"},
    )
    monkeypatch.setattr(
        lifecycle.preflight,
        "run",
        lambda *_args, **_kwargs: {"errors": [], "warnings": [], "details": {}},
    )
    monkeypatch.setattr(lifecycle.system_tuning, "apply_pre", lambda _cfg: ({}, []))
    return lifecycle


def test_start_autoprovisions_missing_passphrase(monkeypatch):
    lifecycle = _common_start_mocks(
        monkeypatch,
        {"wpa2_passphrase": "", "band_preference": "5ghz"},
    )

    writes = []
    strict_calls = {}

    def fake_write_config_file(partial):
        writes.append(dict(partial))
        return partial

    def fake_start_5ghz_strict(**kwargs):
        strict_calls.update(kwargs)
        return lifecycle.LifecycleResult("started", {"phase": "running"})

    monkeypatch.setattr(lifecycle, "write_config_file", fake_write_config_file)
    monkeypatch.setattr(lifecycle, "_start_hotspot_5ghz_strict", fake_start_5ghz_strict)

    res = lifecycle.start_hotspot()

    assert res.code == "started"
    assert writes, "Expected auto-provisioned passphrase write"
    generated = writes[0].get("wpa2_passphrase")
    assert isinstance(generated, str) and len(generated) >= 8
    assert strict_calls.get("passphrase") == generated


def test_start_does_not_autoprovision_when_short_override_provided(monkeypatch):
    lifecycle = _common_start_mocks(
        monkeypatch,
        {"wpa2_passphrase": "", "band_preference": "5ghz"},
    )

    writes = []
    strict_called = {"value": False}

    def fake_write_config_file(partial):
        writes.append(dict(partial))
        return partial

    def fake_start_5ghz_strict(**_kwargs):
        strict_called["value"] = True
        return lifecycle.LifecycleResult("started", {"phase": "running"})

    monkeypatch.setattr(lifecycle, "write_config_file", fake_write_config_file)
    monkeypatch.setattr(lifecycle, "_start_hotspot_5ghz_strict", fake_start_5ghz_strict)

    res = lifecycle.start_hotspot(overrides={"wpa2_passphrase": "short"})

    assert res.code == "start_failed"
    assert res.state.get("last_error") == "invalid_passphrase_min_length_8"
    assert not writes, "Should not auto-provision when caller explicitly provided override"
    assert not strict_called["value"], "Strict start path should not run for invalid override"
