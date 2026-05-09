"""Pure helpers for future diagnostics support bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Union

from vr_hotspotd import __version__


SECRET_PLACEHOLDER = "<redacted-secret>"
AUTHORIZATION_PLACEHOLDER = "<redacted-authorization>"
PRIVATE_KEY_PLACEHOLDER = "<redacted-private-key>"
SUPPORT_BUNDLE_SCHEMA_VERSION = 1


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
        return item


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


def redact_support_bundle_text(text: str) -> str:
    """Return redacted support-bundle text with stable placeholders per call."""
    return _RedactionRun().redact_text(text)


def redact_support_bundle_data(value: Any) -> Any:
    """Return a redacted copy of nested support-bundle data."""
    return _RedactionRun().redact_data(value)


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
