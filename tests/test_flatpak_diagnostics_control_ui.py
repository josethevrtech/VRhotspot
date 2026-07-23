from dataclasses import asdict
import inspect
from pathlib import Path

import pytest

from flatpak_client import (
    ApiResponse,
    DiagnosticsControlUiController,
    FirstRunResult,
    FirstRunState,
    PresentationMode,
    StatusSeverity,
)


def _response(data):
    return ApiResponse(
        correlation_id="ui-foundation-test",
        result_code="ok",
        warnings=(),
        data=data,
    )


def _readiness_response(**overrides):
    data = {
        "recommended": "wlan1",
        "basic_mode_recommended": "wlan1",
        "adapters": [
            {
                "interface": "wlan1",
                "driver": "mt7921u",
                "bus_type": "usb",
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_6ghz": False,
                "basic_mode_visibility": {
                    "visible": True,
                    "selectable": True,
                },
                "readiness_state": "good_for_vr",
                "recommendation_score": 95,
                "reason_codes": [
                    "supports_ap_mode",
                    "supports_5ghz",
                    "supports_80mhz",
                ],
                "explanation": "Strong daemon-reported VR readiness.",
            }
        ],
    }
    data.update(overrides)
    return _response(data)


def _preflight_response(**overrides):
    data = {
        "schema_version": 1,
        "overall_readiness": "ready",
        "platform": {
            "os_name": "Example Linux",
            "os_version": "1",
            "host_kind": "mutable_linux",
        },
        "firewall": {"backend": "nftables", "status": "available"},
        "services": {
            "network_manager": {"status": "active"},
            "iwd": {"status": "not_installed"},
        },
        "network": {"active_uplink_interface": "enp4s0"},
        "wifi": {"selected_adapter": "wlan1"},
        "issues": [],
        "recommended_actions": [],
    }
    data.update(overrides)
    return _response(data)


class FakeReadOnlyClient:
    def __init__(self, *, readiness=None, preflight=None):
        self.readiness = readiness or _readiness_response()
        self.preflight = preflight or _preflight_response()
        self.calls = []

    def adapter_readiness(self):
        self.calls.append("adapter_readiness")
        if isinstance(self.readiness, BaseException):
            raise self.readiness
        return self.readiness

    def preflight_report(self):
        self.calls.append("preflight_report")
        if isinstance(self.preflight, BaseException):
            raise self.preflight
        return self.preflight


def _build(state, *, client=None, mode=PresentationMode.BASIC):
    return DiagnosticsControlUiController(client).build(
        pairing_result=FirstRunResult(state),
        mode=mode,
    )


def test_daemon_unreachable_model_is_safe_and_does_not_query_sections():
    client = FakeReadOnlyClient()

    model = _build(FirstRunState.DAEMON_UNREACHABLE, client=client)

    assert model.daemon.severity is StatusSeverity.ERROR
    assert model.daemon.reachable is False
    assert model.pairing.severity is StatusSeverity.UNKNOWN
    assert model.pairing.paired is False
    assert model.adapters.severity is StatusSeverity.UNKNOWN
    assert model.preflight.severity is StatusSeverity.UNKNOWN
    assert client.calls == []


def test_reachable_without_token_is_unpaired_warning():
    client = FakeReadOnlyClient()

    model = _build(FirstRunState.DAEMON_REACHABLE_UNPAIRED, client=client)

    assert model.daemon.severity is StatusSeverity.OK
    assert model.daemon.reachable is True
    assert model.pairing.severity is StatusSeverity.WARNING
    assert model.pairing.title == "Pairing required"
    assert model.pairing.paired is False
    assert client.calls == []


def test_accepted_token_loads_only_authenticated_read_only_sections():
    client = FakeReadOnlyClient()

    model = _build(FirstRunState.TOKEN_ACCEPTED, client=client)

    assert model.daemon.severity is StatusSeverity.OK
    assert model.pairing.severity is StatusSeverity.OK
    assert model.pairing.paired is True
    assert model.adapters.severity is StatusSeverity.OK
    assert model.preflight.severity is StatusSeverity.OK
    assert client.calls == ["adapter_readiness", "preflight_report"]


def test_rejected_token_is_an_error_without_authenticated_queries():
    client = FakeReadOnlyClient()

    model = _build(FirstRunState.TOKEN_REJECTED, client=client)

    assert model.daemon.severity is StatusSeverity.OK
    assert model.pairing.severity is StatusSeverity.ERROR
    assert model.pairing.title == "Token rejected"
    assert model.pairing.detail_code == "authentication_failed"
    assert client.calls == []


def test_missing_daemon_token_is_a_blocked_pairing_state():
    client = FakeReadOnlyClient()

    model = _build(FirstRunState.DAEMON_TOKEN_MISSING, client=client)

    assert model.daemon.severity is StatusSeverity.OK
    assert model.pairing.severity is StatusSeverity.BLOCKED
    assert model.pairing.title == "Daemon token missing"
    assert model.pairing.detail_code == "api_token_missing"
    assert client.calls == []


def test_adapter_readiness_cards_preserve_daemon_recommendation_and_basic_fields():
    readiness = _readiness_response(
        adapters=[
            {
                "interface": "wlan1",
                "driver": "mt7921u",
                "bus_type": "usb",
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_6ghz": False,
                "basic_mode_visibility": {
                    "visible": True,
                    "selectable": True,
                },
                "readiness_state": "good_for_vr",
                "recommendation_score": 92,
                "reason_codes": ["supports_ap_mode", "supports_80mhz"],
                "explanation": "Recommended by the daemon.",
            },
            {
                "interface": "wlan0",
                "driver": "iwlwifi",
                "bus_type": "pci",
                "supports_2ghz": True,
                "supports_5ghz": True,
                "supports_6ghz": False,
                "basic_mode_visibility": {
                    "visible": False,
                    "selectable": False,
                },
                "readiness_state": "not_recommended",
                "recommendation_score": 40,
                "reason_codes": ["wlan0_deprioritized"],
                "explanation": "Internal adapter is deprioritized.",
            },
        ]
    )
    client = FakeReadOnlyClient(readiness=readiness)

    model = _build(FirstRunState.TOKEN_ACCEPTED, client=client)

    assert model.adapters.recommended_interface == "wlan1"
    assert model.adapters.basic_mode_recommended_interface == "wlan1"
    assert len(model.adapters.cards) == 2
    recommended, internal = model.adapters.cards
    assert recommended.interface == "wlan1"
    assert recommended.severity is StatusSeverity.OK
    assert recommended.recommended is True
    assert recommended.basic_mode_recommended is True
    assert recommended.basic_mode_visible is True
    assert recommended.basic_mode_selectable is True
    assert recommended.supported_bands == ("2.4 GHz", "5 GHz")
    assert recommended.reasons == ("Supports Ap Mode", "Supports 80mhz")
    assert internal.severity is StatusSeverity.WARNING
    assert internal.recommended is False
    assert internal.basic_mode_visible is False
    assert internal.basic_mode_selectable is False


def test_preflight_summary_issues_and_actions_have_ui_severity_mapping():
    preflight = _preflight_response(
        overall_readiness="blocked",
        issues=[
            {
                "severity": "blocked",
                "code": "no_ap_capable_adapter",
                "message": "No AP-capable adapter was found.",
            },
            {
                "severity": "warning",
                "code": "regdom_unknown",
                "message": "Regulatory domain is unknown.",
            },
            {
                "severity": "error",
                "code": "probe_failed",
                "message": "A read-only probe failed.",
            },
            {
                "severity": "future_state",
                "code": "future_issue",
                "message": "A future issue type was reported.",
            },
        ],
        recommended_actions=[
            {
                "code": "no_ap_capable_adapter",
                "message": "Connect an AP-capable Wi-Fi adapter.",
            }
        ],
    )
    client = FakeReadOnlyClient(preflight=preflight)

    model = _build(FirstRunState.TOKEN_ACCEPTED, client=client)

    assert model.preflight.severity is StatusSeverity.BLOCKED
    assert model.preflight.readiness_label == "Blocked"
    assert [issue.severity for issue in model.preflight.issues] == [
        StatusSeverity.BLOCKED,
        StatusSeverity.WARNING,
        StatusSeverity.ERROR,
        StatusSeverity.UNKNOWN,
    ]
    assert model.preflight.actions[0].interactive is False
    facts = {fact.label: fact.value for fact in model.preflight.facts}
    assert facts["Selected adapter"] == "wlan1"
    assert facts["Default route / uplink"] == "enp4s0"
    assert facts["Firewall"] == "Nftables · Available"
    assert facts["NetworkManager"] == "Active"


def test_support_bundle_affordance_is_present_but_never_performs_a_request():
    client = FakeReadOnlyClient()

    paired = _build(FirstRunState.TOKEN_ACCEPTED, client=client)
    unpaired = _build(FirstRunState.DAEMON_REACHABLE_UNPAIRED, client=client)

    assert paired.support_bundle.visible is True
    assert paired.support_bundle.action_label == "Export support bundle"
    assert paired.support_bundle.availability_code == "export_not_implemented"
    assert paired.support_bundle.action_enabled is False
    assert paired.support_bundle.requires_portal is True
    assert paired.support_bundle.request_performed is False
    assert unpaired.support_bundle.availability_code == "pairing_required"
    assert client.calls == ["adapter_readiness", "preflight_report"]


def test_secret_fields_assignments_and_absolute_paths_do_not_enter_ui_models():
    secret_token = "ui-model-secret-token"
    wifi_secret = "ui-model-wifi-passphrase"
    readiness = _readiness_response(
        api_token=secret_token,
        wpa2_passphrase=wifi_secret,
        adapters=[
            {
                "interface": "wlan1",
                "driver": "mt7921u",
                "bus_type": "usb",
                "readiness_state": "good_for_vr",
                "api_token": secret_token,
                "wpa2_passphrase": wifi_secret,
                "explanation": (
                    f"API token={secret_token}; passphrase={wifi_secret}; "
                    "details in /etc/vr-hotspot/env"
                ),
            }
        ],
    )
    preflight = _preflight_response(
        api_token=secret_token,
        environment={"VR_HOTSPOTD_API_TOKEN": secret_token},
        wifi={
            "selected_adapter": "wlan1",
            "wpa2_passphrase": wifi_secret,
        },
        issues=[
            {
                "severity": "warning",
                "code": "secret_test",
                "message": (
                    f"Authorization: Bearer {secret_token}; "
                    f"password={wifi_secret}; see /var/lib/vr-hotspot/state.json"
                ),
                "raw_environment": {"TOKEN": secret_token},
            }
        ],
        recommended_actions=[],
    )
    controller = DiagnosticsControlUiController(
        FakeReadOnlyClient(readiness=readiness, preflight=preflight)
    )

    model = controller.build(
        pairing_result=FirstRunResult(FirstRunState.TOKEN_ACCEPTED)
    )
    exposed = repr(asdict(model)) + repr(model) + repr(controller)

    assert secret_token not in exposed
    assert wifi_secret not in exposed
    assert "/etc/vr-hotspot/env" not in exposed
    assert "/var/lib/vr-hotspot/state.json" not in exposed
    assert "[redacted]" in exposed
    assert "[host path]" in exposed
    assert "raw_environment" not in exposed


@pytest.mark.parametrize(
    ("daemon_text", "forbidden_fragments"),
    [
        (
            "token abc123",
            ("abc123",),
        ),
        (
            "passphrase correct horse battery staple",
            ("correct", "horse", "battery", "staple"),
        ),
        (
            '"token": "json-token-value"',
            ("json-token-value",),
        ),
        (
            '"password": "json password value"',
            ("json", "password value"),
        ),
        (
            "password: hunter2",
            ("hunter2",),
        ),
        (
            "psk = wifi-psk-value",
            ("wifi-psk-value",),
        ),
        (
            "secret=private-secret-value; key: private-key-value",
            ("private-secret-value", "private-key-value"),
        ),
        (
            "secret open sesame; key private material",
            ("open", "sesame", "private material"),
        ),
        (
            "Open file:///home/example-user/private/config.json",
            ("file://", "/home/", "example-user", "config.json"),
        ),
        (
            "Read /etc/vr-hotspot/env and /tmp/vr-hotspot-debug.log",
            ("/etc/", "/tmp/", "vr-hotspot-debug.log"),
        ),
        (
            "state:/var/lib/vr-hotspot/state.json; socket:/run/vr-hotspotd.sock",
            ("/var/lib/", "/run/", "state.json", "vr-hotspotd.sock"),
        ),
        (
            "password mixed secret words; inspect file:///var/lib/private/state.json",
            ("mixed", "secret words", "file://", "/var/lib/", "state.json"),
        ),
    ],
)
def test_blocker_secret_and_host_path_forms_are_fully_sanitized(
    daemon_text,
    forbidden_fragments,
):
    client = FakeReadOnlyClient(
        preflight=_preflight_response(
            overall_readiness="warning",
            issues=[
                {
                    "severity": "warning",
                    "code": "sanitizer_test",
                    "message": daemon_text,
                }
            ],
        )
    )

    model = _build(FirstRunState.TOKEN_ACCEPTED, client=client)
    sanitized = model.preflight.issues[0].message

    assert all(fragment not in sanitized for fragment in forbidden_fragments)
    assert "[redacted]" in sanitized or "[host path]" in sanitized


def test_malformed_partial_and_failed_daemon_responses_degrade_to_unknown():
    secret = "must-not-escape-from-error"
    malformed = FakeReadOnlyClient(
        readiness={"adapters": [{"interface": "wlan1"}]},
        preflight=_response(
            {
                "schema_version": 1,
                "overall_readiness": "ready",
                "issues": [],
            }
        ),
    )
    failed = FakeReadOnlyClient(
        readiness=RuntimeError(f"readiness failed with {secret}"),
        preflight=RuntimeError(f"preflight failed with {secret}"),
    )

    malformed_model = _build(FirstRunState.TOKEN_ACCEPTED, client=malformed)
    failed_model = _build(FirstRunState.TOKEN_ACCEPTED, client=failed)

    assert malformed_model.adapters.severity is StatusSeverity.UNKNOWN
    assert malformed_model.adapters.cards == ()
    assert malformed_model.preflight.severity is StatusSeverity.UNKNOWN
    assert malformed_model.preflight.issues == ()
    assert malformed_model.preflight.actions == ()
    assert failed_model.adapters.severity is StatusSeverity.UNKNOWN
    assert failed_model.preflight.severity is StatusSeverity.UNKNOWN
    assert secret not in repr(failed_model)


def test_daemon_content_and_collections_are_bounded():
    long_text = "x" * 1_000
    adapters = [
        {
            "interface": f"wlan{index}",
            "readiness_state": "warning",
            "reason_codes": [f"reason_{reason}" for reason in range(20)],
            "explanation": long_text,
        }
        for index in range(30)
    ]
    issues = [
        {"severity": "warning", "code": f"issue_{index}", "message": long_text}
        for index in range(30)
    ]
    actions = [
        {"code": f"action_{index}", "message": long_text}
        for index in range(30)
    ]
    client = FakeReadOnlyClient(
        readiness=_readiness_response(adapters=adapters),
        preflight=_preflight_response(
            overall_readiness="warning",
            issues=issues,
            recommended_actions=actions,
        ),
    )

    model = _build(FirstRunState.TOKEN_ACCEPTED, client=client)

    assert len(model.adapters.cards) == 12
    assert len(model.adapters.cards[0].reasons) == 6
    assert len(model.adapters.cards[0].summary) == 240
    assert model.adapters.cards[0].summary.endswith("…")
    assert len(model.preflight.issues) == 16
    assert len(model.preflight.actions) == 12
    assert len(model.preflight.issues[0].message) == 240
    assert len(model.preflight.actions[0].message) == 240


def test_basic_and_pro_modes_change_presentation_depth_not_daemon_policy():
    basic = _build(FirstRunState.DAEMON_REACHABLE_UNPAIRED)
    pro = _build(
        FirstRunState.DAEMON_REACHABLE_UNPAIRED,
        mode=PresentationMode.PRO,
    )

    assert basic.mode is PresentationMode.BASIC
    assert basic.show_technical_details is False
    assert pro.mode is PresentationMode.PRO
    assert pro.show_technical_details is True
    assert basic.pairing == pro.pairing
    assert basic.adapters == pro.adapters
    assert basic.preflight == pro.preflight


def test_controller_exposes_no_mutation_or_generic_request_methods():
    public_methods = {
        name
        for name, value in inspect.getmembers(
            DiagnosticsControlUiController,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert public_methods == {"build"}
    assert public_methods.isdisjoint(
        {
            "start",
            "stop",
            "restart",
            "repair",
            "save_config",
            "update_config",
            "request",
            "post",
            "support_bundle",
            "export",
        }
    )


def test_ui_foundation_has_no_direct_network_host_or_secret_source_access():
    source = Path("flatpak_client/ui.py").read_text(encoding="utf-8")

    for forbidden in (
        "import os",
        "import socket",
        "import subprocess",
        "import urllib",
        "import requests",
        "os.environ",
        "Path(",
    ):
        assert forbidden not in source
