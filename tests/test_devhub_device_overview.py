import ast
from pathlib import Path
import shutil
import subprocess

import pytest

from vr_hotspotd.devtools.adb_operations import RESULT_INVALID_REQUEST, RESULT_OK
from vr_hotspotd.devtools.device_overview import collect_device_overview


ROOT = Path(__file__).resolve().parents[1]
OVERVIEW_PY = ROOT / "backend" / "vr_hotspotd" / "devtools" / "device_overview.py"
OVERVIEW_JS = ROOT / "assets" / "devhub_device_overview.js"
OVERVIEW_CSS = ROOT / "assets" / "devhub_device_overview.css"
LOADER = ROOT / "assets" / "field_visibility.js"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


TOOLS_STATUS = {"adb": {"path": "/managed/adb"}}


class OverviewRunner:
    def __init__(self, *, controller_service=True):
        self.controller_service = controller_service
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(tuple(argv))
        command = tuple(argv[4:])
        outputs = {
            ("getprop",): "\n".join(
                [
                    "[ro.product.manufacturer]: [Meta]",
                    "[ro.product.model]: [Quest 3S]",
                    "[ro.product.name]: [panther]",
                    "[ro.product.device]: [panther]",
                    "[ro.build.version.release]: [12]",
                    "[ro.build.version.sdk]: [32]",
                    "[ro.build.display.id]: [SQ3A.220605.009.A1]",
                    "[ro.build.version.security_patch]: [2026-07-01]",
                ]
            ),
            ("dumpsys", "battery"): "\n".join(
                [
                    "AC powered: false",
                    "USB powered: true",
                    "Wireless powered: false",
                    "status: 2",
                    "level: 73",
                    "scale: 100",
                    "voltage: 4120",
                    "temperature: 315",
                ]
            ),
            ("df", "-k", "/data"): "\n".join(
                [
                    "Filesystem 1K-blocks Used Available Use% Mounted on",
                    "/dev/block/dm-10 104857600 52428800 52428800 50% /data",
                ]
            ),
            ("cmd", "wifi", "status"): (
                "Wifi is enabled\n"
                "WifiInfo: SSID: \"Lab WiFi\", BSSID: aa:bb:cc:dd:ee:ff, "
                "RSSI: -51, Link speed: 1200Mbps, Frequency: 5180MHz"
            ),
            ("ip", "route"): (
                "default via 192.168.0.1 dev wlan0\n"
                "192.168.0.0/24 dev wlan0 src 192.168.0.98"
            ),
            ("cat", "/proc/uptime"): "93784.25 12000.00",
        }
        if command == ("dumpsys", "OVRRemoteService"):
            if not self.controller_service:
                output = "Can't find service: OVRRemoteService"
            else:
                output = "\n".join(
                    [
                        "Paired device: hidden, Type: Right, Firmware: 1.9.0, Battery: 60%, Status: Enabled, ExternalStatus: ENABLED, TrackingStatus: POSITION, BrightnessLevel: GOOD",
                        "Paired device: hidden, Type: Left, Firmware: 1.9.0, Battery: 80%, Status: Enabled, ExternalStatus: ENABLED, TrackingStatus: POSITION, BrightnessLevel: GOOD",
                    ]
                )
        else:
            output = outputs.get(command, "")
        return subprocess.CompletedProcess(argv, 0, stdout=output.encode(), stderr=b"")


def test_collect_device_overview_normalizes_headset_health_and_controller_data():
    runner = OverviewRunner()

    result = collect_device_overview(
        "192.168.0.98:5555",
        tools_status=TOOLS_STATUS,
        runner=runner,
    )

    assert result["result_code"] == RESULT_OK
    assert result["success"] is True
    data = result["data"]
    assert data["transport"] == "wireless"
    assert data["device"]["manufacturer"] == "Meta"
    assert data["device"]["model"] == "Quest 3S"
    assert data["device"]["android_release"] == "12"
    assert data["device"]["android_sdk"] == 32
    assert data["battery"] == {
        "percent": 73,
        "status": "charging",
        "charging": True,
        "power_sources": ["USB"],
        "temperature_c": 31.5,
        "voltage_mv": 4120,
    }
    assert data["storage"]["available_bytes"] == 52428800 * 1024
    assert data["wifi"]["ssid"] == "Lab WiFi"
    assert data["wifi"]["rssi_dbm"] == -51
    assert data["wifi"]["link_speed_mbps"] == 1200
    assert data["wifi"]["ip_address"] == "192.168.0.98"
    assert data["uptime_seconds"] == 93784
    assert data["controller_service_available"] is True
    assert [controller["side"] for controller in data["controllers"]] == [
        "Right",
        "Left",
    ]
    assert [controller["battery_percent"] for controller in data["controllers"]] == [
        60,
        80,
    ]
    assert all(call[0] == "/managed/adb" for call in runner.calls)
    assert all("sh" not in call and "bash" not in call for call in runner.calls)


def test_collect_device_overview_treats_missing_quest_controller_service_as_optional():
    result = collect_device_overview(
        "USB123",
        tools_status=TOOLS_STATUS,
        runner=OverviewRunner(controller_service=False),
    )

    assert result["result_code"] == RESULT_OK
    assert result["data"]["transport"] == "usb"
    assert result["data"]["controller_service_available"] is False
    assert result["data"]["controllers"] == []


def test_collect_device_overview_rejects_unstructured_serial_input():
    result = collect_device_overview(
        "device; reboot",
        tools_status=TOOLS_STATUS,
        runner=OverviewRunner(),
    )

    assert result["result_code"] == RESULT_INVALID_REQUEST
    assert result["success"] is False


def test_device_overview_route_and_assets_are_registered():
    api_source = DEVHUB_API.read_text(encoding="utf-8")
    loader_source = LOADER.read_text(encoding="utf-8")

    assert 'DEVICE_OVERVIEW_PATH = "/v1/devbridge/adb/device-overview"' in api_source
    assert "collect_device_overview" in api_source
    assert '"/assets/devhub_device_overview.css": "text/css; charset=utf-8"' in api_source
    assert (
        '"/assets/devhub_device_overview.js": "application/javascript; charset=utf-8"'
        in api_source
    )
    assert "'/assets/devhub_device_overview.css'" in loader_source
    assert "'/assets/devhub_device_overview.js'" in loader_source
    assert loader_source.index("'/assets/devhub_workspace.js'") < loader_source.index(
        "'/assets/devhub_device_overview.js'"
    )


def test_device_page_replaces_quick_actions_with_live_headset_information():
    source = OVERVIEW_JS.read_text(encoding="utf-8")

    assert "title.textContent = 'Headset Overview'" in source
    assert "body.replaceChildren(empty, content)" in source
    assert "Headset battery" in source
    assert "Storage" in source
    assert "Wi-Fi" in source
    assert "Network quality" in source
    assert "IP address" in source
    assert "System" in source
    assert "Build" in source
    assert "Uptime" in source
    assert "Controllers" in source
    assert "Quick Actions" in source  # source card lookup only
    assert "setInterval(() => void refreshOverview(false), REFRESH_MS)" in source


def test_device_overview_python_keeps_python39_grammar():
    ast.parse(OVERVIEW_PY.read_text(encoding="utf-8"), feature_version=(3, 9))


@pytest.mark.parametrize("asset", [OVERVIEW_JS, LOADER])
def test_device_overview_javascript_parses_with_node(asset):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    completed = subprocess.run(
        [node, "--check", str(asset)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_device_overview_styles_are_responsive_and_use_no_status_lights():
    source = OVERVIEW_CSS.read_text(encoding="utf-8")

    assert ".devhub-overview-metrics" in source
    assert ".devhub-controller-row" in source
    assert "@media (max-width: 720px)" in source
    assert "status-light" not in source
    assert "box-shadow: 0 0" not in source
