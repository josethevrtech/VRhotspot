import json

import pytest

from vr_hotspotd import cli as base_cli
from vr_hotspotd.devtools import adb_cli


class _FakeResponse:
    def __init__(self, data):
        self.status = 200
        self._raw = json.dumps(
            {
                "correlation_id": "test-cid",
                "result_code": "ok",
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


def test_tools_status_uses_existing_status_endpoint(monkeypatch, tmp_path, capsys):
    data = {"adb": {"source": "missing"}, "platform_tools_pin": {"version": "r37.0.0"}}

    result, seen = _run(monkeypatch, tmp_path, data, ["tools", "status"])

    assert result == 0
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/tools/status"
    assert seen["body"] is None
    assert json.loads(capsys.readouterr().out) == data


def test_tools_install_posts_license_acceptance_and_uses_install_timeout(
    monkeypatch,
    tmp_path,
    capsys,
):
    data = {"operation": "install", "success": True, "result_code": "ok"}

    result, seen = _run(
        monkeypatch,
        tmp_path,
        data,
        ["tools", "install", "--accept-license"],
    )

    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/tools/install"
    assert seen["body"] == {"license_accepted": True}
    assert seen["timeout"] == 180.0
    assert json.loads(capsys.readouterr().out) == data


def test_tools_install_requires_license_before_request(monkeypatch, tmp_path, capsys):
    def must_not_open(_request, *, timeout):
        raise AssertionError(f"request must not run with timeout {timeout}")

    monkeypatch.setattr(base_cli, "_open_preflight_request", must_not_open)

    with pytest.raises(SystemExit) as excinfo:
        adb_cli.main(
            [
                "tools",
                "install",
                *_missing_env_args(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "requires --accept-license" in captured.err
    assert captured.out == ""


def test_tools_remove_posts_empty_body(monkeypatch, tmp_path, capsys):
    data = {"operation": "remove", "success": True, "result_code": "ok"}

    result, seen = _run(monkeypatch, tmp_path, data, ["tools", "remove"])

    assert result == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:9900/v1/devbridge/tools/remove"
    assert seen["body"] == {}
    assert json.loads(capsys.readouterr().out) == data
