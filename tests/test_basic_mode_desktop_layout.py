from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASIC_LAYOUT = ROOT / "assets" / "basic_layout.css"
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
CORE_UI = ROOT / "assets" / "ui.css"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_wide_basic_mode_reuses_two_column_grid_after_readiness_removal() -> None:
    layout = BASIC_LAYOUT.read_text(encoding="utf-8")

    assert '@media (min-width: 1400px)' in layout
    assert 'body[data-ui-mode="basic"] .basic-grid' in layout
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in layout
    assert ".advanced-layout" not in layout
    assert ".content-area" not in layout


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
    assert "zoom:" not in layout
    assert "transform: scale(" not in layout


def test_existing_responsive_container_and_mobile_stack_remain_intact() -> None:
    core = CORE_UI.read_text(encoding="utf-8")

    assert "max-width: 1720px;" in core
    assert "@media (max-width: 760px)" in core
    assert "grid-template-columns: minmax(0, 1fr);" in core


def test_basic_layout_asset_is_loaded_and_served() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")
    api = DEVHUB_API.read_text(encoding="utf-8")

    assert "loadBasicModeLayout" in loader
    assert "'/assets/basic_layout.css'" in loader
    assert '"/assets/basic_layout.css": "text/css; charset=utf-8"' in api


def test_adapter_readiness_removal_and_pro_layout_contracts_are_unchanged() -> None:
    loader = FIELD_VISIBILITY.read_text(encoding="utf-8")
    core = CORE_UI.read_text(encoding="utf-8")

    assert "removeBasicAdapterReadinessCard" in loader
    assert "card.remove()" in loader
    assert ".advanced-layout" in core
    assert ".sidebar" in core
    assert ".content-area" in core
