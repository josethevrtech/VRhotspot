import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

from vr_hotspotd import api
from vr_hotspotd import autostart
from vr_hotspotd.autostart import (
    AUTOSTART_ROLLBACK_FAILED,
    AUTOSTART_UNIT,
    AutostartControlError,
    set_hotspot_autostart,
)


def _handler(body):
    raw = json.dumps(body).encode("utf-8")
    handler = api.APIHandler.__new__(api.APIHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    headers = Message()
    headers["Content-Length"] = str(len(raw))
    headers["X-Api-Token"] = "configured-token"
    handler.headers = headers
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "POST /v1/autostart HTTP/1.1"
    handler.path = "/v1/autostart"
    handler._last_code = None
    handler.send_response = lambda code, _message=None: setattr(
        handler, "_last_code", code
    )
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    return handler


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


@pytest.mark.parametrize(
    ("enabled", "expected_command"),
    (
        (True, ("systemctl", "enable", AUTOSTART_UNIT)),
        (False, ("systemctl", "disable", "--now", AUTOSTART_UNIT)),
    ),
)
def test_canonical_autostart_sync_uses_existing_unit_and_config(
    enabled,
    expected_command,
):
    commands = []
    writes = []

    result = set_hotspot_autostart(
        enabled,
        runner=lambda command: commands.append(tuple(command))
        or SimpleNamespace(returncode=0),
        config_loader=lambda: {"autostart": not enabled},
        config_writer=lambda updates: writes.append(dict(updates)) or updates,
    )

    assert result is enabled
    assert commands == [expected_command]
    assert writes == [{"autostart": enabled}]


def test_systemctl_child_never_inherits_daemon_api_token(monkeypatch):
    captured = {}
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "must-not-reach-child")

    def fake_run(argv, **kwargs):
        captured["argv"] = tuple(argv)
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart._run_systemctl(
        ("systemctl", "enable", AUTOSTART_UNIT)
    )

    assert result.returncode == 0
    assert captured["argv"] == ("systemctl", "enable", AUTOSTART_UNIT)
    assert "VR_HOTSPOTD_API_TOKEN" not in captured["env"]


def test_autostart_config_write_failure_restores_previous_service_state():
    commands = []

    def fail_writer(_updates):
        raise OSError("sensitive host detail must not escape")

    with pytest.raises(AutostartControlError) as exc_info:
        set_hotspot_autostart(
            True,
            runner=lambda command: commands.append(tuple(command))
            or SimpleNamespace(returncode=0),
            config_loader=lambda: {"autostart": False},
            config_writer=fail_writer,
        )

    assert exc_info.value.code == "autostart_config_update_failed"
    assert "sensitive host detail" not in str(exc_info.value)
    assert commands == [
        ("systemctl", "enable", AUTOSTART_UNIT),
        ("systemctl", "disable", "--now", AUTOSTART_UNIT),
    ]


@pytest.mark.parametrize("rollback_raises", (False, True))
def test_autostart_config_write_and_rollback_failure_reports_inconsistent_state(
    rollback_raises,
):
    commands = []
    secret = "rollback-secret-output-must-not-escape"

    def runner(command):
        commands.append(tuple(command))
        if len(commands) == 1:
            return SimpleNamespace(returncode=0)
        if rollback_raises:
            raise OSError(secret)
        return SimpleNamespace(
            returncode=1,
            stdout=secret * 1_000,
            stderr=secret * 1_000,
        )

    with pytest.raises(AutostartControlError) as exc_info:
        set_hotspot_autostart(
            True,
            runner=runner,
            config_loader=lambda: {"autostart": False},
            config_writer=lambda _updates: (_ for _ in ()).throw(
                OSError("config-secret-must-not-escape")
            ),
        )

    assert exc_info.value.code == AUTOSTART_ROLLBACK_FAILED
    assert str(exc_info.value) == AUTOSTART_ROLLBACK_FAILED
    assert secret not in str(exc_info.value)
    assert commands == [
        ("systemctl", "enable", AUTOSTART_UNIT),
        ("systemctl", "disable", "--now", AUTOSTART_UNIT),
    ]


def test_autostart_service_failure_does_not_change_config():
    writes = []

    with pytest.raises(AutostartControlError) as exc_info:
        set_hotspot_autostart(
            True,
            runner=lambda _command: SimpleNamespace(returncode=1),
            config_loader=lambda: {"autostart": False},
            config_writer=lambda updates: writes.append(updates),
        )

    assert exc_info.value.code == "autostart_service_update_failed"
    assert writes == []


def test_autostart_config_read_failure_is_fixed_and_runs_no_service_command():
    commands = []

    with pytest.raises(AutostartControlError) as exc_info:
        set_hotspot_autostart(
            True,
            runner=lambda command: commands.append(tuple(command)),
            config_loader=lambda: (_ for _ in ()).throw(
                OSError("sensitive config path")
            ),
        )

    assert exc_info.value.code == "autostart_config_read_failed"
    assert "sensitive config path" not in str(exc_info.value)
    assert commands == []


@pytest.mark.parametrize(
    "body",
    (
        {},
        {"enabled": 1},
        {"enabled": "true"},
        {"enabled": None},
        {"enabled": True, "other": False},
    ),
)
def test_autostart_api_strictly_rejects_non_boolean_or_extra_input(
    monkeypatch,
    body,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-token")
    calls = []
    monkeypatch.setattr(
        api,
        "set_hotspot_autostart",
        lambda enabled: calls.append(enabled),
    )
    handler = _handler(body)

    handler.do_POST()
    payload = _payload(handler)

    assert handler._last_code == 400
    assert payload["result_code"] == "invalid_request"
    assert payload["warnings"] == ["boolean_enabled_required"]
    assert calls == []


@pytest.mark.parametrize("enabled", (True, False))
def test_authenticated_autostart_api_updates_only_canonical_setting(
    monkeypatch,
    enabled,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-token")
    calls = []
    monkeypatch.setattr(
        api,
        "set_hotspot_autostart",
        lambda value: calls.append(value) or value,
    )
    handler = _handler({"enabled": enabled})

    handler.do_POST()
    payload = _payload(handler)

    assert handler._last_code == 200
    assert payload["result_code"] == (
        "autostart_enabled" if enabled else "autostart_disabled"
    )
    assert payload["data"] == {"autostart": enabled}
    assert calls == [enabled]


def test_autostart_api_returns_fixed_failure_without_command_output(
    monkeypatch,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-token")

    def fail(_enabled):
        raise AutostartControlError("autostart_service_update_failed")

    monkeypatch.setattr(api, "set_hotspot_autostart", fail)
    handler = _handler({"enabled": True})

    handler.do_POST()
    payload_text = handler.wfile.getvalue().decode("utf-8")
    payload = json.loads(payload_text)

    assert handler._last_code == 500
    assert payload["result_code"] == "autostart_update_failed"
    assert payload["warnings"] == ["autostart_service_update_failed"]
    assert "systemctl" not in payload_text


def test_autostart_api_reports_failed_rollback_as_inconsistent_without_details(
    monkeypatch,
):
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", "configured-token")
    secret = "rollback-command-secret-must-not-escape"

    def fail(_enabled):
        try:
            raise OSError(secret * 1_000)
        except OSError:
            raise AutostartControlError(AUTOSTART_ROLLBACK_FAILED) from None

    monkeypatch.setattr(api, "set_hotspot_autostart", fail)
    handler = _handler({"enabled": True})

    handler.do_POST()
    payload_text = handler.wfile.getvalue().decode("utf-8")
    payload = json.loads(payload_text)

    assert handler._last_code == 500
    assert payload["result_code"] == "autostart_state_inconsistent"
    assert payload["warnings"] == [AUTOSTART_ROLLBACK_FAILED]
    assert len(payload_text.encode("utf-8")) < 1_024
    assert secret not in payload_text
    assert "systemctl" not in payload_text
