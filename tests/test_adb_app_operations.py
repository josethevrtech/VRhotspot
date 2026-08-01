from __future__ import annotations

import subprocess

from vr_hotspotd.devtools.adb_operations import execute_adb_operation


TOOLS = {"adb": {"path": "/opt/vrhotspot/platform-tools/adb"}}
SERIAL = "192.168.68.24:5555"
PACKAGE = "com.example.xrtraining"


def _completed(argv, *, stdout=b"", stderr=b"", returncode=0, **kwargs):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_packages_lists_third_party_apps_by_default():
    observed = {}

    def runner(argv, **kwargs):
        observed["argv"] = argv
        observed["timeout"] = kwargs["timeout"]
        return _completed(
            argv,
            stdout=(
                b"package:com.example.xrtraining\n"
                b"package:com.vendor.utility\n"
                b"not-a-package-line\n"
            ),
        )

    result = execute_adb_operation(
        "packages",
        {"serial": SERIAL},
        tools_status=TOOLS,
        runner=runner,
    )

    assert result["success"] is True
    assert observed["argv"] == [
        "/opt/vrhotspot/platform-tools/adb",
        "-s",
        SERIAL,
        "shell",
        "pm",
        "list",
        "packages",
        "-3",
    ]
    assert observed["timeout"] == 60.0
    assert result["data"]["third_party_only"] is True
    assert result["data"]["packages"] == [
        "com.example.xrtraining",
        "com.vendor.utility",
    ]


def test_packages_can_include_system_apps():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, stdout=b"package:android\npackage:com.example.xrtraining\n")

    result = execute_adb_operation(
        "packages",
        {"serial": SERIAL, "third_party_only": False},
        tools_status=TOOLS,
        runner=runner,
    )

    assert result["success"] is True
    assert calls[0][-2:] == ["list", "packages"]
    assert "-3" not in calls[0]
    assert result["data"]["third_party_only"] is False


def test_install_builds_reinstall_command_and_uses_long_timeout(tmp_path):
    apk = tmp_path / "training.apk"
    apk.write_bytes(b"APK")
    observed = {}

    def runner(argv, **kwargs):
        observed["argv"] = argv
        observed["timeout"] = kwargs["timeout"]
        return _completed(argv, stdout=b"Success\n")

    result = execute_adb_operation(
        "install",
        {"serial": SERIAL, "apk_path": str(apk)},
        tools_status=TOOLS,
        runner=runner,
    )

    assert result["success"] is True
    assert observed["argv"] == [
        "/opt/vrhotspot/platform-tools/adb",
        "-s",
        SERIAL,
        "install",
        "-r",
        str(apk),
    ]
    assert observed["timeout"] == 300.0
    assert result["data"]["apk_path"] == str(apk)
    assert result["data"]["reinstall"] is True
    assert result["data"]["grant_permissions"] is False


def test_install_supports_fresh_install_and_runtime_permission_grants(tmp_path):
    apk = tmp_path / "training.apk"
    apk.write_bytes(b"APK")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, stdout=b"Success\n")

    result = execute_adb_operation(
        "install",
        {
            "serial": SERIAL,
            "apk_path": str(apk),
            "reinstall": False,
            "grant_permissions": True,
        },
        tools_status=TOOLS,
        runner=runner,
    )

    assert result["success"] is True
    assert calls[0] == [
        "/opt/vrhotspot/platform-tools/adb",
        "-s",
        SERIAL,
        "install",
        "-g",
        str(apk),
    ]


def test_launch_stop_clear_and_uninstall_build_typed_commands():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return _completed(argv, stdout=b"Success\n")

    launch = execute_adb_operation(
        "launch",
        {"serial": SERIAL, "package": PACKAGE},
        tools_status=TOOLS,
        runner=runner,
    )
    stop = execute_adb_operation(
        "stop",
        {"serial": SERIAL, "package": PACKAGE},
        tools_status=TOOLS,
        runner=runner,
    )
    clear = execute_adb_operation(
        "clear_data",
        {"serial": SERIAL, "package": PACKAGE},
        tools_status=TOOLS,
        runner=runner,
    )
    uninstall = execute_adb_operation(
        "uninstall",
        {"serial": SERIAL, "package": PACKAGE, "keep_data": True},
        tools_status=TOOLS,
        runner=runner,
    )

    assert all(result["success"] is True for result in (launch, stop, clear, uninstall))
    assert calls == [
        [
            "/opt/vrhotspot/platform-tools/adb",
            "-s",
            SERIAL,
            "shell",
            "monkey",
            "-p",
            PACKAGE,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        [
            "/opt/vrhotspot/platform-tools/adb",
            "-s",
            SERIAL,
            "shell",
            "am",
            "force-stop",
            PACKAGE,
        ],
        [
            "/opt/vrhotspot/platform-tools/adb",
            "-s",
            SERIAL,
            "shell",
            "pm",
            "clear",
            PACKAGE,
        ],
        [
            "/opt/vrhotspot/platform-tools/adb",
            "-s",
            SERIAL,
            "uninstall",
            "-k",
            PACKAGE,
        ],
    ]


def test_invalid_app_requests_are_rejected_before_execution(tmp_path):
    called = False

    def runner(argv, **kwargs):
        nonlocal called
        called = True
        return _completed(argv)

    relative_apk = execute_adb_operation(
        "install",
        {"serial": SERIAL, "apk_path": "relative.apk"},
        tools_status=TOOLS,
        runner=runner,
    )
    missing_apk = execute_adb_operation(
        "install",
        {"serial": SERIAL, "apk_path": str(tmp_path / "missing.apk")},
        tools_status=TOOLS,
        runner=runner,
    )
    bad_package = execute_adb_operation(
        "launch",
        {"serial": SERIAL, "package": "not a package"},
        tools_status=TOOLS,
        runner=runner,
    )
    bad_boolean = execute_adb_operation(
        "uninstall",
        {"serial": SERIAL, "package": PACKAGE, "keep_data": "yes"},
        tools_status=TOOLS,
        runner=runner,
    )

    assert called is False
    assert relative_apk["result_code"] == "invalid_request"
    assert missing_apk["result_code"] == "invalid_request"
    assert bad_package["result_code"] == "invalid_request"
    assert bad_boolean["result_code"] == "invalid_request"


def test_apk_symlink_is_rejected(tmp_path):
    target = tmp_path / "target.apk"
    target.write_bytes(b"APK")
    symlink = tmp_path / "link.apk"
    symlink.symlink_to(target)

    result = execute_adb_operation(
        "install",
        {"serial": SERIAL, "apk_path": str(symlink)},
        tools_status=TOOLS,
        runner=lambda argv, **kwargs: _completed(argv),
    )

    assert result["success"] is False
    assert result["result_code"] == "invalid_request"
    assert "non-symlink" in result["stderr"]
