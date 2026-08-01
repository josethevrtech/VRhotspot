import json

import pytest

from vr_hotspotd import cli as base_cli
from vr_hotspotd.devtools import adb_cli


SERIAL = "192.168.68.24:5555"
PACKAGE = "com.example.xrtraining"


class _FakeResponse:
    def __init__(self, data, *, status=200, result_code="ok"):
        self.status = status
        self._raw = json.dumps(
            {
                "correlation_id": "test-cid",
                "result_code": result_code,
                "warnings": [],
                "data": data,
            }
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self):
        return self._raw


@pytest.fixture(autouse=True)
def clear_cli_environment(monkeypatch):
    for key in (*base_cli._ENV_KEYS, "VR_HOTSPOTD_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)


def _missing_env_args(tmp_path):
    return ["--env-file", str(tmp_path / "missing-env")]


def _mocked_open(monkeypatch, response_data, seen):
    def open_request(request, *, timeout):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["timeout"] = timeout
        seen["body"] = (
            None if request.data is None else json.loads(request.data.decode("utf-8"))
        )
        return _FakeResponse(response_data)

    monkeypatch.setattr(base_cli, "_open_preflight_request", open_request)


def _run(monkeypatch, tmp_path, response_data, argv):
    seen = {}
    _mocked_open(monkeypatch, response_data, seen)
    result = adb_cli.main(
        [
            *argv,
            "--api-url",
            "http://127.0.0.1:9900",
            *_missing_env_args(tmp_path),
        ]
    )
    return result, seen


def test_packages_targets_selected_device(monkeypatch, tmp_path, capsys):
    data = {
        "operation": "packages",
        "success": True,
        "data": {"packages": [PACKAGE]},
    }

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        ["packages", "--serial", SERIAL],
    )

    assert result == 0
    assert seen["method"] == "GET"
    assert seen["url"] == (
        "http://127.0.0.1:9900/v1/devbridge/adb/packages?"
        "serial=192.168.68.24%3A5555&third_party_only=1"
    )
    assert seen["body"] is None
    assert json.loads(capsys.readouterr().out) == data


def test_packages_all_includes_system_packages(monkeypatch, tmp_path, capsys):
    result, seen = _run(
        monkeypatch,
        tmp_path,
        {"operation": "packages", "success": True},
        ["packages", "--serial", SERIAL, "--all"],
    )

    assert result == 0
    assert seen["url"].endswith("third_party_only=0")
    capsys.readouterr()


def test_install_sends_apk_workflow_and_uses_long_http_timeout(
    monkeypatch,
    tmp_path,
    capsys,
):
    apk = tmp_path / "training.apk"
    apk.write_bytes(b"APK")
    data = {"operation": "install", "success": True}

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        [
            "install",
            "--serial",
            SERIAL,
            "--apk",
            str(apk),
            "--grant-permissions",
        ],
    )

    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/install"
    assert seen["body"] == {
        "serial": SERIAL,
        "apk_path": str(apk),
        "reinstall": True,
        "grant_permissions": True,
    }
    assert seen["timeout"] == 300.0
    assert json.loads(capsys.readouterr().out) == data


def test_install_can_disable_reinstall(monkeypatch, tmp_path, capsys):
    apk = tmp_path / "training.apk"
    apk.write_bytes(b"APK")

    result, seen = _run(
        monkeypatch,
        tmp_path,
        {"operation": "install", "success": True},
        [
            "install",
            "--serial",
            SERIAL,
            "--apk",
            str(apk),
            "--no-reinstall",
        ],
    )

    assert result == 0
    assert seen["body"]["reinstall"] is False
    assert seen["body"]["grant_permissions"] is False
    capsys.readouterr()


def test_launch_stop_clear_and_uninstall_send_structured_requests(
    monkeypatch,
    tmp_path,
    capsys,
):
    cases = (
        (
            ["launch", "--serial", SERIAL, "--package", PACKAGE],
            "/v1/devbridge/adb/launch",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            ["stop", "--serial", SERIAL, "--package", PACKAGE],
            "/v1/devbridge/adb/stop",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            ["clear-data", "--serial", SERIAL, "--package", PACKAGE],
            "/v1/devbridge/adb/clear-data",
            {"serial": SERIAL, "package": PACKAGE},
        ),
        (
            ["uninstall", "--serial", SERIAL, "--package", PACKAGE, "--keep-data"],
            "/v1/devbridge/adb/uninstall",
            {"serial": SERIAL, "package": PACKAGE, "keep_data": True},
        ),
    )

    for argv, path, body in cases:
        result, seen = _run(
            monkeypatch,
            tmp_path,
            {"operation": argv[0], "success": True},
            argv,
        )
        assert result == 0
        assert seen["method"] == "POST"
        assert seen["url"] == "http://127.0.0.1:9900" + path
        assert seen["body"] == body
        capsys.readouterr()


@pytest.mark.parametrize(
    "argv",
    (
        ["packages", "--serial", "bad serial"],
        ["install", "--serial", SERIAL, "--apk", "relative.apk"],
        ["launch", "--serial", SERIAL, "--package", "bad package"],
        ["uninstall", "--serial", SERIAL, "--package", "com..broken"],
    ),
)
def test_invalid_app_arguments_are_rejected_before_request(
    monkeypatch,
    tmp_path,
    capsys,
    argv,
):
    def must_not_open(_request, *, timeout):
        raise AssertionError(f"request must not run with timeout {timeout}")

    monkeypatch.setattr(base_cli, "_open_preflight_request", must_not_open)

    with pytest.raises(SystemExit) as excinfo:
        adb_cli.main([*argv, *_missing_env_args(tmp_path)])

    assert excinfo.value.code == 2
    assert capsys.readouterr().out == ""
