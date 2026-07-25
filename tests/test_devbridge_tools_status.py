"""Read-only Dev Bridge tools status: pin loading, discovery, and safety rails."""

import json
import os
import socket
import subprocess
import urllib.request

import pytest

from vr_hotspotd.devtools import platform_tools
from vr_hotspotd.devtools.platform_tools import (
    ADB_SOURCE_MANAGED,
    ADB_SOURCE_MISSING,
    ADB_SOURCE_SYSTEM,
    BLOCKED_REASON_PIN_FLAG,
    BLOCKED_REASON_PIN_UNAVAILABLE,
    BLOCKED_REASON_PLACEHOLDER_SHA256,
    BLOCKED_SHA256_PLACEHOLDER,
    MANAGED_ADB_PATH,
    PinError,
    build_devbridge_tools_status,
    collect_devbridge_tools_status,
    default_pin_path,
    discover_adb,
    load_platform_tools_pin,
    normalize_arch,
    pin_blocked_reason,
    pin_is_blocked,
)


VALID_PIN = {
    "schema_version": 1,
    "version": "r36.0.0",
    "url": "https://dl.google.com/android/repository/platform-tools_r36.0.0-linux.zip",
    "archive_sha256": "a" * 64,
    "implementation_blocked": False,
    "max_archive_bytes": 100000000,
    "arch": "x86_64",
    "extract_allowlist": ["platform-tools/NOTICE.txt", "platform-tools/adb"],
}


def _write_pin(tmp_path, pin):
    path = tmp_path / "platform_tools_pin.json"
    path.write_text(json.dumps(pin), encoding="utf-8")
    return path


# --- pin loading ---


def test_packaged_pin_file_exists_next_to_module():
    assert default_pin_path().is_file()


def test_repo_pin_loads_and_reports_blocked_placeholder():
    pin = load_platform_tools_pin()

    assert pin["schema_version"] == 1
    assert pin["archive_sha256"] == BLOCKED_SHA256_PLACEHOLDER
    assert pin["implementation_blocked"] is True
    assert pin_is_blocked(pin) is True
    assert pin_blocked_reason(pin) == BLOCKED_REASON_PLACEHOLDER_SHA256


def test_load_valid_pin(tmp_path):
    path = _write_pin(tmp_path, VALID_PIN)

    pin = load_platform_tools_pin(path)

    assert pin["version"] == "r36.0.0"
    assert pin_is_blocked(pin) is False
    assert pin_blocked_reason(pin) is None


def test_load_missing_pin_raises(tmp_path):
    with pytest.raises(PinError):
        load_platform_tools_pin(tmp_path / "missing.json")


def test_load_unparseable_pin_raises(tmp_path):
    path = tmp_path / "platform_tools_pin.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PinError):
        load_platform_tools_pin(path)


def test_load_non_object_pin_raises(tmp_path):
    path = tmp_path / "platform_tools_pin.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(PinError):
        load_platform_tools_pin(path)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"version": ""},
        {"url": "http://dl.google.com/android/repository/platform-tools_r36.0.0-linux.zip"},
        {"url": "https://evil.example.com/platform-tools.zip"},
        {"archive_sha256": "not-a-hash"},
        {"archive_sha256": "A" * 64},
        {"implementation_blocked": "yes"},
        {"arch": ""},
        {"extract_allowlist": []},
        {"extract_allowlist": "platform-tools/adb"},
    ],
)
def test_load_invalid_pin_raises(tmp_path, mutation):
    pin = dict(VALID_PIN)
    pin.update(mutation)
    path = _write_pin(tmp_path, pin)

    with pytest.raises(PinError):
        load_platform_tools_pin(path)


def test_placeholder_hash_blocks_even_if_flag_is_false():
    pin = dict(VALID_PIN)
    pin["archive_sha256"] = BLOCKED_SHA256_PLACEHOLDER
    pin["implementation_blocked"] = False

    assert pin_is_blocked(pin) is True
    assert pin_blocked_reason(pin) == BLOCKED_REASON_PLACEHOLDER_SHA256


def test_flag_alone_blocks_with_pin_flag_reason():
    pin = dict(VALID_PIN)
    pin["implementation_blocked"] = True

    assert pin_is_blocked(pin) is True
    assert pin_blocked_reason(pin) == BLOCKED_REASON_PIN_FLAG


# --- adb discovery ---


def _managed_adb(tmp_path, *, executable=True):
    managed = tmp_path / "platform-tools" / "adb"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"#!/bin/false\n")
    if executable:
        managed.chmod(0o755)
    return managed


def test_discovery_prefers_managed_adb(tmp_path):
    managed = _managed_adb(tmp_path)

    discovery = discover_adb(
        managed_path=str(managed), which=lambda _name: "/usr/bin/adb"
    )

    assert discovery["managed"]["present"] is True
    assert discovery["managed"]["installed"] is True
    assert discovery["managed"]["verified"] is None
    assert discovery["system"] == {"present": True, "path": "/usr/bin/adb"}
    assert discovery["source"] == ADB_SOURCE_MANAGED
    assert discovery["path"] == str(managed)


def test_discovery_falls_back_to_system_adb(tmp_path):
    discovery = discover_adb(
        managed_path=str(tmp_path / "platform-tools" / "adb"),
        which=lambda _name: "/usr/bin/adb",
    )

    assert discovery["managed"]["present"] is False
    assert discovery["managed"]["installed"] is False
    assert discovery["source"] == ADB_SOURCE_SYSTEM
    assert discovery["path"] == "/usr/bin/adb"


def test_discovery_reports_missing(tmp_path):
    discovery = discover_adb(
        managed_path=str(tmp_path / "platform-tools" / "adb"),
        which=lambda _name: None,
    )

    assert discovery["source"] == ADB_SOURCE_MISSING
    assert discovery["path"] is None
    assert discovery["system"] == {"present": False, "path": None}


def test_discovery_rejects_non_executable_managed_adb(tmp_path):
    managed = _managed_adb(tmp_path, executable=False)

    discovery = discover_adb(managed_path=str(managed), which=lambda _name: None)

    assert discovery["managed"]["present"] is True
    assert discovery["managed"]["installed"] is False
    assert discovery["source"] == ADB_SOURCE_MISSING


def test_discovery_rejects_symlinked_managed_adb(tmp_path):
    real = tmp_path / "real-adb"
    real.write_bytes(b"#!/bin/false\n")
    real.chmod(0o755)
    link = tmp_path / "adb"
    link.symlink_to(real)

    discovery = discover_adb(managed_path=str(link), which=lambda _name: None)

    assert discovery["managed"]["present"] is True
    assert discovery["managed"]["installed"] is False
    assert discovery["source"] == ADB_SOURCE_MISSING


def test_default_managed_path_is_the_planned_location():
    assert MANAGED_ADB_PATH == "/var/lib/vr-hotspot/devtools/platform-tools/adb"


# --- status model ---


def _status(**overrides):
    kwargs = {
        "pin": load_platform_tools_pin(),
        "pin_error": None,
        "discovery": discover_adb(
            managed_path=str(MANAGED_ADB_PATH), which=lambda _name: None
        ),
        "host_machine": "x86_64",
    }
    kwargs.update(overrides)
    return build_devbridge_tools_status(**kwargs)


def test_status_model_reports_pin_and_blocked_reason():
    status = _status()

    assert status["schema_version"] == 1
    assert status["mode"] == "read_only"
    pin_view = status["platform_tools_pin"]
    assert pin_view["available"] is True
    assert pin_view["version"] == "r36.0.0"
    assert pin_view["url_host"] == "dl.google.com"
    assert pin_view["implementation_blocked"] is True
    assert pin_view["blocked_reason"] == BLOCKED_REASON_PLACEHOLDER_SHA256
    assert status["host"]["arch_supported"] is True
    assert "unsupported_arch" not in status["warnings"]


def test_status_model_never_contains_full_pin_url():
    pin = load_platform_tools_pin()
    rendered = json.dumps(_status())

    assert pin["url"] not in rendered
    assert "https://dl.google.com" not in rendered


def test_status_model_flags_unsupported_arch():
    status = _status(host_machine="aarch64")

    assert status["host"]["arch"] == "aarch64"
    assert status["host"]["arch_supported"] is False
    assert "unsupported_arch" in status["warnings"]


def test_status_model_normalizes_amd64():
    status = _status(host_machine="amd64")

    assert status["host"]["arch"] == "x86_64"
    assert status["host"]["arch_supported"] is True


def test_status_model_fails_safe_without_pin():
    status = _status(pin=None, pin_error="cannot read pin")

    pin_view = status["platform_tools_pin"]
    assert pin_view["available"] is False
    assert pin_view["implementation_blocked"] is True
    assert pin_view["blocked_reason"] == BLOCKED_REASON_PIN_UNAVAILABLE
    assert pin_view["error"] == "cannot read pin"
    assert status["host"]["arch_supported"] is None
    assert "platform_tools_pin_unavailable" in status["warnings"]


def test_normalize_arch():
    assert normalize_arch("x86_64") == "x86_64"
    assert normalize_arch("AMD64") == "x86_64"
    assert normalize_arch("arm64") == "aarch64"
    assert normalize_arch(None) is None
    assert normalize_arch("") is None


# --- collector safety rails ---


def _forbid_network(monkeypatch):
    def _no_network(*_args, **_kwargs):
        raise AssertionError("tools status must never touch the network")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)


def _forbid_subprocesses(monkeypatch):
    def _no_exec(*_args, **_kwargs):
        raise AssertionError("tools status must never execute commands")

    for name in ("run", "check_output", "check_call", "call", "Popen"):
        monkeypatch.setattr(subprocess, name, _no_exec)
    for name in ("system", "popen"):
        monkeypatch.setattr(os, name, _no_exec)


def test_collect_makes_no_network_calls_and_executes_nothing(monkeypatch):
    _forbid_network(monkeypatch)
    _forbid_subprocesses(monkeypatch)

    status = collect_devbridge_tools_status()

    assert status["schema_version"] == 1
    assert status["platform_tools_pin"]["available"] is True
    assert status["platform_tools_pin"]["implementation_blocked"] is True
    assert status["adb"]["source"] in (
        ADB_SOURCE_MANAGED,
        ADB_SOURCE_SYSTEM,
        ADB_SOURCE_MISSING,
    )


def test_collect_never_raises_when_pin_is_broken(monkeypatch):
    monkeypatch.setattr(
        platform_tools,
        "load_platform_tools_pin",
        lambda path=None: (_ for _ in ()).throw(PinError("broken pin")),
    )

    status = collect_devbridge_tools_status()

    assert status["platform_tools_pin"]["available"] is False
    assert status["platform_tools_pin"]["implementation_blocked"] is True
    assert status["platform_tools_pin"]["error"] == "broken pin"


def test_collect_never_raises_when_discovery_is_broken(monkeypatch):
    monkeypatch.setattr(
        platform_tools,
        "discover_adb",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("filesystem exploded")),
    )

    status = collect_devbridge_tools_status()

    assert status["adb"]["source"] == ADB_SOURCE_MISSING
    assert status["adb"]["managed"]["installed"] is False
