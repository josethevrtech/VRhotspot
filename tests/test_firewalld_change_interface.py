import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_firewalld_add_interface_uses_change_interface(monkeypatch):
    from vr_hotspotd.engine import firewalld

    calls = []

    monkeypatch.setattr(firewalld.shutil, "which", lambda name: "/usr/bin/firewall-cmd")

    def fake_run(cmd, stdout=None, stderr=None, text=None, check=None):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = "success"

        return Result()

    monkeypatch.setattr(firewalld.subprocess, "run", fake_run)

    ok, _out = firewalld.change_interface("trusted", "wlan1")

    assert ok is True
    assert calls == [["/usr/bin/firewall-cmd", "--zone", "trusted", "--change-interface", "wlan1"]]
