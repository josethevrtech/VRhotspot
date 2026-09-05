from types import SimpleNamespace

import pytest

from vr_hotspotd.engine import hostapd6_engine, hostapd_bridge_engine, hostapd_nat_engine, tx_power


@pytest.mark.parametrize(
    ("requested_dbm", "expected_setting"),
    [(None, ["auto"]), (20, ["fixed", "2000"]), (0, ["fixed", "0"]),
     (17.0, ["fixed", "1700"]), (30, ["fixed", "3000"])],
)
def test_tx_power_uses_iw_mbm_units_or_driver_auto(monkeypatch, requested_dbm, expected_setting):
    calls = []
    monkeypatch.setattr(tx_power, "_iw_bin", lambda: "/usr/sbin/iw")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tx_power.subprocess, "run", run)

    assert tx_power.set_tx_power("x0wlan1", requested_dbm) == (True, "ok")
    assert calls == [(
        ["/usr/sbin/iw", "dev", "x0wlan1", "set", "txpower"] + expected_setting,
        {"capture_output": True, "text": True, "timeout": 2.0},
    )]


@pytest.mark.parametrize("requested_dbm", [True, False, "20", "auto", float("nan"),
                                          float("inf"), float("-inf"), 17.25, -1, 31, 10 ** 1000, [], {}])
def test_invalid_power_never_runs_iw(monkeypatch, requested_dbm):
    def unexpected_lookup():
        pytest.fail("invalid transmit power must be rejected before invoking iw")

    monkeypatch.setattr(tx_power, "_iw_bin", unexpected_lookup)

    ok, reason = tx_power.set_tx_power("wlan1", requested_dbm)

    assert not ok
    assert reason.startswith("invalid_tx_power:")


@pytest.mark.parametrize("requested_dbm", [None, 20])
def test_tx_power_preserves_driver_rejection(monkeypatch, requested_dbm):
    monkeypatch.setattr(tx_power, "_iw_bin", lambda: "/usr/sbin/iw")
    monkeypatch.setattr(
        tx_power.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=161, stdout="", stderr="command failed: Operation not supported (-95)\n"
        ),
    )

    assert tx_power.set_tx_power("wlan1", requested_dbm) == (
        False, "command failed: Operation not supported (-95)"
    )


def test_tx_power_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(tx_power, "_iw_bin", lambda: "/usr/sbin/iw")

    def timeout(command, **_kwargs):
        raise tx_power.subprocess.TimeoutExpired(command, 2.0)

    monkeypatch.setattr(tx_power.subprocess, "run", timeout)

    ok, reason = tx_power.set_tx_power("wlan1", 20)

    assert not ok
    assert "timed out" in reason


def test_tx_power_missing_iw_is_reported(monkeypatch):
    monkeypatch.setattr(tx_power, "_iw_bin", lambda: None)
    assert tx_power.set_tx_power("wlan1", None) == (False, "iw_not_found")


@pytest.mark.parametrize(("returncode", "output", "expected"), [
    (0, "\ttxpower 20.00 dBm\n", 20),
    (0, "\ttxpower 0.17 dBm\n", 0.17),
    (1, "\ttxpower 20.00 dBm\n", None),
    (0, "\ttxpower nan dBm\n", None),
    (0, "\ttxpower inf dBm\n", None),
    (0, "", None),
])
def test_tx_power_readback_requires_success_and_finite_value(monkeypatch, returncode, output, expected):
    monkeypatch.setattr(tx_power, "_iw_bin", lambda: "/usr/sbin/iw")
    monkeypatch.setattr(tx_power.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=returncode, stdout=output))
    assert tx_power.get_tx_power("wlan1") == expected


@pytest.mark.parametrize("engine", ["nat", "bridge", "6ghz"])
@pytest.mark.parametrize("requested_dbm", [None, 20])
def test_hostapd_config_does_not_emit_unsupported_tx_power(tmp_path, engine, requested_dbm):
    config_path = tmp_path / "hostapd.conf"
    args = {
        "path": str(config_path), "ifname": "wlan1", "ssid": "VR-Hotspot",
        "passphrase": "password123", "country": "US", "tx_power": requested_dbm,
    }
    if engine == "6ghz":
        hostapd6_engine._write_hostapd_6ghz_conf(**args, channel=5)
    else:
        args.update(band="5ghz", channel=36, ap_security="wpa2", wifi6=True)
        if engine == "bridge":
            hostapd_bridge_engine._write_hostapd_conf(**args, bridge="vrbr0")
        else:
            hostapd_nat_engine._write_hostapd_conf(**args)

    lines = config_path.read_text(encoding="utf-8").splitlines()
    assert "interface=wlan1" in lines
    assert not any(line.startswith("tx_power=") for line in lines)
