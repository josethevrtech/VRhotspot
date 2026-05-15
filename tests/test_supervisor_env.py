import os
import sys
from types import SimpleNamespace

import pytest


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


def _make_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _common_selection_patches(monkeypatch, supervisor, tmp_path, *, vendor_dnsmasq, sys_dnsmasq):
    vendor_hostapd = _make_exe(tmp_path / "vendor" / "hostapd")
    sys_hostapd = _make_exe(tmp_path / "system" / "hostapd")
    vendor_dnsmasq_path = None
    sys_dnsmasq_path = None
    if vendor_dnsmasq:
        vendor_dnsmasq_path = _make_exe(tmp_path / "vendor" / "dnsmasq")
    if sys_dnsmasq:
        sys_dnsmasq_path = _make_exe(tmp_path / "system" / "dnsmasq")

    monkeypatch.setattr(supervisor, "vendor_bin_dirs", lambda: [tmp_path / "vendor"])
    monkeypatch.setattr(
        supervisor,
        "resolve_vendor_required",
        lambda _names: (
            {"hostapd": vendor_hostapd, "dnsmasq": vendor_dnsmasq_path},
            tmp_path / "vendor-lib",
            None,
            [] if vendor_dnsmasq_path else ["dnsmasq"],
        ),
    )
    monkeypatch.setattr(supervisor, "vendor_lib_dirs", lambda preferred_profile=None: [tmp_path / "vendor-lib"])
    monkeypatch.setattr(
        supervisor,
        "_which_in_path",
        lambda exe, _path: {"hostapd": sys_hostapd, "dnsmasq": sys_dnsmasq_path}.get(exe),
    )
    monkeypatch.setattr(supervisor, "_hostapd_supports_ht_vht", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor.os_release, "read_os_release", lambda: {"id": "cachyos"})
    monkeypatch.delenv("VR_HOTSPOT_FORCE_VENDOR_BIN", raising=False)
    monkeypatch.delenv("VR_HOTSPOT_VENDOR_STRICT", raising=False)
    monkeypatch.delenv("VR_HOTSPOT_FORCE_SYSTEM_BIN", raising=False)
    supervisor._stderr_tail.clear()
    return vendor_hostapd, vendor_dnsmasq_path, sys_hostapd, sys_dnsmasq_path


def test_build_engine_env_selects_usable_vendor_dnsmasq(monkeypatch, tmp_path):
    import vr_hotspotd.engine.supervisor as supervisor

    _vendor_hostapd, vendor_dnsmasq, _sys_hostapd, _sys_dnsmasq = _common_selection_patches(
        monkeypatch,
        supervisor,
        tmp_path,
        vendor_dnsmasq=True,
        sys_dnsmasq=True,
    )
    monkeypatch.setattr(supervisor, "_probe_dnsmasq_executable", lambda *_args, **_kwargs: (True, None))

    env = supervisor._build_engine_env()

    assert env["DNSMASQ"] == vendor_dnsmasq
    shim_dnsmasq = os.path.join(env["PATH"].split(":")[0], "dnsmasq")
    assert os.path.realpath(shim_dnsmasq) == vendor_dnsmasq


def test_build_engine_env_rejects_vendor_dnsmasq_missing_shared_libs(monkeypatch, tmp_path):
    import vr_hotspotd.engine.supervisor as supervisor

    _vendor_hostapd, vendor_dnsmasq, _sys_hostapd, sys_dnsmasq = _common_selection_patches(
        monkeypatch,
        supervisor,
        tmp_path,
        vendor_dnsmasq=True,
        sys_dnsmasq=True,
    )

    def fake_run(cmd, **_kwargs):
        if cmd == [vendor_dnsmasq, "--version"]:
            return SimpleNamespace(
                returncode=127,
                stdout="",
                stderr=(
                    f"{vendor_dnsmasq}: error while loading shared libraries: "
                    "libhogweed.so.6: cannot open shared object file: No such file or directory\n"
                    "libnettle.so.8 => not found\n"
                ),
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    env = supervisor._build_engine_env()

    assert env["DNSMASQ"] == sys_dnsmasq
    shim_dnsmasq = os.path.join(env["PATH"].split(":")[0], "dnsmasq")
    assert os.path.realpath(shim_dnsmasq) == sys_dnsmasq
    notes = "\n".join(supervisor._stderr_tail)
    assert "vendor_dnsmasq_rejected" in notes
    assert "libhogweed.so.6" in notes
    assert f"dnsmasq_select={sys_dnsmasq}" in notes


def test_build_engine_env_errors_when_dnsmasq_missing_everywhere(monkeypatch, tmp_path):
    import vr_hotspotd.engine.supervisor as supervisor

    _common_selection_patches(
        monkeypatch,
        supervisor,
        tmp_path,
        vendor_dnsmasq=False,
        sys_dnsmasq=False,
    )

    with pytest.raises(supervisor.VendorSelectionError) as exc:
        supervisor._build_engine_env()

    payload = exc.value.to_payload()
    assert payload["error"] == "binary_missing"
    assert "dnsmasq" in payload["missing"]
    assert payload["selection"]["chosen_dnsmasq"] is None
