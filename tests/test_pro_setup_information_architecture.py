from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "field_visibility.js"


def test_pro_navigation_unifies_overview_and_settings() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "buildProSetupInformationArchitecture" in source
    assert "'Set Up Hotspot'" in source
    assert "'Support & Logs'" in source
    assert "settingsNav.remove()" in source
    assert "shell.append(statusCard, configuration)" in source
    assert "settings.hidden = true" in source


def test_pro_configuration_reuses_the_existing_settings_dom() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "settingsPane.querySelector('.settings-header')" in source
    assert "configuration.appendChild(header)" in source
    for control in (
        "ap_adapter",
        "ssid",
        "wpa2_passphrase",
        "qos_preset",
        "enable_internet",
    ):
        assert control in source

    assert "Hotspot Configuration" in source
    assert "Essentials" in source
    assert "Wireless" in source
    assert "Network" in source
    assert "System & Performance" in source


def test_service_status_uses_one_state_aware_primary_action() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "proServiceAction" in source
    assert "Stop Hotspot" in source
    assert "Start Hotspot" in source
    # Transitional states mirror the exact lifecycle action instead of a
    # generic Working… label.
    assert "'Starting…'" in source
    assert "'Stopping…'" in source
    assert "'Restarting…'" in source
    assert "'Repairing…'" in source
    assert "Working…" not in source
    assert "const stop = el('btnStop')" in source
    assert "if (stop) stop.click()" in source
    assert "btnRestart" in source
    assert "btnRepair" in source


def test_refresh_controls_are_hidden_but_adaptive_polling_remains_live() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "configureAdaptivePolling" in source
    assert "ACTIVE_POLL_MS = '2000'" in source
    assert "BACKGROUND_POLL_MS = '5000'" in source
    assert "document.addEventListener('visibilitychange', configureAdaptivePolling)" in source
    assert "auto.dispatchEvent(new Event('change'" in source
    assert "every.dispatchEvent(new Event('change'" in source
    assert ".hero-quick-controls" in source


def test_telemetry_interval_moves_with_telemetry_controls() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "moveTelemetrySettings" in source
    assert "telemetry_interval_s" in source
    assert "settingsGroup.appendChild(interval)" in source


def test_runtime_details_and_support_bundle_move_to_logs() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "buildSupportAndLogs" in source
    assert "Runtime Details" in source
    assert "Support Bundle" in source
    assert "debugCard" in source
    assert "supportBundle" in source


def test_basic_info_tip_keeps_one_visible_circle() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert 'body[data-ui-mode="basic"] .basic-guided-tip' in source
    assert "border-color: var(--accent-primary) !important" in source
    assert "background: rgba(0, 217, 255, .08) !important" in source
    assert "zoom:" not in source
    assert "transform: scale(" not in source
