import configparser
import importlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


APP_ID = "io.github.josethevrtech.VRhotspot"
APP_NAME = "VR Hotspot"
MANIFEST_PATH = Path("packaging/flatpak") / f"{APP_ID}.json"
DESKTOP_PATH = Path("packaging/flatpak") / f"{APP_ID}.desktop"
METAINFO_PATH = Path("packaging/flatpak") / f"{APP_ID}.metainfo.xml"
LAUNCHER_PATH = Path("packaging/flatpak/vrhotspot-flatpak")


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _smoke():
    return subprocess.run(
        [sys.executable, "-m", "flatpak_app", "--smoke-json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_app_shell_imports_without_importing_gtk(monkeypatch):
    monkeypatch.setitem(sys.modules, "gi", None)
    for module_name in tuple(sys.modules):
        if module_name == "flatpak_app" or module_name.startswith("flatpak_app."):
            del sys.modules[module_name]

    module = importlib.import_module("flatpak_app")

    assert module.APP_ID == APP_ID
    assert sys.modules.get("gi") is None


def test_smoke_json_exits_successfully_is_bounded_and_has_expected_sections():
    from flatpak_app import MAX_SMOKE_JSON_BYTES

    result = _smoke()
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stderr == ""
    assert len(result.stdout.encode("utf-8")) <= MAX_SMOKE_JSON_BYTES + 1
    assert set(payload) == {"application", "controls", "shell", "ui"}
    assert payload["application"] == {
        "id": APP_ID,
        "name": APP_NAME,
        "prototype": True,
    }
    assert payload["shell"] == {
        "graphical_shell": "gtk4_placeholder",
        "state": "offline_unpaired",
    }
    assert set(payload["ui"]) == {
        "mode",
        "show_technical_details",
        "daemon",
        "pairing",
        "adapters",
        "preflight",
        "support_bundle",
    }


def test_smoke_json_contains_no_secret_or_host_path_leak_markers():
    result = _smoke()
    rendered = result.stdout.lower()

    assert result.returncode == 0
    for forbidden in (
        "token",
        "passphrase",
        "password",
        "psk",
        "bearer",
        "file://",
        "/etc/",
        "/home/",
        "/run/",
        "/tmp/",
        "/var/",
    ):
        assert forbidden not in rendered


def test_shell_exposes_no_mutation_controls_or_actions():
    from flatpak_app import build_smoke_payload
    from flatpak_app import app

    payload = build_smoke_payload()
    public_methods = {
        name
        for name, value in inspect.getmembers(app, inspect.isfunction)
        if not name.startswith("_")
    }

    assert payload["controls"]["mutation_actions"] == []
    assert payload["controls"]["support_bundle_export_enabled"] is False
    assert payload["ui"]["support_bundle"]["action_enabled"] is False
    assert public_methods.isdisjoint(
        {
            "start",
            "stop",
            "restart",
            "repair",
            "save_config",
            "update_config",
            "request",
            "post",
            "export",
        }
    )


def test_app_shell_has_no_direct_host_secret_or_network_access():
    source = Path("flatpak_app/app.py").read_text(encoding="utf-8")

    for forbidden in (
        "import os",
        "import pathlib",
        "import socket",
        "import subprocess",
        "import urllib",
        "import requests",
        "os.environ",
        "LocalApiClient(",
        "TokenPairingController(",
        "/etc/",
        "/var/lib/",
        "systemctl",
        "nmcli",
        "hostapd",
        "dnsmasq",
        "firewall",
    ):
        assert forbidden not in source


def test_manifest_is_valid_json_and_matches_app_id_command_and_runtime():
    manifest = _manifest()

    assert manifest["app-id"] == APP_ID
    assert manifest["command"] == LAUNCHER_PATH.name
    assert manifest["runtime"] == "org.gnome.Platform"
    assert manifest["sdk"] == "org.gnome.Sdk"
    assert manifest["runtime-version"]


def test_manifest_has_only_minimal_display_and_loopback_client_permissions():
    finish_args = set(_manifest()["finish-args"])

    assert finish_args == {
        "--share=network",
        "--share=ipc",
        "--socket=wayland",
        "--socket=fallback-x11",
    }
    assert not any("filesystem=" in argument for argument in finish_args)
    assert not any("system-bus" in argument for argument in finish_args)
    assert not any("session-bus" in argument for argument in finish_args)
    assert not any("talk-name" in argument for argument in finish_args)
    assert "--filesystem=host" not in finish_args
    assert "--device=all" not in finish_args
    assert "--socket=system-bus" not in finish_args


def test_manifest_packages_only_shell_client_and_static_desktop_assets():
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    sources = _manifest()["modules"][0]["sources"]
    paths = {source["path"] for source in sources}

    assert all(source["type"] == "file" for source in sources)
    assert paths
    assert all(
        path.startswith("../../flatpak_app/")
        or path.startswith("../../flatpak_client/")
        or "/" not in path
        for path in paths
    )
    for forbidden in (
        "backend/",
        "backend/vendor",
        "vr_hotspotd",
        "install.sh",
        "uninstall.sh",
        "systemd",
    ):
        assert forbidden not in manifest_text


def test_desktop_file_matches_app_id_name_and_launcher():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    assert DESKTOP_PATH.exists()
    parser.read(DESKTOP_PATH, encoding="utf-8")
    entry = parser["Desktop Entry"]

    assert DESKTOP_PATH.stem == APP_ID
    assert entry["Type"] == "Application"
    assert entry["Name"] == APP_NAME
    assert entry["Exec"] == LAUNCHER_PATH.name
    assert entry["Icon"] == APP_ID
    assert entry["Terminal"] == "false"
    assert not any(key.startswith("Actions") for key in entry)


def test_metainfo_xml_parses_and_matches_app_and_desktop_ids():
    assert METAINFO_PATH.exists()
    root = ET.parse(METAINFO_PATH).getroot()

    assert root.tag == "component"
    assert root.attrib["type"] == "desktop-application"
    assert root.findtext("id") == APP_ID
    assert root.findtext("name") == APP_NAME
    launchable = root.find("launchable")
    assert launchable is not None
    assert launchable.attrib["type"] == "desktop-id"
    assert launchable.text == DESKTOP_PATH.name


def test_launcher_is_executable_static_and_safe():
    assert LAUNCHER_PATH.exists()
    assert LAUNCHER_PATH.stat().st_mode & 0o111
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert source.startswith("#!/bin/sh\n")
    assert "exec python3 -m flatpak_app" in source
    assert len(source.encode("utf-8")) < 512
    for forbidden in (
        "sudo",
        "pkexec",
        "curl",
        "wget",
        "systemctl",
        "dbus-send",
        "/etc/",
        "/var/lib/",
        "VR_HOTSPOTD_API_TOKEN",
        "backend/vendor",
    ):
        assert forbidden not in source
