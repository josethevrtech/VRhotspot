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
        "btnRevealPassBasic",
        "btnShowQrBasic",
        "btnSavePassBasic",
        "btnCopySsid",
        "btnCopyPass",
        "basicPillTxt",
        "basicStatusAdapterBand",
        "btnStartBasic",
    ):
        assert control_id in source

    assert "card.querySelector('.basic-status-actions')" in source
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
    assert "observeStagingContainer(el('basicQuickFields'))" in source
    assert "observeStatusSource(el('basicStatusAdapterBand'))" not in source
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
    assert "Switch to Pro mode for details." in source


def test_status_omits_adapter_band_facts_and_decorative_notice() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "makeFact(" not in source
    assert "basicGuidedAdapterValue" not in source
    assert "basicGuidedBandValue" not in source
    assert "basic-guided-status-facts" not in source
    assert "Pending changes are saved automatically" not in source
    assert "basic-guided-apply-notice" not in source
    assert "if (originalMeta) hiddenStatus.appendChild(originalMeta);" in source


def test_empty_more_actions_disclosure_is_removed_but_controls_stay_alive() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "'More actions'" not in source
    assert "basic-guided-more-actions" not in source
    assert "if (copySsid) staging.appendChild(copySsid);" in source
    assert "if (copyPass) staging.appendChild(copyPass);" in source


def test_guided_styles_match_existing_theme_and_avoid_custom_iconography() -> None:
    styles = GUIDED_CSS.read_text(encoding="utf-8")
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert 'body[data-ui-mode="basic"] .basic-guided-step' in styles
    assert 'body[data-ui-mode="basic"] .basic-guided-status-hero' in styles
    assert 'body[data-ui-mode="basic"] .basic-guided-status-actions' in styles
    assert "background: var(--bg-panel);" in styles
    assert "border-color: var(--border-subtle);" in styles
    assert "basic-guided-card-icon" not in styles
    assert "basic-guided-status-orb" not in styles
    assert "data-guided-icon" not in source
    assert "data-profile-icon" not in source
    assert 'body[data-ui-mode="advanced"]' not in styles
    assert "zoom:" not in styles
    assert "transform: scale(" not in styles


def test_info_tips_use_one_native_circle_instead_of_a_circled_glyph() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")

    assert "make('span', 'tip basic-guided-tip', 'i')" in source
    assert "make('span', 'tip basic-guided-tip', 'ⓘ')" not in source


def test_pro_only_status_controls_stay_alive_but_are_hidden_in_basic() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")
    styles = GUIDED_CSS.read_text(encoding="utf-8")

    assert "make('details', 'basic-guided-options')" not in source
    assert "'Options'" not in source
    assert "card.querySelector('.basic-status-preferences')" in source
    assert "basicTelemetryContainer" in source
    assert "privacyHintBasic" in source
    assert "hiddenStatus.appendChild(node)" in source
    assert ".basic-guided-options" not in styles
    assert ".basic-guided-hidden-status" in styles


def test_connection_profiles_fill_the_available_width() -> None:
    styles = GUIDED_CSS.read_text(encoding="utf-8")

    assert ".basic-guided-profile-field .segmented" in styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in styles
    assert ".basic-guided-profile-field .seg span" in styles
    assert "min-height: 64px;" in styles
    assert "width: 100%;" in styles


def test_password_field_reveal_and_qr_are_locked_to_one_row() -> None:
    source = GUIDED_JS.read_text(encoding="utf-8")
    styles = GUIDED_CSS.read_text(encoding="utf-8")

    assert "passActions.classList.add('basic-guided-password-row');" in source
    assert "passActions.appendChild(passField);" in source
    assert "passActions.appendChild(revealPass);" in source
    assert "passActions.appendChild(showQr);" in source
    assert ".basic-guided-password-row #wpa2_passphrase_basic" in styles
    assert ".basic-guided-password-row #btnRevealPassBasic" in styles
    assert ".basic-guided-password-row #btnShowQrBasic" in styles
    assert "grid-template-columns: minmax(0, 1fr) 50px 50px !important;" in styles
