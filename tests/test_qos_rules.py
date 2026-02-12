import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_iptables_add_unique_places_action_after_table(monkeypatch):
    import vr_hotspotd.qos as qos

    rule = [
        "-t",
        "mangle",
        "POSTROUTING",
        "-o",
        "wlan0",
        "-j",
        "DSCP",
        "--set-dscp-class",
        "CS5",
    ]

    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        if "-C" in cmd:
            return False, "not found"
        return True, ""

    monkeypatch.setattr(qos, "_iptables_path", lambda: "/usr/sbin/iptables")
    monkeypatch.setattr(qos, "_run", fake_run)

    ok, _out = qos._iptables_add_unique(rule)
    assert ok is True
    assert calls[0][:5] == ["/usr/sbin/iptables", "-t", "mangle", "-C", "POSTROUTING"]
    assert calls[1][:5] == ["/usr/sbin/iptables", "-t", "mangle", "-A", "POSTROUTING"]


def test_iptables_del_places_action_after_table(monkeypatch):
    import vr_hotspotd.qos as qos

    rule = [
        "-t",
        "mangle",
        "POSTROUTING",
        "-o",
        "wlan0",
        "-j",
        "DSCP",
        "--set-dscp-class",
        "CS5",
    ]

    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(qos, "_iptables_path", lambda: "/usr/sbin/iptables")
    monkeypatch.setattr(qos, "_run", fake_run)

    qos._iptables_del(rule)
    assert calls[0][:5] == ["/usr/sbin/iptables", "-t", "mangle", "-D", "POSTROUTING"]

