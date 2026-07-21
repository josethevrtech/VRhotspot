import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shlex
import stat
import subprocess
import threading
from urllib.error import HTTPError, URLError

import pytest

from vr_hotspotd import cli


class _FakeResponse:
    def __init__(self, payload=None, *, status=200, raw=None):
        self.status = status
        self._raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

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


def _mocked_open(monkeypatch, payload, seen, *, status=200, raw=None):
    def open_request(request, *, timeout):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["timeout"] = timeout
        return _FakeResponse(payload, status=status, raw=raw)

    monkeypatch.setattr(cli, "_open_preflight_request", open_request)


def _mocked_http_error(monkeypatch, status, result_code):
    raw = json.dumps({"result_code": result_code}).encode("utf-8")

    def reject(request, *, timeout):
        assert timeout == 15.0
        raise HTTPError(
            request.full_url,
            status,
            "mocked error",
            {},
            io.BytesIO(raw),
        )

    monkeypatch.setattr(cli, "_open_preflight_request", reject)


def _missing_env_args(tmp_path):
    return ["--env-file", str(tmp_path / "missing-env")]


def test_preflight_command_performs_authenticated_get_only_to_canonical_endpoint(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "authenticated-test-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    report = {
        "schema_version": 1,
        "overall_readiness": "warning",
        "issues": [{"code": "default_uplink_not_detected", "severity": "warning"}],
    }
    seen = {}
    _mocked_open(
        monkeypatch,
        {
            "correlation_id": "test-cid",
            "result_code": "ok",
            "warnings": [],
            "data": report,
        },
        seen,
    )

    result = cli.main(
        [
            "preflight",
            "--api-url",
            "http://127.0.0.1:9900",
            *_missing_env_args(tmp_path),
            "--timeout",
            "3.5",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == report
    assert "correlation_id" not in json.loads(captured.out)
    assert captured.err == ""
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:9900/v1/diagnostics/preflight"
    assert seen["headers"]["x-api-token"] == secret
    assert seen["timeout"] == 3.5
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_uses_explicit_argument_token_without_disclosing_it(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "explicit-argument-token"
    report = {"schema_version": 1, "overall_readiness": "ready", "issues": []}
    seen = {}
    _mocked_open(monkeypatch, {"result_code": "ok", "data": report}, seen)

    result = cli.main(
        [
            "preflight",
            "--token",
            secret,
            *_missing_env_args(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == report
    assert captured.err == ""
    assert seen["headers"]["x-api-token"] == secret
    assert secret not in captured.out
    assert secret not in captured.err


@pytest.mark.parametrize(
    "secret",
    (
        pytest.param("unsafe\rheader", id="carriage-return"),
        pytest.param("unsafe\nheader", id="line-feed"),
        pytest.param("unsafe\x00header", id="nul"),
        pytest.param("unsafe\x1fheader", id="control-character"),
        pytest.param("unsafe\x7fheader", id="delete-character"),
        pytest.param("unsafe-\N{SNOWMAN}-header", id="non-ascii"),
    ),
)
def test_preflight_command_rejects_unsafe_header_tokens_without_disclosure(
    monkeypatch,
    tmp_path,
    capsys,
    secret,
):
    called = False

    def must_not_open(_request, *, timeout):
        nonlocal called
        called = True
        raise AssertionError(f"unexpected request with timeout {timeout}")

    monkeypatch.setattr(cli, "_open_preflight_request", must_not_open)
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                "--token",
                secret,
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "unsafe for an HTTP header" in captured.err
    assert called is False
    assert not output.exists()
    assert secret not in captured.out
    assert secret not in captured.err


def test_unexpected_transport_error_is_sanitized_without_exception_chain(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "unexpected-transport-token"

    def unexpected_failure(_request, *, timeout):
        assert timeout == 15.0
        raise ValueError(f"invalid header value containing {secret}")

    monkeypatch.setattr(cli, "_open_preflight_request", unexpected_failure)
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                "--token",
                secret,
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    cli_error = exc_info.value.__context__
    assert exc_info.value.code == 1
    assert "unexpected transport error (ValueError)" in captured.err
    assert isinstance(cli_error, cli.CLIError)
    assert cli_error.__cause__ is None
    assert cli_error.__context__ is None
    assert not output.exists()
    assert secret not in str(cli_error)
    assert secret not in captured.out
    assert secret not in captured.err


def test_url_error_reason_is_redacted_without_retaining_exception_chain(monkeypatch):
    secret = "url-error-reason-token"

    def unavailable(_request, *, timeout):
        assert timeout == 15.0
        raise URLError(RuntimeError(f"transport failure containing {secret}"))

    monkeypatch.setattr(cli, "_open_preflight_request", unavailable)

    with pytest.raises(cli.CLIError) as exc_info:
        cli.fetch_preflight_report(cli.DEFAULT_API_URL, token=secret)

    error = exc_info.value
    assert "transport failure containing [redacted]" in str(error)
    assert secret not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_preflight_command_exports_report_using_protected_service_env_file(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "protected-file-token"
    env_file = tmp_path / "vr-hotspot.env"
    env_file.write_text(
        "\n".join(
            (
                f'VR_HOTSPOTD_API_TOKEN="{secret}"',
                "VR_HOTSPOTD_HOST=0.0.0.0",
                "VR_HOTSPOTD_PORT=9876",
            )
        ),
        encoding="utf-8",
    )
    report = {"schema_version": 1, "overall_readiness": "ready", "issues": []}
    seen = {}
    _mocked_open(monkeypatch, {"result_code": "ok", "data": report}, seen)
    output = tmp_path / "preflight.json"

    result = cli.main(
        [
            "preflight",
            "--env-file",
            str(env_file),
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    exported = output.read_text(encoding="utf-8")
    assert result == 0
    assert captured.out == ""
    assert "Wrote canonical preflight report" in captured.err
    assert json.loads(exported) == report
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert seen["url"] == "http://127.0.0.1:9876/v1/diagnostics/preflight"
    assert seen["headers"]["x-api-token"] == secret
    assert secret not in captured.out
    assert secret not in captured.err
    assert secret not in exported


def test_preflight_command_reads_token_from_stdin_without_echoing_it(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "stdin-only-token"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{secret}\n"))
    seen = {}
    _mocked_open(
        monkeypatch,
        {"result_code": "ok", "data": {"schema_version": 1, "issues": []}},
        seen,
    )

    result = cli.main(
        [
            "preflight",
            "--token-stdin",
            *_missing_env_args(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert seen["headers"]["x-api-token"] == secret
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_hides_interactive_stdin_token(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "hidden-interactive-token"

    class InteractiveInput:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt, *, stream: secret)
    seen = {}
    _mocked_open(
        monkeypatch,
        {"result_code": "ok", "data": {"schema_version": 1, "issues": []}},
        seen,
    )

    result = cli.main(
        [
            "preflight",
            "--token-stdin",
            *_missing_env_args(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert seen["headers"]["x-api-token"] == secret
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_rejects_redirect_without_forwarding_token(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "redirect-guard-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    requests = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(
                {
                    "path": self.path,
                    "token": self.headers.get("X-Api-Token"),
                }
            )
            if self.path == cli.PREFLIGHT_PATH:
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"result_code":"ok","data":{}}')

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "preflight",
                    "--api-url",
                    f"http://127.0.0.1:{server.server_port}",
                    *_missing_env_args(tmp_path),
                ]
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "redirected (HTTP 302)" in captured.err
    assert "redirects are not allowed" in captured.err
    assert requests == [{"path": cli.PREFLIGHT_PATH, "token": secret}]
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_reports_service_unavailable(monkeypatch, tmp_path, capsys):
    secret = "unavailable-service-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)

    def unavailable(_request, *, timeout):
        assert timeout == 15.0
        raise URLError(ConnectionRefusedError("mocked service unavailable"))

    monkeypatch.setattr(cli, "_open_preflight_request", unavailable)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["preflight", *_missing_env_args(tmp_path)])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Unable to reach the VR Hotspot API" in captured.err
    assert "mocked service unavailable" in captured.err
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_reports_auth_failure_without_token_leak(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "auth-failure-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    _mocked_http_error(monkeypatch, 401, "unauthorized")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["preflight", *_missing_env_args(tmp_path)])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "HTTP 401: unauthorized" in captured.err
    assert "VR_HOTSPOTD_API_TOKEN or --token-stdin" in captured.err
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_reports_generic_non_200(monkeypatch, tmp_path, capsys):
    _mocked_http_error(monkeypatch, 500, "internal_error")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["preflight", *_missing_env_args(tmp_path)])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "API request failed (HTTP 500: internal_error)" in captured.err
    assert captured.out == ""


def test_preflight_command_rejects_malformed_json(monkeypatch, tmp_path, capsys):
    seen = {}
    _mocked_open(monkeypatch, None, seen, raw=b"{not-json")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["preflight", *_missing_env_args(tmp_path)])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "returned invalid JSON" in captured.err
    assert captured.out == ""


def test_preflight_command_reports_unwritable_output_path(monkeypatch, tmp_path, capsys):
    secret = "output-error-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    seen = {}
    _mocked_open(
        monkeypatch,
        {"result_code": "ok", "data": {"schema_version": 1, "issues": []}},
        seen,
    )
    output = tmp_path / "missing-parent" / "preflight.json"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "Unable to write preflight report" in captured.err
    assert not output.exists()
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_does_not_overwrite_existing_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "existing-output-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    seen = {}
    _mocked_open(
        monkeypatch,
        {"result_code": "ok", "data": {"schema_version": 1, "issues": []}},
        seen,
    )
    output = tmp_path / f"{secret}.json"
    output.write_text("keep-existing-content\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "already exists or is a symlink" in captured.err
    assert "refusing to overwrite" in captured.err
    assert output.read_text(encoding="utf-8") == "keep-existing-content\n"
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_rejects_symlink_output_without_touching_target(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "symlink-output-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    seen = {}
    _mocked_open(
        monkeypatch,
        {"result_code": "ok", "data": {"schema_version": 1, "issues": []}},
        seen,
    )
    target = tmp_path / "target.json"
    target.write_text("keep-target-content\n", encoding="utf-8")
    output = tmp_path / "preflight.json"
    output.symlink_to(target)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "already exists or is a symlink" in captured.err
    assert output.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep-target-content\n"
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_refuses_report_that_contains_authentication_token(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret = "reflected-authentication-token"
    monkeypatch.setenv("VR_HOTSPOTD_API_TOKEN", secret)
    seen = {}
    _mocked_open(
        monkeypatch,
        {
            "result_code": "ok",
            "data": {"schema_version": 1, "unexpected_secret": secret},
        },
        seen,
    )
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                *_missing_env_args(tmp_path),
                "--output",
                str(output),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "report containing the authentication token" in captured.err
    assert not output.exists()
    assert secret not in captured.out
    assert secret not in captured.err


def test_preflight_command_rejects_api_base_paths_before_request(
    monkeypatch,
    tmp_path,
    capsys,
):
    called = False

    def must_not_open(_request, *, timeout):
        nonlocal called
        called = True
        raise AssertionError(f"unexpected request with timeout {timeout}")

    monkeypatch.setattr(cli, "_open_preflight_request", must_not_open)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "preflight",
                "--api-url",
                "http://127.0.0.1:8732/not-the-preflight-endpoint",
                *_missing_env_args(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "without a path" in captured.err
    assert called is False


def test_preflight_help_warns_about_argument_token_and_offers_stdin(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["preflight", "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--token-stdin" in captured.out
    normalized_help = " ".join(captured.out.split())
    assert "visible in process arguments and shell history" in normalized_help
    assert "existing paths and symlinks are refused" in normalized_help


def test_cli_entry_points_and_installed_launcher_behavior_are_preserved(tmp_path):
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    installer = (root / "backend" / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'vr-hotspot = "vr_hotspotd.cli:main"' in pyproject
    assert 'vr-hotspotd = "vr_hotspotd.main:main"' in pyproject
    start = installer.index("install_cli_launcher() {")
    end = installer.index("\n}\n", start) + 2
    launcher_function = installer[start:end]
    assert 'local cli_src="$VENV_DIR/bin/vr-hotspot"' in launcher_function
    assert 'local cli_dst="$BIN_DIR/vr-hotspot"' in launcher_function
    assert 'ln -sfn "$cli_src" "$cli_dst"' in launcher_function
    pip_install = installer.index('"$VENV_DIR/bin/pip" install --no-cache-dir "$INSTALL_DIR"')
    launcher_call = installer.index("\ninstall_cli_launcher\n", pip_install)
    daemon_setup = installer.index("\ninstall_systemd_units\n", launcher_call)
    assert pip_install < launcher_call < daemon_setup

    venv_cli = tmp_path / "venv" / "bin" / "vr-hotspot"
    venv_cli.parent.mkdir(parents=True)
    venv_cli.write_text(
        "#!/usr/bin/env bash\nprintf 'cli-args=%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    venv_cli.chmod(0o755)
    bin_dir = tmp_path / "bin"
    command = "\n".join(
        (
            "set -euo pipefail",
            f"VENV_DIR={shlex.quote(str(tmp_path / 'venv'))}",
            f"BIN_DIR={shlex.quote(str(bin_dir))}",
            "log() { :; }",
            'die() { printf "%s\\n" "$*" >&2; exit 1; }',
            launcher_function,
            "install_cli_launcher",
            '"$BIN_DIR/vr-hotspot" preflight --help',
        )
    )

    completed = subprocess.run(
        ["bash", "-c", command],
        text=True,
        capture_output=True,
        check=False,
    )

    installed_cli = bin_dir / "vr-hotspot"
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "cli-args=preflight --help\n"
    assert installed_cli.is_symlink()
    assert installed_cli.resolve() == venv_cli
