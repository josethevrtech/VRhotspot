import sys
from subprocess import CompletedProcess, TimeoutExpired

from vr_hotspotd.diagnostics.support_bundle import (
    CollectorStatus,
    SECRET_PLACEHOLDER,
    collect_command,
    collect_file,
)


def test_successful_command_collection():
    collected = collect_command(
        [sys.executable, "-c", "print('collector ok')"],
        timeout=5,
    )

    assert collected.result.status == CollectorStatus.OK
    assert collected.result.exit_code == 0
    assert collected.result.timed_out is False
    assert collected.result.permission_denied is False
    assert collected.stdout == "collector ok\n"
    assert collected.stderr == ""


def test_missing_command():
    def runner(*args, **kwargs):
        raise FileNotFoundError("missing collector")

    collected = collect_command(["missing-vr-hotspot-helper"], timeout=1, runner=runner)

    assert collected.result.status == CollectorStatus.MISSING_COMMAND
    assert collected.result.exit_code is None
    assert collected.result.error_summary == "missing collector"


def test_permission_denied_command():
    def runner(*args, **kwargs):
        raise PermissionError("permission denied running collector")

    collected = collect_command(["journalctl"], timeout=1, runner=runner)

    assert collected.result.status == CollectorStatus.PERMISSION_DENIED
    assert collected.result.exit_code is None
    assert collected.result.permission_denied is True
    assert collected.result.error_summary == "permission denied running collector"


def test_timeout():
    def runner(*args, **kwargs):
        raise TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output="VR_HOTSPOTD_API_TOKEN=secret-token\n",
            stderr="still running\n",
        )

    collected = collect_command(["slow-command"], timeout=2, runner=runner)

    assert collected.result.status == CollectorStatus.TIMEOUT
    assert collected.result.exit_code is None
    assert collected.result.timed_out is True
    assert collected.result.error_summary == "collector timed out after 2s"
    assert collected.stdout == f"VR_HOTSPOTD_API_TOKEN={SECRET_PLACEHOLDER}\n"
    assert collected.stderr == "still running\n"


def test_failed_command():
    def runner(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=42,
            stdout="partial output\n",
            stderr="collector failed\n",
        )

    collected = collect_command(["nmcli", "device", "status"], timeout=1, runner=runner)

    assert collected.result.status == CollectorStatus.FAILED
    assert collected.result.exit_code == 42
    assert collected.result.error_summary == "command exited with status 42"
    assert collected.stdout == "partial output\n"
    assert collected.stderr == "collector failed\n"


def test_command_redaction_applied_to_stdout_and_stderr():
    def runner(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="VR_HOTSPOTD_API_TOKEN=stdout-secret\n",
            stderr="wpa_passphrase=stderr-secret\n",
        )

    collected = collect_command(["print-secrets"], timeout=1, runner=runner)

    assert "stdout-secret" not in collected.stdout
    assert "stderr-secret" not in collected.stderr
    assert collected.stdout == f"VR_HOTSPOTD_API_TOKEN={SECRET_PLACEHOLDER}\n"
    assert collected.stderr == f"wpa_passphrase={SECRET_PLACEHOLDER}\n"


def test_command_redaction_failure_omits_raw_output():
    def runner(*args, **kwargs):
        return CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="VR_HOTSPOTD_API_TOKEN=raw-secret\n",
            stderr="raw stderr\n",
        )

    def broken_redactor(value):
        raise RuntimeError("redactor unavailable")

    collected = collect_command(
        ["print-secrets"],
        timeout=1,
        runner=runner,
        redactor=broken_redactor,
    )

    assert collected.result.status == CollectorStatus.REDACTION_FAILED
    assert collected.result.exit_code == 0
    assert collected.result.error_summary == "redaction failed; raw command output omitted"
    assert collected.stdout == ""
    assert collected.stderr == ""


def test_file_collection_success(tmp_path):
    source = tmp_path / "os-release"
    source.write_text("ID=steamos\n", encoding="utf-8")

    collected = collect_file(
        source,
        "system/os-release.txt",
        collector="os release",
    )

    assert collected.result.status == CollectorStatus.OK
    assert collected.result.path == "system/os-release.txt"
    assert collected.result.collector == "os release"
    assert collected.result.size_bytes == len("ID=steamos\n")
    assert collected.content == "ID=steamos\n"


def test_file_missing(tmp_path):
    collected = collect_file(
        tmp_path / "missing",
        "system/os-release.txt",
        collector="os release",
    )

    assert collected.result.status == CollectorStatus.NOT_APPLICABLE
    assert collected.result.size_bytes == 0
    assert collected.result.error_summary.startswith("file not found:")
    assert collected.content == ""


def test_file_permission_denied(monkeypatch, tmp_path):
    source = tmp_path / "protected"
    source.write_text("secret\n", encoding="utf-8")

    def denied(self, *args, **kwargs):
        raise PermissionError

    monkeypatch.setattr("pathlib.Path.read_text", denied)

    collected = collect_file(
        source,
        "service/journal.txt",
        collector="journal",
    )

    assert collected.result.status == CollectorStatus.PERMISSION_DENIED
    assert collected.result.size_bytes == 0
    assert collected.result.error_summary == f"permission denied reading {source}"
    assert collected.content == ""


def test_file_redaction_applied(tmp_path):
    source = tmp_path / "config.env"
    source.write_text(
        "ssid=VRHotspot\nwpa_passphrase=file-secret\n",
        encoding="utf-8",
    )

    collected = collect_file(
        source,
        "vr-hotspot/config.redacted.env",
        collector="config",
    )

    assert "file-secret" not in collected.content
    assert collected.content == f"ssid=VRHotspot\nwpa_passphrase={SECRET_PLACEHOLDER}\n"
    assert collected.result.size_bytes == len(collected.content.encode("utf-8"))


def test_file_redaction_failure_omits_raw_content(tmp_path):
    source = tmp_path / "config.env"
    source.write_text("VR_HOTSPOTD_API_TOKEN=raw-secret\n", encoding="utf-8")

    def broken_redactor(value):
        raise RuntimeError("redactor unavailable")

    collected = collect_file(
        source,
        "vr-hotspot/config.redacted.env",
        collector="config",
        redactor=broken_redactor,
    )

    assert collected.result.status == CollectorStatus.REDACTION_FAILED
    assert collected.result.size_bytes == 0
    assert collected.result.error_summary == "redaction failed; raw file content omitted"
    assert collected.content == ""
