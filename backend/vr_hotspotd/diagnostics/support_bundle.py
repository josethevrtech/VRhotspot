"""Redaction helpers for future diagnostics support bundles."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict


SECRET_PLACEHOLDER = "<redacted-secret>"
AUTHORIZATION_PLACEHOLDER = "<redacted-authorization>"
PRIVATE_KEY_PLACEHOLDER = "<redacted-private-key>"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_RE = re.compile(r"(?im)(\bAuthorization\s*:\s*)[^\r\n]+")
_QUERY_TOKEN_RE = re.compile(
    r"(?i)([?&](?:api[_-]?token|access[_-]?token|auth[_-]?token|token)=)[^&\s\"']+"
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
