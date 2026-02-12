import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))


def test_build_engine_env_sets_unbuffered_python(monkeypatch):
    import vr_hotspotd.engine.supervisor as supervisor

    monkeypatch.setattr(supervisor, "vendor_bin_dirs", lambda: [])
    monkeypatch.setattr(
        supervisor,
        "resolve_vendor_required",
        lambda _names: (
            {"hostapd": None, "dnsmasq": None},
            None,
            None,
            {},
        ),
    )
    monkeypatch.setattr(supervisor, "vendor_lib_dirs", lambda preferred_profile=None: [])
    monkeypatch.setattr(
        supervisor,
        "_which_in_path",
        lambda exe, _path: f"/usr/sbin/{exe}" if exe in ("hostapd", "dnsmasq") else None,
    )
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    env = supervisor._build_engine_env()

    assert env.get("PYTHONUNBUFFERED") == "1"
    assert env.get("PYTHONIOENCODING") == "utf-8"
