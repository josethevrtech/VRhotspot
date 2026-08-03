from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELD_VISIBILITY = ROOT / "assets" / "field_visibility.js"
INDEX_HTML = ROOT / "assets" / "index.html"


def test_basic_mode_removes_adapter_readiness_card_from_the_dom() -> None:
    source = FIELD_VISIBILITY.read_text(encoding="utf-8")

    assert "removeBasicAdapterReadinessCard" in source
    assert "[data-adapter-readiness-card]" in source
    assert ".remove()" in source


def test_adapter_readiness_markup_remains_available_to_the_readiness_engine() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'data-adapter-readiness-card' in html
    assert 'data-readiness-field="recommended"' in html
