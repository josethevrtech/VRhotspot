"""Toolkit-agnostic UI models for the future Flatpak control app."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Any, Mapping, Protocol, Sequence, Tuple

from .client import ApiResponse
from .pairing import FirstRunResult, FirstRunState


_MAX_ADAPTER_CARDS = 12
_MAX_REASONS_PER_ADAPTER = 6
_MAX_PREFLIGHT_ISSUES = 16
_MAX_PREFLIGHT_ACTIONS = 12
_MAX_TEXT_CHARS = 240
_MAX_CODE_CHARS = 64
_MAX_SANITIZER_INPUT_CHARS = 4_096

_SECRET_KEY_PATTERN = (
    r"x-api-token|"
    r"(?:api|access|auth|client|private)[_ -]?(?:token|secret|key)|"
    r"secret[_ -]?key|token|authorization|"
    r"wpa2?[_ -]?passphrase|passphrase|sae[_ -]?password|password|"
    r"psk|secret|key"
)
_SECRET_VALUE_PATTERN = (
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,;]+)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i)(?<![\w-])(?P<quote>[\"']?)(?P<key>{_SECRET_KEY_PATTERN})"
    rf"(?P=quote)(?![\w-])\s*[:=]\s*(?:bearer\s+)?{_SECRET_VALUE_PATTERN}"
)
_SPACED_SECRET_RE = re.compile(
    rf"(?i)(?<![\w-])(?P<key>{_SECRET_KEY_PATTERN})"
    rf"(?![\w-])\s+(?:bearer\s+)?{_SECRET_VALUE_PATTERN}"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_FILE_URI_RE = re.compile(
    r"(?i)(?<![\w])file:(?://)?/[^\s,;!?)}\]>'\"]*"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w/])/(?:[^/\s]+/)*[^/\s,;:!?)}\]>'\"]*"
)
_SAFE_CODE_RE = re.compile(r"[^a-z0-9_.-]+")


class StatusSeverity(str, Enum):
    """Presentation severity shared by the future desktop UI."""

    OK = "ok"
    WARNING = "warning"
    BLOCKED = "blocked"
    ERROR = "error"
    UNKNOWN = "unknown"


class PresentationMode(str, Enum):
    """Presentation depth only; daemon policy remains authoritative."""

    BASIC = "basic"
    PRO = "pro"


@dataclass(frozen=True)
class DaemonStatusModel:
    """Safe local-daemon connection status derived from pairing state."""

    severity: StatusSeverity
    reachable: bool | None
    title: str
    message: str
    detail_code: str


@dataclass(frozen=True)
class PairingStatusModel:
    """Safe authentication status with no credential-bearing fields."""

    severity: StatusSeverity
    paired: bool
    title: str
    message: str
    detail_code: str


@dataclass(frozen=True)
class AdapterReadinessCard:
    """One bounded projection of daemon-owned adapter readiness."""

    interface: str
    severity: StatusSeverity
    readiness_label: str
    summary: str
    recommended: bool
    basic_mode_recommended: bool
    basic_mode_visible: bool | None
    basic_mode_selectable: bool | None
    driver: str
    bus_type: str
    supported_bands: Tuple[str, ...]
    recommendation_score: int | None
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class AdapterReadinessModel:
    """UI-ready adapter section without client-side selection policy."""

    severity: StatusSeverity
    title: str
    summary: str
    recommended_interface: str
    basic_mode_recommended_interface: str
    cards: Tuple[AdapterReadinessCard, ...]


@dataclass(frozen=True)
class SummaryFact:
    """A concise allowlisted label/value pair for a diagnostics summary."""

    label: str
    value: str


@dataclass(frozen=True)
class PreflightIssueModel:
    """A bounded issue reported by the daemon."""

    severity: StatusSeverity
    code: str
    message: str


@dataclass(frozen=True)
class PreflightActionModel:
    """Non-interactive guidance; it cannot invoke a host mutation."""

    code: str
    message: str
    interactive: bool = False


@dataclass(frozen=True)
class PreflightSummaryModel:
    """Safe projection of the canonical preflight report."""

    severity: StatusSeverity
    readiness_label: str
    summary: str
    facts: Tuple[SummaryFact, ...]
    issues: Tuple[PreflightIssueModel, ...]
    actions: Tuple[PreflightActionModel, ...]


@dataclass(frozen=True)
class SupportBundleAffordance:
    """Disabled export affordance for separately reviewed future wiring."""

    visible: bool
    severity: StatusSeverity
    title: str
    summary: str
    action_label: str
    availability_code: str
    action_enabled: bool = False
    requires_portal: bool = True
    request_performed: bool = False


@dataclass(frozen=True)
class DiagnosticsControlUiModel:
    """Complete read-only foundation consumed by a future GUI toolkit."""

    mode: PresentationMode
    show_technical_details: bool
    daemon: DaemonStatusModel
    pairing: PairingStatusModel
    adapters: AdapterReadinessModel
    preflight: PreflightSummaryModel
    support_bundle: SupportBundleAffordance


class DiagnosticsUiClient(Protocol):
    """Only the existing read-only client methods needed by this foundation."""

    def adapter_readiness(self) -> ApiResponse:
        """Return the daemon-owned adapter readiness response."""

    def preflight_report(self) -> ApiResponse:
        """Return the daemon-owned canonical preflight response."""


def _sanitize_text(
    value: object,
    *,
    fallback: str,
    limit: int = _MAX_TEXT_CHARS,
) -> str:
    if not isinstance(value, (str, int, float)):
        return fallback
    if isinstance(value, float) and not math.isfinite(value):
        return fallback
    text = " ".join(str(value)[:_MAX_SANITIZER_INPUT_CHARS].split())
    if not text:
        return fallback
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}=[redacted]",
        text,
    )
    text = _SPACED_SECRET_RE.sub(
        lambda match: f"{match.group('key')}=[redacted]",
        text,
    )
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _FILE_URI_RE.sub("[host path]", text)
    text = _ABSOLUTE_PATH_RE.sub("[host path]", text)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _safe_code(value: object, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    code = _SAFE_CODE_RE.sub("_", value.strip().lower()).strip("_.-")
    if not code:
        return fallback
    return code[:_MAX_CODE_CHARS]


def _label_from_code(value: object, *, fallback: str) -> str:
    code = _safe_code(value, fallback="")
    if not code:
        return fallback
    return " ".join(part.capitalize() for part in code.replace(".", "_").split("_"))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object, *, limit: int) -> Tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value[:limit] if isinstance(item, Mapping))


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _bounded_score(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return max(-1_000, min(1_000, int(value)))


_READINESS_SEVERITIES = {
    "ready": StatusSeverity.OK,
    "good_for_vr": StatusSeverity.OK,
    "warning": StatusSeverity.WARNING,
    "usable_with_limitations": StatusSeverity.WARNING,
    "not_recommended": StatusSeverity.WARNING,
    "blocked": StatusSeverity.BLOCKED,
    "unsupported": StatusSeverity.BLOCKED,
    "error": StatusSeverity.ERROR,
}

_READINESS_LABELS = {
    "ready": "Ready",
    "good_for_vr": "Ready for VR",
    "warning": "Needs attention",
    "usable_with_limitations": "Usable with limitations",
    "not_recommended": "Not recommended",
    "blocked": "Blocked",
    "unsupported": "Unsupported",
    "error": "Error",
}


def _severity_for_readiness(value: object) -> StatusSeverity:
    code = _safe_code(value, fallback="")
    return _READINESS_SEVERITIES.get(code, StatusSeverity.UNKNOWN)


def _readiness_label(value: object) -> str:
    code = _safe_code(value, fallback="")
    return _READINESS_LABELS.get(code, "Unknown")


def _pairing_models(
    result: FirstRunResult,
) -> tuple[DaemonStatusModel, PairingStatusModel]:
    state = result.state
    if state is FirstRunState.DAEMON_UNREACHABLE:
        return (
            DaemonStatusModel(
                severity=StatusSeverity.ERROR,
                reachable=False,
                title="Daemon unavailable",
                message="The local VRhotspot daemon could not be reached.",
                detail_code="connection_failed",
            ),
            PairingStatusModel(
                severity=StatusSeverity.UNKNOWN,
                paired=False,
                title="Pairing unavailable",
                message="Pairing cannot be checked until the daemon is reachable.",
                detail_code="daemon_unreachable",
            ),
        )
    if state is FirstRunState.DAEMON_REACHABLE_UNPAIRED:
        return (
            DaemonStatusModel(
                severity=StatusSeverity.OK,
                reachable=True,
                title="Daemon reachable",
                message="The local VRhotspot daemon is responding.",
                detail_code="health_check_succeeded",
            ),
            PairingStatusModel(
                severity=StatusSeverity.WARNING,
                paired=False,
                title="Pairing required",
                message="Enter an API token to view authenticated diagnostics.",
                detail_code="token_required",
            ),
        )
    if state is FirstRunState.TOKEN_ACCEPTED:
        return (
            DaemonStatusModel(
                severity=StatusSeverity.OK,
                reachable=True,
                title="Daemon connected",
                message="The local daemon accepted authenticated read-only access.",
                detail_code="authenticated_read_only_check_succeeded",
            ),
            PairingStatusModel(
                severity=StatusSeverity.OK,
                paired=True,
                title="Paired",
                message="The supplied API token was accepted.",
                detail_code="token_accepted",
            ),
        )
    if state is FirstRunState.TOKEN_REJECTED:
        return (
            DaemonStatusModel(
                severity=StatusSeverity.OK,
                reachable=True,
                title="Daemon reachable",
                message="The local VRhotspot daemon is responding.",
                detail_code="health_check_succeeded",
            ),
            PairingStatusModel(
                severity=StatusSeverity.ERROR,
                paired=False,
                title="Token rejected",
                message="The supplied API token was rejected.",
                detail_code="authentication_failed",
            ),
        )
    if state is FirstRunState.DAEMON_TOKEN_MISSING:
        return (
            DaemonStatusModel(
                severity=StatusSeverity.OK,
                reachable=True,
                title="Daemon reachable",
                message="The local VRhotspot daemon is responding.",
                detail_code="health_check_succeeded",
            ),
            PairingStatusModel(
                severity=StatusSeverity.BLOCKED,
                paired=False,
                title="Daemon token missing",
                message="The daemon administrator must configure API authentication.",
                detail_code="api_token_missing",
            ),
        )
    return (
        DaemonStatusModel(
            severity=StatusSeverity.UNKNOWN,
            reachable=None,
            title="Daemon status unknown",
            message="The daemon returned an invalid or unsupported response.",
            detail_code="unexpected_daemon_response",
        ),
        PairingStatusModel(
            severity=StatusSeverity.UNKNOWN,
            paired=False,
            title="Pairing status unknown",
            message="Pairing status could not be determined safely.",
            detail_code="unexpected_daemon_response",
        ),
    )


def _unknown_adapters(message: str) -> AdapterReadinessModel:
    return AdapterReadinessModel(
        severity=StatusSeverity.UNKNOWN,
        title="Adapter readiness unknown",
        summary=message,
        recommended_interface="Not reported",
        basic_mode_recommended_interface="Not reported",
        cards=(),
    )


def _supported_bands(adapter: Mapping[str, Any]) -> Tuple[str, ...]:
    fields = (
        ("supports_2ghz", "2.4 GHz"),
        ("supports_5ghz", "5 GHz"),
        ("supports_6ghz", "6 GHz"),
    )
    return tuple(label for field, label in fields if adapter.get(field) is True)


def _adapter_card(
    adapter: Mapping[str, Any],
    *,
    recommended: str,
    basic_recommended: str,
) -> AdapterReadinessCard:
    interface = _sanitize_text(
        adapter.get("interface"),
        fallback="Unknown adapter",
        limit=80,
    )
    readiness = adapter.get("readiness_state")
    visibility = _mapping(adapter.get("basic_mode_visibility"))
    reason_codes = adapter.get("reason_codes")
    if not isinstance(reason_codes, Sequence) or isinstance(
        reason_codes, (str, bytes, bytearray)
    ):
        reason_codes = ()
    reasons = tuple(
        _label_from_code(reason, fallback="Unknown reason")
        for reason in reason_codes[:_MAX_REASONS_PER_ADAPTER]
    )
    return AdapterReadinessCard(
        interface=interface,
        severity=_severity_for_readiness(readiness),
        readiness_label=_readiness_label(readiness),
        summary=_sanitize_text(
            adapter.get("explanation"),
            fallback="No readiness explanation was reported.",
        ),
        recommended=bool(recommended and interface == recommended),
        basic_mode_recommended=bool(
            basic_recommended and interface == basic_recommended
        ),
        basic_mode_visible=_optional_bool(visibility.get("visible")),
        basic_mode_selectable=_optional_bool(visibility.get("selectable")),
        driver=_sanitize_text(adapter.get("driver"), fallback="Not reported", limit=80),
        bus_type=_label_from_code(adapter.get("bus_type"), fallback="Not reported"),
        supported_bands=_supported_bands(adapter),
        recommendation_score=_bounded_score(adapter.get("recommendation_score")),
        reasons=reasons,
    )


def _adapter_model(response: object) -> AdapterReadinessModel:
    if (
        not isinstance(response, ApiResponse)
        or response.result_code != "ok"
        or not isinstance(response.data, Mapping)
    ):
        return _unknown_adapters(
            "Adapter readiness could not be interpreted safely."
        )

    data = response.data
    recommended = _sanitize_text(
        data.get("recommended"),
        fallback="",
        limit=80,
    )
    basic_recommended = _sanitize_text(
        data.get("basic_mode_recommended"),
        fallback="",
        limit=80,
    )
    cards = tuple(
        _adapter_card(
            adapter,
            recommended=recommended,
            basic_recommended=basic_recommended,
        )
        for adapter in _records(data.get("adapters"), limit=_MAX_ADAPTER_CARDS)
    )

    summary_data = _mapping(data.get("summary"))
    summary_state: object = summary_data.get("readiness_state")
    if not summary_state and recommended:
        summary_state = next(
            (
                card.readiness_label
                for card in cards
                if card.interface == recommended
            ),
            "",
        )
    if summary_state in _READINESS_LABELS.values():
        summary_severity = next(
            (
                card.severity
                for card in cards
                if card.interface == recommended
            ),
            StatusSeverity.UNKNOWN,
        )
        summary_label = str(summary_state)
    else:
        summary_severity = _severity_for_readiness(summary_state)
        summary_label = _readiness_label(summary_state)

    if not cards:
        summary = "No adapter readiness cards were reported."
    elif recommended:
        summary = f"{recommended} is the daemon-recommended adapter."
    else:
        summary = "The daemon did not report a recommended adapter."

    return AdapterReadinessModel(
        severity=summary_severity,
        title=f"Adapter readiness: {summary_label}",
        summary=summary,
        recommended_interface=recommended or "Not reported",
        basic_mode_recommended_interface=basic_recommended or "Not reported",
        cards=cards,
    )


_PREFLIGHT_LABELS = {
    "ready": "Ready",
    "warning": "Needs attention",
    "blocked": "Blocked",
    "error": "Error",
}


def _unknown_preflight(message: str) -> PreflightSummaryModel:
    return PreflightSummaryModel(
        severity=StatusSeverity.UNKNOWN,
        readiness_label="Unknown",
        summary=message,
        facts=(),
        issues=(),
        actions=(),
    )


def _fact(label: str, value: object) -> SummaryFact:
    return SummaryFact(
        label=label,
        value=_sanitize_text(value, fallback="Not reported"),
    )


def _service_status(value: object) -> str:
    service = _mapping(value)
    status = service.get("status")
    if isinstance(status, str) and status.strip():
        return _label_from_code(status, fallback="Not reported")
    if service.get("active") is True:
        return "Active"
    if service.get("present") is True:
        return "Present, inactive"
    if service.get("present") is False:
        return "Not installed"
    return "Not reported"


def _platform_summary(value: object) -> str:
    platform = _mapping(value)
    parts = tuple(
        _sanitize_text(platform.get(field), fallback="", limit=80)
        for field in ("os_name", "os_version", "host_kind")
    )
    reported = tuple(part for part in parts if part)
    return " · ".join(reported) if reported else "Not reported"


def _firewall_summary(value: object) -> str:
    firewall = _mapping(value)
    parts = tuple(
        _label_from_code(firewall.get(field), fallback="")
        for field in ("backend", "status")
    )
    reported = tuple(part for part in parts if part)
    return " · ".join(reported) if reported else "Not reported"


def _preflight_model(response: object) -> PreflightSummaryModel:
    if (
        not isinstance(response, ApiResponse)
        or response.result_code != "ok"
        or not isinstance(response.data, Mapping)
    ):
        return _unknown_preflight(
            "Preflight diagnostics could not be interpreted safely."
        )

    report = response.data
    schema_version = report.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        return _unknown_preflight(
            "The daemon returned an unsupported preflight report."
        )
    readiness = _safe_code(report.get("overall_readiness"), fallback="")
    if readiness not in {"ready", "warning", "blocked"}:
        return _unknown_preflight(
            "The daemon returned an unknown preflight readiness state."
        )
    if not isinstance(report.get("issues"), list) or not isinstance(
        report.get("recommended_actions"), list
    ):
        return _unknown_preflight(
            "The daemon returned a partial preflight report."
        )

    severity = _READINESS_SEVERITIES[readiness]
    summary_messages = {
        "ready": "The daemon reported no blocking preflight issues.",
        "warning": "The daemon reported preflight items that need attention.",
        "blocked": "The daemon reported issues that block hotspot readiness.",
    }
    platform = _mapping(report.get("platform"))
    firewall = _mapping(report.get("firewall"))
    services = _mapping(report.get("services"))
    network = _mapping(report.get("network"))
    wifi = _mapping(report.get("wifi"))
    facts = (
        _fact("Selected adapter", wifi.get("selected_adapter")),
        _fact("Default route / uplink", network.get("active_uplink_interface")),
        _fact("Platform", _platform_summary(platform)),
        _fact("Firewall", _firewall_summary(firewall)),
        _fact("NetworkManager", _service_status(services.get("network_manager"))),
        _fact("iwd", _service_status(services.get("iwd"))),
    )

    issues = tuple(
        PreflightIssueModel(
            severity=_severity_for_readiness(issue.get("severity")),
            code=_safe_code(issue.get("code"), fallback="unknown_issue"),
            message=_sanitize_text(
                issue.get("message"),
                fallback="No issue description was reported.",
            ),
        )
        for issue in _records(
            report.get("issues"),
            limit=_MAX_PREFLIGHT_ISSUES,
        )
    )
    actions = tuple(
        PreflightActionModel(
            code=_safe_code(action.get("code"), fallback="recommended_action"),
            message=_sanitize_text(
                action.get("message"),
                fallback="Review the daemon-reported preflight issue.",
            ),
        )
        for action in _records(
            report.get("recommended_actions"),
            limit=_MAX_PREFLIGHT_ACTIONS,
        )
    )
    return PreflightSummaryModel(
        severity=severity,
        readiness_label=_PREFLIGHT_LABELS[readiness],
        summary=summary_messages[readiness],
        facts=facts,
        issues=issues,
        actions=actions,
    )


def _support_bundle_affordance(paired: bool) -> SupportBundleAffordance:
    if paired:
        summary = (
            "Daemon-produced redacted bundles will be exportable after "
            "bounded download and portal wiring are reviewed."
        )
        availability_code = "export_not_implemented"
    else:
        summary = (
            "Pair with the local daemon before support-bundle export can "
            "be offered."
        )
        availability_code = "pairing_required"
    return SupportBundleAffordance(
        visible=True,
        severity=StatusSeverity.UNKNOWN,
        title="Support bundle",
        summary=summary,
        action_label="Export support bundle",
        availability_code=availability_code,
    )


class DiagnosticsControlUiController:
    """Build safe read-only UI models from pairing and local API results."""

    def __init__(self, client: DiagnosticsUiClient | None = None):
        self._client = client

    def __repr__(self) -> str:
        return (
            "DiagnosticsControlUiController("
            f"read_only_client_configured={self._client is not None!r})"
        )

    def build(
        self,
        *,
        pairing_result: FirstRunResult,
        mode: PresentationMode = PresentationMode.BASIC,
    ) -> DiagnosticsControlUiModel:
        """Collect only authenticated read-only sections after successful pairing."""

        if not isinstance(mode, PresentationMode):
            mode = PresentationMode.BASIC
        if not isinstance(pairing_result, FirstRunResult):
            pairing_result = FirstRunResult(FirstRunState.INVALID_RESPONSE)

        daemon, pairing = _pairing_models(pairing_result)
        adapters = _unknown_adapters(
            "Pair with the local daemon to view adapter readiness."
        )
        preflight = _unknown_preflight(
            "Pair with the local daemon to view preflight diagnostics."
        )

        if pairing.paired and self._client is not None:
            try:
                adapters = _adapter_model(self._client.adapter_readiness())
            except Exception:
                adapters = _unknown_adapters(
                    "Adapter readiness is temporarily unavailable."
                )
            try:
                preflight = _preflight_model(self._client.preflight_report())
            except Exception:
                preflight = _unknown_preflight(
                    "Preflight diagnostics are temporarily unavailable."
                )

        return DiagnosticsControlUiModel(
            mode=mode,
            show_technical_details=mode is PresentationMode.PRO,
            daemon=daemon,
            pairing=pairing,
            adapters=adapters,
            preflight=preflight,
            support_bundle=_support_bundle_affordance(pairing.paired),
        )
