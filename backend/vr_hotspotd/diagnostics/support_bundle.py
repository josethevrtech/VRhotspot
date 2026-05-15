"""Pure helpers for future diagnostics support bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import posixpath
import re
import shlex
import subprocess
from subprocess import CompletedProcess, TimeoutExpired
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Union
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from vr_hotspotd import __version__


SECRET_PLACEHOLDER = "<redacted-secret>"
AUTHORIZATION_PLACEHOLDER = "<redacted-authorization>"
PRIVATE_KEY_PLACEHOLDER = "<redacted-private-key>"
SUPPORT_BUNDLE_SCHEMA_VERSION = 1
DEFAULT_SUPPORT_BUNDLE_README = (
    "VR Hotspot diagnostics support bundle\n\n"
    "This archive contains already-sanitized diagnostic files. "
    "Secrets and personal identifiers should be redacted before files are added.\n"
)
_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class CollectorStatus:
    """Documented support-bundle collector result states."""

    OK = "ok"
    MISSING_COMMAND = "missing_command"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    REDACTION_FAILED = "redaction_failed"


SUPPORT_BUNDLE_RESULT_STATES = (
    CollectorStatus.OK,
    CollectorStatus.MISSING_COMMAND,
    CollectorStatus.PERMISSION_DENIED,
    CollectorStatus.TIMEOUT,
    CollectorStatus.NOT_APPLICABLE,
    CollectorStatus.FAILED,
    CollectorStatus.REDACTION_FAILED,
)


SUPPORT_BUNDLE_REDACTION_POLICY = {
    "secrets": "redacted",
    "emails_usernames": "redacted_when_detected",
    "public_ips": "redacted",
    "mac_addresses": "redacted_by_default",
}


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_RE = re.compile(r"(?im)(\bAuthorization\s*:\s*)[^\r\n]+")
_QUERY_TOKEN_RE = re.compile(
    r"(?i)([?&](?:api[_-]?token|access[_-]?token|auth[_-]?token|token)=)[^&\s\"']+"
)
_INLINE_SECRET_RE = re.compile(
    r"(?i)(\b[A-Za-z0-9_.-]*(?:"
    r"VR_HOTSPOTD_API_TOKEN|wpa_passphrase|sae_password|passphrase|password|secret|token|psk"
    r")[A-Za-z0-9_.-]*\s*=\s*)[^\s;&\"']+"
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_HOME_USER_RE = re.compile(r"(?P<prefix>/home/)(?P<user>[^/\s:]+)")
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_LINE_SECRET_RE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Za-z0-9_.-]*(?:"
    r"VR_HOTSPOTD_API_TOKEN|wpa_passphrase|sae_password|passphrase|password|secret|token|psk"
    r")[A-Za-z0-9_.-]*\s*[:=]\s*)[^\r\n#]+"
)
_JSON_SECRET_RE = re.compile(
    r'(?i)("?[A-Za-z0-9_.-]*(?:'
    r'VR_HOTSPOTD_API_TOKEN|wpa_passphrase|sae_password|passphrase|password|secret|token|psk'
    r')[A-Za-z0-9_.-]*"?\s*:\s*)(".*?"|[^,\r\n}]+)'
)


@dataclass(frozen=True)
class ArchiveFileMetadata:
    """Static metadata for a support-bundle archive member."""

    path: str
    collector: str
    content_type: str = "text/plain"

    def to_manifest_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "collector": self.collector,
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class SupportBundleFileResult:
    """Manifest metadata for one sanitized archive file."""

    path: str
    collector: str
    status: str = CollectorStatus.OK
    content_type: str = "text/plain"
    size_bytes: int = 0
    error_summary: Optional[str] = None

    def to_manifest_dict(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "path": self.path,
            "collector": self.collector,
            "status": self.status,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        }
        if self.error_summary:
            item["error_summary"] = self.error_summary
        return item


@dataclass(frozen=True)
class SupportBundleCommandResult:
    """Manifest metadata for one command collector outcome."""

    command: Union[str, Sequence[str]]
    status: str = CollectorStatus.OK
    exit_code: Optional[int] = 0
    timed_out: bool = False
    permission_denied: bool = False
    error_summary: Optional[str] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None

    def to_manifest_dict(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "command": _format_command(self.command),
            "status": self.status,
        }
        if self.exit_code is not None:
            item["exit_code"] = self.exit_code
        if self.timed_out:
            item["timed_out"] = True
        if self.permission_denied:
            item["permission_denied"] = True
        if self.error_summary:
            item["error_summary"] = self.error_summary
        if self.stdout_path:
            item["stdout_path"] = self.stdout_path
        if self.stderr_path:
            item["stderr_path"] = self.stderr_path
        return item


@dataclass(frozen=True)
class CollectedCommand:
    """Sanitized command collector output plus manifest metadata."""

    result: SupportBundleCommandResult
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CollectedFile:
    """Sanitized file collector output plus manifest metadata."""

    result: SupportBundleFileResult
    content: str = ""


@dataclass(frozen=True)
class AssembledSupportBundle:
    """In-memory support bundle archive plus response metadata."""

    archive_bytes: bytes
    filename: str
    manifest: Dict[str, Any]


SUPPORT_BUNDLE_ARCHIVE_LAYOUT = (
    ArchiveFileMetadata("manifest.json", "manifest", "application/json"),
    ArchiveFileMetadata("README.txt", "readme"),
    ArchiveFileMetadata("system/os-release.txt", "os release"),
    ArchiveFileMetadata("system/kernel.txt", "kernel"),
    ArchiveFileMetadata("system/command-results.json", "command results", "application/json"),
    ArchiveFileMetadata("service/status.txt", "systemctl status"),
    ArchiveFileMetadata("service/show.json", "systemctl show", "application/json"),
    ArchiveFileMetadata("service/journal.txt", "journalctl"),
    ArchiveFileMetadata("vr-hotspot/version.json", "vr hotspot version", "application/json"),
    ArchiveFileMetadata("vr-hotspot/status.json", "vr hotspot status", "application/json"),
    ArchiveFileMetadata("vr-hotspot/adapters.json", "vr hotspot adapters", "application/json"),
    ArchiveFileMetadata("vr-hotspot/readiness.json", "vr hotspot readiness", "application/json"),
    ArchiveFileMetadata("vr-hotspot/config.redacted.json", "vr hotspot config", "application/json"),
    ArchiveFileMetadata("wireless/iw-dev.txt", "iw dev"),
    ArchiveFileMetadata("wireless/iw-list.txt", "iw list"),
    ArchiveFileMetadata("wireless/iw-reg-get.txt", "iw reg get"),
    ArchiveFileMetadata("wireless/rfkill-list.txt", "rfkill list"),
    ArchiveFileMetadata("network/nmcli-device-status.txt", "nmcli device status"),
    ArchiveFileMetadata("network/firewall.txt", "firewall"),
    ArchiveFileMetadata("network/ufw-status.txt", "ufw status verbose"),
)


def default_support_bundle_archive_layout() -> list[Dict[str, Any]]:
    """Return the documented archive member metadata."""
    return [item.to_manifest_dict() for item in SUPPORT_BUNDLE_ARCHIVE_LAYOUT]


def default_support_bundle_redaction_policy() -> Dict[str, str]:
    """Return the manifest redaction policy."""
    return dict(SUPPORT_BUNDLE_REDACTION_POLICY)


def make_support_bundle_manifest(
    *,
    files: Iterable[SupportBundleFileResult] = (),
    command_results: Iterable[SupportBundleCommandResult] = (),
    generated_at: Optional[datetime] = None,
    vr_hotspot_version: str = __version__,
    platform_summary: Optional[Mapping[str, Any]] = None,
    hostname_redacted: Optional[bool] = None,
    hostname_note: Optional[str] = "hostname_not_collected",
    warnings: Iterable[str] = (),
    redaction_policy: Optional[Mapping[str, Any]] = None,
    bundle_schema_version: int = SUPPORT_BUNDLE_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """Build a redacted support-bundle manifest from already-collected results."""
    when = generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    generated_at_text = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    manifest: Dict[str, Any] = {
        "bundle_schema_version": bundle_schema_version,
        "generated_at": generated_at_text,
        "vr_hotspot_version": vr_hotspot_version,
        "platform_summary": dict(platform_summary or {}),
        "redaction_policy": dict(redaction_policy or SUPPORT_BUNDLE_REDACTION_POLICY),
        "files": [item.to_manifest_dict() for item in files],
        "command_results": [item.to_manifest_dict() for item in command_results],
        "warnings": list(warnings),
    }
    if hostname_redacted is None:
        manifest["hostname_note"] = hostname_note or "hostname_not_collected"
    else:
        manifest["hostname_redacted"] = hostname_redacted

    return _RedactionRun().redact_data(manifest)


def assemble_support_bundle(
    *,
    commands: Iterable[CollectedCommand] = (),
    files: Iterable[CollectedFile] = (),
    generated_at: Optional[datetime] = None,
    vr_hotspot_version: str = __version__,
    platform_summary: Optional[Mapping[str, Any]] = None,
    hostname_redacted: Optional[bool] = None,
    hostname_note: Optional[str] = "hostname_not_collected",
    warnings: Iterable[str] = (),
    redaction_policy: Optional[Mapping[str, Any]] = None,
    readme: Optional[Union[str, bytes]] = None,
) -> AssembledSupportBundle:
    """Assemble a support bundle from already-sanitized collector results.

    This helper is intentionally pure: it does not run collectors, read source
    files, or inspect the host system. Callers are responsible for passing only
    sanitized command/file content.
    """
    when = generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when_utc = when.astimezone(timezone.utc)

    archive_files: list[tuple[str, Union[str, bytes]]] = []
    manifest_files: list[SupportBundleFileResult] = []
    manifest_commands: list[SupportBundleCommandResult] = []

    for collected_file in files:
        file_result = collected_file.result
        archive_path = _safe_archive_path(file_result.path)
        manifest_files.append(
            SupportBundleFileResult(
                path=archive_path,
                collector=file_result.collector,
                status=file_result.status,
                content_type=file_result.content_type,
                size_bytes=file_result.size_bytes,
                error_summary=file_result.error_summary,
            )
        )
        if file_result.status != CollectorStatus.REDACTION_FAILED:
            archive_files.append((archive_path, collected_file.content))

    for index, collected_command in enumerate(commands, start=1):
        command_result = collected_command.result
        collector_name = _format_command(command_result.command)
        stdout_path: Optional[str] = None
        stderr_path: Optional[str] = None

        if command_result.status != CollectorStatus.REDACTION_FAILED:
            if collected_command.stdout:
                stdout_path = _command_output_archive_path(
                    index, command_result.command, "stdout"
                )
                archive_files.append((stdout_path, collected_command.stdout))
                manifest_files.append(
                    SupportBundleFileResult(
                        path=stdout_path,
                        collector=collector_name,
                        status=command_result.status,
                        content_type="text/plain",
                        size_bytes=len(collected_command.stdout.encode("utf-8")),
                    )
                )
            if collected_command.stderr:
                stderr_path = _command_output_archive_path(
                    index, command_result.command, "stderr"
                )
                archive_files.append((stderr_path, collected_command.stderr))
                manifest_files.append(
                    SupportBundleFileResult(
                        path=stderr_path,
                        collector=collector_name,
                        status=command_result.status,
                        content_type="text/plain",
                        size_bytes=len(collected_command.stderr.encode("utf-8")),
                    )
                )

        manifest_commands.append(
            SupportBundleCommandResult(
                command=command_result.command,
                status=command_result.status,
                exit_code=command_result.exit_code,
                timed_out=command_result.timed_out,
                permission_denied=command_result.permission_denied,
                error_summary=command_result.error_summary,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        )

    manifest = make_support_bundle_manifest(
        files=manifest_files,
        command_results=manifest_commands,
        generated_at=when_utc,
        vr_hotspot_version=vr_hotspot_version,
        platform_summary=platform_summary,
        hostname_redacted=hostname_redacted,
        hostname_note=hostname_note,
        warnings=warnings,
        redaction_policy=redaction_policy,
    )
    archive_bytes = build_support_bundle_archive(
        manifest,
        archive_files,
        readme=readme,
    )
    filename = f"vr-hotspot-support-bundle-{when_utc:%Y%m%d-%H%M%S}.zip"
    return AssembledSupportBundle(
        archive_bytes=archive_bytes,
        filename=filename,
        manifest=manifest,
    )


def redact_support_bundle_text(text: str) -> str:
    """Return redacted support-bundle text with stable placeholders per call."""
    return _RedactionRun().redact_text(text)


def redact_support_bundle_data(value: Any) -> Any:
    """Return a redacted copy of nested support-bundle data."""
    return _RedactionRun().redact_data(value)


def command_collection_result(
    command: Union[str, Sequence[str]],
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: Optional[int] = 0,
    status: str = CollectorStatus.OK,
    timed_out: bool = False,
    permission_denied: bool = False,
    error_summary: Optional[str] = None,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Create a sanitized command collection result."""
    try:
        redact = redactor or _RedactionRun().redact_text
        sanitized_stdout = redact(_text_from_subprocess_value(stdout))
        sanitized_stderr = redact(_text_from_subprocess_value(stderr))
        sanitized_error = redact(error_summary) if error_summary else None
    except Exception:
        return CollectedCommand(
            result=SupportBundleCommandResult(
                command=command,
                status=CollectorStatus.REDACTION_FAILED,
                exit_code=exit_code,
                timed_out=timed_out,
                permission_denied=permission_denied,
                error_summary="redaction failed; raw command output omitted",
            )
        )

    return CollectedCommand(
        result=SupportBundleCommandResult(
            command=command,
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            permission_denied=permission_denied,
            error_summary=sanitized_error,
        ),
        stdout=sanitized_stdout,
        stderr=sanitized_stderr,
    )


def missing_command_result(
    command: Union[str, Sequence[str]],
    *,
    error_summary: Optional[str] = None,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Create a missing-command collector result."""
    return command_collection_result(
        command,
        exit_code=None,
        status=CollectorStatus.MISSING_COMMAND,
        error_summary=error_summary or f"{_command_name(command)} not found",
        redactor=redactor,
    )


def permission_denied_command_result(
    command: Union[str, Sequence[str]],
    *,
    exit_code: Optional[int] = None,
    stdout: str = "",
    stderr: str = "",
    error_summary: Optional[str] = None,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Create a permission-denied command collector result."""
    return command_collection_result(
        command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        status=CollectorStatus.PERMISSION_DENIED,
        permission_denied=True,
        error_summary=error_summary or "permission denied",
        redactor=redactor,
    )


def timeout_command_result(
    command: Union[str, Sequence[str]],
    *,
    timeout: float,
    stdout: Union[str, bytes, None] = "",
    stderr: Union[str, bytes, None] = "",
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Create a timed-out command collector result."""
    return command_collection_result(
        command,
        stdout=_text_from_subprocess_value(stdout),
        stderr=_text_from_subprocess_value(stderr),
        exit_code=None,
        status=CollectorStatus.TIMEOUT,
        timed_out=True,
        error_summary=f"collector timed out after {timeout:g}s",
        redactor=redactor,
    )


def failed_command_result(
    command: Union[str, Sequence[str]],
    *,
    exit_code: Optional[int],
    stdout: str = "",
    stderr: str = "",
    error_summary: Optional[str] = None,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Create a failed command collector result."""
    return command_collection_result(
        command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        status=CollectorStatus.FAILED,
        error_summary=error_summary or f"command exited with status {exit_code}",
        redactor=redactor,
    )


def collect_command(
    command: Union[str, Sequence[str]],
    *,
    timeout: float,
    runner: Callable[..., CompletedProcess[str]] = subprocess.run,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedCommand:
    """Run a bounded command collector and return sanitized output metadata."""
    args = _command_args(command)
    try:
        completed = runner(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return missing_command_result(command, error_summary=str(exc), redactor=redactor)
    except PermissionError as exc:
        return permission_denied_command_result(
            command,
            error_summary=str(exc),
            redactor=redactor,
        )
    except TimeoutExpired as exc:
        return timeout_command_result(
            command,
            timeout=timeout,
            stdout=exc.output,
            stderr=exc.stderr,
            redactor=redactor,
        )

    stdout = _text_from_subprocess_value(completed.stdout)
    stderr = _text_from_subprocess_value(completed.stderr)
    exit_code = completed.returncode
    if exit_code == 0:
        return command_collection_result(
            command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            status=CollectorStatus.OK,
            redactor=redactor,
        )

    if _looks_permission_denied(stderr) or _looks_permission_denied(stdout):
        return permission_denied_command_result(
            command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error_summary="permission denied",
            redactor=redactor,
        )

    return failed_command_result(
        command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        redactor=redactor,
    )


def file_collection_result(
    path: str,
    collector: str,
    *,
    content: str = "",
    status: str = CollectorStatus.OK,
    content_type: str = "text/plain",
    error_summary: Optional[str] = None,
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedFile:
    """Create a sanitized file collection result."""
    try:
        redact = redactor or _RedactionRun().redact_text
        sanitized_content = redact(content)
        sanitized_error = redact(error_summary) if error_summary else None
    except Exception:
        return CollectedFile(
            result=SupportBundleFileResult(
                path=path,
                collector=collector,
                status=CollectorStatus.REDACTION_FAILED,
                content_type=content_type,
                size_bytes=0,
                error_summary="redaction failed; raw file content omitted",
            )
        )

    return CollectedFile(
        result=SupportBundleFileResult(
            path=path,
            collector=collector,
            status=status,
            content_type=content_type,
            size_bytes=len(sanitized_content.encode("utf-8")),
            error_summary=sanitized_error,
        ),
        content=sanitized_content,
    )


def collect_file(
    source_path: Union[str, Path],
    bundle_path: str,
    *,
    collector: Optional[str] = None,
    content_type: str = "text/plain",
    redactor: Optional[Callable[[str], str]] = None,
) -> CollectedFile:
    """Read and sanitize one text file for a future support bundle."""
    source = Path(source_path)
    collector_name = collector or str(source)
    try:
        content = source.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return file_collection_result(
            bundle_path,
            collector_name,
            status=CollectorStatus.NOT_APPLICABLE,
            content_type=content_type,
            error_summary=f"file not found: {source}",
        )
    except PermissionError:
        return file_collection_result(
            bundle_path,
            collector_name,
            status=CollectorStatus.PERMISSION_DENIED,
            content_type=content_type,
            error_summary=f"permission denied reading {source}",
        )

    return file_collection_result(
        bundle_path,
        collector_name,
        content=content,
        status=CollectorStatus.OK,
        content_type=content_type,
        redactor=redactor,
    )


def build_support_bundle_archive(
    manifest: Mapping[str, Any],
    files: Union[Mapping[str, Union[str, bytes]], Iterable[tuple[str, Union[str, bytes]]]],
    *,
    readme: Optional[Union[str, bytes]] = None,
) -> bytes:
    """Build a deterministic .zip archive from already-sanitized bundle content."""
    members: Dict[str, bytes] = {
        "manifest.json": _json_bytes(manifest),
        "README.txt": _content_bytes(
            DEFAULT_SUPPORT_BUNDLE_README if readme is None else readme
        ),
    }

    for path, content in _iter_archive_files(files):
        archive_path = _safe_archive_path(path)
        if archive_path in members:
            raise ValueError(f"duplicate support bundle archive path: {archive_path}")
        members[archive_path] = _content_bytes(content)

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(members):
            info = ZipInfo(filename=path, date_time=_ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, members[path])
    return buffer.getvalue()


def is_sensitive_key(key: Any) -> bool:
    """Return True when a config/log key should be treated as secret-bearing."""
    if not isinstance(key, str):
        return False
    normalized = key.strip().lower().replace("-", "_")
    if normalized in {"psk", "wpa_passphrase", "sae_password", "vr_hotspotd_api_token"}:
        return True
    return normalized.endswith(
        ("_psk", "_password", "_passphrase", "_secret", "_token")
    ) or normalized in {"password", "passphrase", "secret", "token", "api_token"}


def _format_command(command: Union[str, Sequence[str]]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(str(part) for part in command)


def _command_args(command: Union[str, Sequence[str]]) -> Sequence[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(part) for part in command]


def _command_name(command: Union[str, Sequence[str]]) -> str:
    args = _command_args(command)
    return args[0] if args else ""


def _text_from_subprocess_value(value: Union[str, bytes, None]) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _looks_permission_denied(text: str) -> bool:
    return "permission denied" in text.lower()


def _iter_archive_files(
    files: Union[Mapping[str, Union[str, bytes]], Iterable[tuple[str, Union[str, bytes]]]]
) -> Iterable[tuple[str, Union[str, bytes]]]:
    if isinstance(files, Mapping):
        return files.items()
    return files


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _content_bytes(content: Union[str, bytes]) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError("support bundle archive content must be str or bytes")


def _command_output_archive_path(
    index: int,
    command: Union[str, Sequence[str]],
    stream_name: str,
) -> str:
    slug = _archive_slug(_format_command(command))
    return _safe_archive_path(f"commands/{index:03d}-{slug}-{stream_name}.txt")


def _archive_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "command"


def _safe_archive_path(path: str) -> str:
    if not isinstance(path, str):
        raise TypeError("support bundle archive path must be a string")
    if not path or "\x00" in path:
        raise ValueError("support bundle archive path must be non-empty")
    if "\\" in path:
        raise ValueError(f"unsafe support bundle archive path: {path}")
    if path.startswith("/"):
        raise ValueError(f"unsafe absolute support bundle archive path: {path}")
    if re.match(r"^[A-Za-z]:", path):
        raise ValueError(f"unsafe absolute support bundle archive path: {path}")

    normalized = posixpath.normpath(path)
    if normalized in {"", "."}:
        raise ValueError("support bundle archive path must name a file")
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        raise ValueError(f"unsafe path traversal in support bundle archive path: {path}")
    if normalized.startswith("/"):
        raise ValueError(f"unsafe absolute support bundle archive path: {path}")
    if normalized.endswith("/"):
        raise ValueError(f"support bundle archive path must name a file: {path}")
    return normalized


class _RedactionRun:
    def __init__(self) -> None:
        self._email_placeholders: Dict[str, str] = {}
        self._mac_placeholders: Dict[str, str] = {}
        self._user_placeholders: Dict[str, str] = {}
        self._ipv4_placeholders: Dict[str, str] = {}

    def redact_data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            redacted: Dict[Any, Any] = {}
            for key, item in value.items():
                redacted_key = self.redact_text(key) if isinstance(key, str) else key
                if is_sensitive_key(key):
                    redacted[redacted_key] = SECRET_PLACEHOLDER
                else:
                    redacted[redacted_key] = self.redact_data(item)
            return redacted
        if isinstance(value, list):
            return [self.redact_data(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_data(item) for item in value)
        return value

    def redact_text(self, text: str) -> str:
        redacted = _PRIVATE_KEY_RE.sub(PRIVATE_KEY_PLACEHOLDER, text)
        redacted = _AUTHORIZATION_RE.sub(r"\1" + AUTHORIZATION_PLACEHOLDER, redacted)
        redacted = _QUERY_TOKEN_RE.sub(r"\1" + SECRET_PLACEHOLDER, redacted)
        redacted = _INLINE_SECRET_RE.sub(r"\1" + SECRET_PLACEHOLDER, redacted)
        redacted = _LINE_SECRET_RE.sub(r"\1" + SECRET_PLACEHOLDER, redacted)
        redacted = _JSON_SECRET_RE.sub(r"\1" + '"' + SECRET_PLACEHOLDER + '"', redacted)
        redacted = _EMAIL_RE.sub(self._email_placeholder, redacted)
        redacted = _HOME_USER_RE.sub(self._home_user_placeholder, redacted)
        redacted = _MAC_RE.sub(self._mac_placeholder, redacted)
        redacted = _IPV4_RE.sub(self._ipv4_placeholder, redacted)
        return redacted

    def _email_placeholder(self, match: re.Match[str]) -> str:
        return self._stable_placeholder(self._email_placeholders, match.group(0), "email")

    def _mac_placeholder(self, match: re.Match[str]) -> str:
        value = match.group(0).lower().replace("-", ":")
        return self._stable_placeholder(self._mac_placeholders, value, "mac")

    def _home_user_placeholder(self, match: re.Match[str]) -> str:
        user = match.group("user")
        placeholder = self._stable_placeholder(self._user_placeholders, user, "user")
        return match.group("prefix") + placeholder

    def _ipv4_placeholder(self, match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return value
        if not ip.is_global:
            return value
        return self._stable_placeholder(self._ipv4_placeholders, value, "ipv4")

    @staticmethod
    def _stable_placeholder(placeholders: Dict[str, str], value: str, label: str) -> str:
        if value not in placeholders:
            placeholders[value] = f"<redacted-{label}-{len(placeholders) + 1}>"
        return placeholders[value]
