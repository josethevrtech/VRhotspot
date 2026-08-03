from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDED_JS = ROOT / "assets" / "basic_guided.js"
GUIDED_CSS = ROOT / "assets" / "basic_guided.css"
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_guided_basic_assets_are_loaded_and_served() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")
    api = DEVHUB_API.read_text(encoding="utf-8")

    assert "loadGuidedBasicModeAssets" in loader
    assert "'/assets/basic_guided.css'" in loader
    assert "'/assets/basic_guided.js'" in loader
    assert '"/assets/basic_guided.css": "text/css; charset=utf-8"' in api
    assert '"/assets/basic_guided.js": "application/javascript; charset=utf-8"' in api


def test_guided_interface_reuses_existing_live_control_ids() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    for control_id in (
        "basicQuickFields",
        "basicConnectFields",
        "wpa2_passphrase_basic",
        "btnSavePassBasic",
        "btnCopySsid",
        "btnCopyPass",
        "basicPillTxt",
        "basicStatusAdapterBand",
        "btnStartBasic",
        "btnStopBasic",
        "btnRepairBasic",
        "btnRefreshBasic",
        "privacyModeBasic",
        "autoRefreshBasic",
        "showTelemetryBasic",
    ):
        assert control_id in source

    assert "api(" not in source
    assert "fetch(" not in source


def test_guided_setup_uses_four_plain_language_steps() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "'Set Up Hotspot'" in source
    assert "'Choose Wi-Fi adapter'" in source
    assert "'Choose connection profile'" in source
    assert "'Hotspot name (SSID)'" in source
    assert "'Password (Passphrase)'" in source
    assert "basicGuidedAdapterSlot" in source
    assert "basicGuidedProfileSlot" in source
    assert "basicGuidedSsidSlot" in source
    assert "basicGuidedPassSlot" in source


def test_technical_basic_defaults_remain_live_but_hidden() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")
    styles = GUIDED_CSS.read_text(encoding="utf-8")

    for field in (
        "band_preference",
        "ap_security",
        "country",
        "enable_internet",
    ):
        assert f"'{field}'" in source

    assert "basicGuidedTechnicalDefaults" in source
    assert ".basic-guided-technical-defaults" in styles
    assert "display: none !important;" in styles


def test_status_presentation_is_idempotent_and_source_scoped() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "function setTextIfChanged" in source
    assert "observeStatusSource(el('basicPillTxt'))" in source
    assert "observeStatusSource(el('basicStatusAdapterBand'))" in source
    assert "observeStagingContainer(el('basicQuickFields'))" in source
    assert "observer.observe(basic" not in source


def test_start_saves_pending_basic_changes_before_existing_start_handler() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "function wireSaveBeforeStart" in source
    assert "event.stopImmediatePropagation();" in source
    assert "const saveButton = el('btnSaveConfig');" in source
    assert "saveButton.click();" in source
    assert "await waitForBasicSave();" in source
    assert "target.dataset.guidedResume = '1';" in source
    assert "target.click();" in source
    assert "Pending changes are saved automatically when you start the hotspot." in source


def test_status_uses_friendly_adapter_labels_and_hides_technical_details() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "function adapterLabelForValue" in source
    assert ".find((option) => option.value === value)" in source
    assert "if (statusDetails) optionsBody.appendChild(statusDetails);" in source
    assert "if (statusDetails) diagnosticDetails.appendChild(statusDetails);" not in source


def test_guided_styles_are_basic_only_and_use_native_sizing() -> None:
    styles = GUIDED_CSS.read_text(encoding="utf-8")

    assert 'body[data-ui-mode="basic"] .basic-guided-step' in styles
    assert 'body[data-ui-mode="basic"] .basic-guided-status-hero' in styles
    assert 'body[data-ui-mode="basic"] .basic-guided-status-actions' in styles
    assert 'body[data-ui-mode="advanced"]' not in styles
    assert "zoom:" not in styles
    assert "transform: scale(" not in styles


def test_secondary_controls_are_preserved_in_collapsed_options() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "make('details', 'basic-guided-options')" in source
    assert "'Options'" in source
    assert "basic-status-preferences" in source
    assert "basicTelemetryContainer" in source
    assert "privacyHintBasic" in source
