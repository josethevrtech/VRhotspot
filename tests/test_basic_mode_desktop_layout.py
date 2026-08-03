from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASIC_LAYOUT = ROOT / "assets" / "basic_layout.css"
WIZARD_LAYOUT = ROOT / "assets" / "devhub_connection_wizard.css"
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
CORE_UI = ROOT / "assets" / "ui.css"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_wide_basic_mode_reuses_two_column_grid_after_readiness_removal() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")

    assert '@media (min-width: 1400px)' in layout
    assert 'body[data-ui-mode="basic"] .basic-grid' in layout
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in layout


def test_basic_desktop_uses_comfortable_native_density_without_page_scaling() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")

    assert '@media (min-width: 1200px)' in layout
    assert 'body[data-ui-mode="basic"] .basic-container' in layout
    assert "max-width: 2480px;" in layout
    assert 'body[data-ui-mode="basic"] .basic-card .card-header' in layout
    assert "padding: 24px 30px;" in layout
    assert 'body[data-ui-mode="basic"] .basic-card .card-body' in layout
    assert "padding: 30px;" in layout
    assert "min-height: 50px;" in layout
    assert 'body[data-ui-mode="basic"] [data-ui-section="basic"] .btn' in layout
    assert "height: 48px;" in layout
    assert "font-size: 17px;" in layout


def test_pro_desktop_uses_comfortable_native_density() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")

    assert 'body[data-ui-mode="advanced"] .sidebar' in layout
    assert "width: 300px;" in layout
    assert 'body[data-ui-mode="advanced"] .content-area' in layout
    assert 'body[data-ui-mode="advanced"] .card-body' in layout
    assert "padding: 28px;" in layout
    assert 'body[data-ui-mode="advanced"] .btn' in layout
    assert "height: 46px;" in layout
    assert 'body[data-ui-mode="advanced"] .grid-overview' in layout
    assert "max-width: 1100px;" in layout
    assert 'body[data-ui-mode="advanced"] .preflight-page' in layout
    assert "width: min(100%, 1400px);" in layout


def test_pro_density_covers_developer_hub() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")

    assert '#tab-devhub .devhub-page' in layout
    assert '#tab-devhub .devhub-device-bar' in layout
    assert '#tab-devhub .devhub-workspace-tab' in layout
    assert '#tab-devhub .devhub-overview-metric' in layout
    assert '#tab-devhub .devhub-file-drop' in layout
    assert '#tab-devhub .devhub-target-device-display' in layout


def test_pro_density_covers_body_mounted_wireless_wizard() -> None:
    wizard = WIZARD_LAYOUT.read_text(encoding="utf-8")

    assert "The wizard is appended to document.body" in wizard
    assert 'body[data-ui-mode="advanced"] .devhub-wizard-dialog' in wizard
    assert "width: min(980px, 100%);" in wizard
    assert 'body[data-ui-mode="advanced"] .devhub-wizard-body' in wizard
    assert "min-height: 320px;" in wizard
    assert 'body[data-ui-mode="advanced"] .devhub-wizard-device select' in wizard
    assert "min-height: 48px;" in wizard


def test_desktop_density_uses_native_component_sizing_not_page_scaling() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")
    wizard = WIZARD_LAYOUT.read_text(encoding="utf-8")

    assert "zoom:" not in layout
    assert "transform: scale(" not in layout
    assert "zoom:" not in wizard
    assert "transform: scale(" not in wizard


def test_existing_responsive_container_and_mobile_stack_remain_intact() -> None:
    core = CORE_UI.read_text(encoding="utf-8")

    assert "max-width: 1720px;" in core
    assert "@media (max-width: 760px)" in core
    assert "grid-template-columns: minmax(0, 1fr);" in core


def test_desktop_layout_asset_is_loaded_and_served() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")
    api = DEVHUB_API.read_text(encoding="utf-8")

    assert "loadBasicModeLayout" in loader
    assert "'/assets/basic_layout.css'" in loader
    assert '"/assets/basic_layout.css": "text/css; charset=utf-8"' in api


def test_adapter_readiness_removal_and_core_pro_dom_contracts_remain() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")
    core = CORE_UI.read_text(encoding="utf-8")

    assert "removeBasicAdapterReadinessCard" in loader
    assert "[data-adapter-readiness-card]" in loader
    assert ".remove()" in loader
    assert ".advanced-layout" in core
    assert ".sidebar" in core
    assert ".content-area" in core
