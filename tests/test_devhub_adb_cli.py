import io
import json

import pytest

from vr_hotspotd import cli as base_cli
from vr_hotspotd.devtools import adb_cli


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


def test_version_uses_authenticated_get(monkeypatch, tmp_path, capsys):
    secret = "adb-version-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    data = {"operation": "version", "success": True, "stdout": "Android Debug Bridge"}

    result, seen = _run(monkeypatch, tmp_path, data, ["version"])

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == data
    assert captured.err == ""
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/version"
    assert seen["body"] is None
    assert seen["headers"]["x-api-token"] == secret
    assert seen["headers"]["x-correlation-id"].startswith("cli-devhub-adb-version-")
    assert secret not in captured.out


def test_devices_uses_devices_endpoint(monkeypatch, tmp_path, capsys):
    data = {"operation": "devices", "success": True, "data": {"devices": []}}

    result, seen = _run(monkeypatch, tmp_path, data, ["devices"])

    assert result == 0
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/devices"
    assert json.loads(capsys.readouterr().out) == data


def test_connect_sends_structured_post(monkeypatch, tmp_path, capsys):
    data = {"operation": "connect", "success": True, "data": {"target": "192.168.68.23:5555"}}

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        ["connect", "--ip", "192.168.68.23"],
    )

    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/connect"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["body"] == {"ip": "192.168.68.23", "port": 5555}
    assert json.loads(capsys.readouterr().out) == data


def test_connect_accepts_explicit_port(monkeypatch, tmp_path, capsys):
    result, seen = _run(
        monkeypatch,
        tmp_path,
        {"operation": "connect", "success": True},
        ["connect", "--ip", "192.168.68.23", "--port", "42117"],
    )

    assert result == 0
    assert seen["body"] == {"ip": "192.168.68.23", "port": 42117}
    capsys.readouterr()


def test_disconnect_sends_exact_serial(monkeypatch, tmp_path, capsys):
    serial = "192.168.68.23:5555"

    result, seen = _run(
        monkeypatch,
        tmp_path,
        {"operation": "disconnect", "success": True},
        ["disconnect", "--serial", serial],
    )

    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/disconnect"
    assert seen["body"] == {"serial": serial}
    capsys.readouterr()


def test_pair_reads_code_from_stdin_and_never_returns_it(
    monkeypatch,
    tmp_path,
    capsys,
):
    pairing_code = "123456"
    secret = "pair-api-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    monkeypatch.setattr(adb_cli.sys, "stdin", io.StringIO(f"{pairing_code}\n"))
    data = {
        "operation": "pair",
        "success": True,
        "data": {"target": "192.168.68.23:37143"},
    }

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        ["pair", "--ip", "192.168.68.23", "--port", "37143"],
    )

    captured = capsys.readouterr()
    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/adb/pair"
    assert seen["body"] == {
        "ip": "192.168.68.23",
        "port": 37143,
        "pairing_code": pairing_code,
    }
    assert pairing_code not in seen["url"]
    assert pairing_code not in json.dumps(seen["headers"])
    assert pairing_code not in captured.out
    assert pairing_code not in captured.err
    assert secret not in captured.out
    assert json.loads(captured.out) == data


def test_pair_uses_hidden_tty_prompt(monkeypatch, tmp_path, capsys):
    pairing_code = "654321"

    class InteractiveInput:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(adb_cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr(
        adb_cli.getpass,
        "getpass",
        lambda prompt, *, stream: pairing_code,
    )

    result, seen = _run(
        monkeypatch,
        tmp_path,
        {"operation": "pair", "success": True},
        ["pair", "--ip", "192.168.68.23", "--port", "37143"],
    )

    captured = capsys.readouterr()
    assert result == 0
    assert seen["body"]["pairing_code"] == pairing_code
    assert pairing_code not in captured.out
    assert pairing_code not in captured.err


def test_pair_rejects_api_token_stdin_without_reading_or_requesting(
    monkeypatch,
    tmp_path,
    capsys,
):
    def must_not_open(_request, *, timeout):
        raise AssertionError(f"request must not run with timeout {timeout}")

    monkeypatch.setattr(base_cli, "_open_preflight_request", must_not_open)
    monkeypatch.setattr(adb_cli.sys, "stdin", io.StringIO("123456\n"))

    with pytest.raises(SystemExit) as excinfo:
        adb_cli.main(
            [
                "pair",
                "--ip",
                "192.168.68.23",
                "--port",
                "37143",
                "--token-stdin",
                *_missing_env_args(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "stdin is reserved" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("code", ("", "12345", "1234567", "12a456"))
def test_pair_rejects_invalid_code_before_request(
    monkeypatch,
    tmp_path,
    capsys,
    code,
):
    def must_not_open(_request, *, timeout):
        raise AssertionError(f"request must not run with timeout {timeout}")

    monkeypatch.setattr(base_cli, "_open_preflight_request", must_not_open)
    monkeypatch.setattr(adb_cli.sys, "stdin", io.StringIO(f"{code}\n"))

    with pytest.raises(SystemExit) as excinfo:
        adb_cli.main(
            [
                "pair",
                "--ip",
                "192.168.68.23",
                "--port",
                "37143",
                *_missing_env_args(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "exactly six digits" in captured.err
    if code:
        assert code not in captured.out
        assert code not in captured.err


def test_pair_refuses_reflected_pairing_code(monkeypatch, tmp_path, capsys):
    pairing_code = "123456"
    monkeypatch.setattr(adb_cli.sys, "stdin", io.StringIO(f"{pairing_code}\n"))
    seen = {}
    _mocked_open(
        monkeypatch,
        {"operation": "pair", "success": True, "stdout": pairing_code},
        seen,
    )

    with pytest.raises(SystemExit) as excinfo:
        adb_cli.main(
            [
                "pair",
                "--ip",
                "192.168.68.23",
                "--port",
                "37143",
                "--api-url",
                "http://127.0.0.1:9900",
                *_missing_env_args(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "sensitive request value" in captured.err
    assert pairing_code not in captured.out
    assert pairing_code not in captured.err


@pytest.mark.parametrize(
    "argv",
    (
        ["connect", "--ip", "not-an-ip"],
        ["connect", "--ip", "192.168.68.23", "--port", "0"],
        ["pair", "--ip", "192.168.68.23", "--port", "70000"],
        ["disconnect", "--serial", "bad serial with spaces"],
    ),
)
def test_invalid_arguments_are_rejected_before_request(
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


def test_result_can_be_exported_to_private_file(monkeypatch, tmp_path, capsys):
    seen = {}
    data = {"operation": "devices", "success": True, "data": {"devices": []}}
    _mocked_open(monkeypatch, data, seen)
    output = tmp_path / "adb-devices.json"

    result = adb_cli.main(
        [
            "devices",
            "--api-url",
            "http://127.0.0.1:9900",
            "--output",
            str(output),
            *_missing_env_args(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    assert "Wrote an ADB device result" in captured.err
    assert json.loads(output.read_text(encoding="utf-8")) == data
