import json

import pytest

from vr_hotspotd import cli


class _FakeResponse:
    def __init__(self, payload, *, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self):
        return self._raw


@pytest.fixture(autouse=True)
def clear_cli_environment(monkeypatch):
    for key in (*cli._ENV_KEYS, "VR_HOTSPOTD_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)


def _mocked_open(monkeypatch, data, seen):
    def open_request(request, *, timeout):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["timeout"] = timeout
        return _FakeResponse(
            {
                "correlation_id": "test-cid",
                "result_code": "ok",
                "warnings": [],
                "data": data,
            }
        )

    monkeypatch.setattr(cli, "_open_preflight_request", open_request)


def _missing_env_args(tmp_path):
    return ["--env-file", str(tmp_path / "missing-env")]


def _run(monkeypatch, tmp_path, data, argv):
    seen = {}
    _mocked_open(monkeypatch, data, seen)
    result = cli.main(
        [
            "devbridge",
            *argv,
            "--api-url",
            "http://127.0.0.1:9900",
            *_missing_env_args(tmp_path),
        ]
    )
    return result, seen


def test_devbridge_status_fetches_status_endpoint(monkeypatch, tmp_path, capsys):
    secret = "devbridge-status-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    data = {"schema_version": 1, "mode": "read_only"}

    result, seen = _run(monkeypatch, tmp_path, data, ["status"])

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == data
    assert captured.err == ""
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/status"
    assert seen["headers"]["x-api-token"] == secret
    assert seen["headers"]["x-correlation-id"].startswith("cli-devbridge-status-")
    assert secret not in captured.out


def test_devbridge_scan_fetches_devices_endpoint(monkeypatch, tmp_path, capsys):
    data = {"devices": [], "summary": {"devices_detected": 0}}

    result, seen = _run(monkeypatch, tmp_path, data, ["scan"])

    assert result == 0
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/devices"
    assert json.loads(capsys.readouterr().out) == data


def test_devbridge_scan_no_probe_disables_probing(monkeypatch, tmp_path, capsys):
    result, seen = _run(monkeypatch, tmp_path, {"devices": []}, ["scan", "--no-probe"])

    assert result == 0
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/devices?probe=0"


def test_devbridge_tools_status_fetches_tools_status_endpoint(
    monkeypatch, tmp_path, capsys
):
    secret = "devbridge-tools-status-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    data = {
        "schema_version": 1,
        "mode": "read_only",
        "platform_tools_pin": {"implementation_blocked": True},
        "adb": {"source": "missing"},
    }

    result, seen = _run(monkeypatch, tmp_path, data, ["tools", "status"])

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == data
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/tools/status"
    assert seen["headers"]["x-api-token"] == secret
    assert seen["headers"]["x-correlation-id"].startswith("cli-devbridge-tools-status-")
    assert secret not in captured.out


def test_devbridge_tools_requires_a_subcommand(monkeypatch, tmp_path, capsys):
    def must_not_open(_request, *, timeout):
        raise AssertionError("no request may be sent without a tools subcommand")

    monkeypatch.setattr(cli, "_open_preflight_request", must_not_open)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["devbridge", "tools", *_missing_env_args(tmp_path)])

    assert excinfo.value.code == 2


def test_devbridge_adb_command_targets_ip(monkeypatch, tmp_path, capsys):
    data = {"kind": "connect", "devices": []}

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        ["adb-command", "--ip", "192.168.68.23"],
    )

    assert result == 0
    assert seen["url"] == (
        "http://127.0.0.1:9900/v1/devbridge/adb?kind=connect&ip=192.168.68.23"
    )
    assert json.loads(capsys.readouterr().out) == data


def test_devbridge_logcat_command_requests_logcat_kind(monkeypatch, tmp_path, capsys):
    result, seen = _run(monkeypatch, tmp_path, {"kind": "logcat"}, ["logcat-command"])

    assert result == 0
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb?kind=logcat"


def test_devbridge_adb_command_rejects_invalid_ip(monkeypatch, tmp_path, capsys):
    def must_not_open(_request, *, timeout):
        raise AssertionError("no request may be sent for an invalid --ip")

    monkeypatch.setattr(cli, "_open_preflight_request", must_not_open)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "devbridge",
                "adb-command",
                "--ip",
                "not-an-ip",
                *_missing_env_args(tmp_path),
            ]
        )

    assert excinfo.value.code == 2
    assert "IPv4" in capsys.readouterr().err


def test_devbridge_status_writes_output_file(monkeypatch, tmp_path, capsys):
    data = {"schema_version": 1}
    seen = {}
    _mocked_open(monkeypatch, data, seen)
    output_path = tmp_path / "devbridge.json"

    result = cli.main(
        [
            "devbridge",
            "status",
            "--api-url",
            "http://127.0.0.1:9900",
            "--output",
            str(output_path),
            *_missing_env_args(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    assert "Wrote" in captured.err
    assert json.loads(output_path.read_text(encoding="utf-8")) == data


def test_devbridge_api_error_is_reported_concisely(monkeypatch, tmp_path, capsys):
    def open_request(request, *, timeout):
        return _FakeResponse(
            {"result_code": "unauthorized", "warnings": [], "data": {}},
            status=200,
        )

    monkeypatch.setattr(cli, "_open_preflight_request", open_request)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "devbridge",
                "status",
                "--api-url",
                "http://127.0.0.1:9900",
                *_missing_env_args(tmp_path),
            ]
        )

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "unauthorized" in captured.err
    assert captured.out == ""
