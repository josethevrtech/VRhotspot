from pathlib import Path

from vr_hotspotd.devtools import devhub_api


ROOT = Path(__file__).resolve().parents[1]
BROWSER_SESSION_JS = ROOT / "assets" / "browser_session.js"
DEVHUB_API = ROOT / "backend" / "vr_hotspotd" / "devtools" / "devhub_api.py"


def test_browser_session_keeps_api_token_out_of_browser_storage() -> None:
    source = BROWSER_SESSION_JS.read_text(encoding="utf-8")

    assert "sessionStorage" not in source
    assert "localStorage" not in source
    assert "document.cookie" not in source
    assert "credentials: 'same-origin'" in source
    assert "X-Api-Token" in source
    assert "pendingCandidate = ''" in source


def test_daemon_issues_opaque_http_only_same_site_cookie() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert 'BROWSER_SESSION_PATH = "/v1/auth/browser-session"' in source
    assert 'secrets.token_urlsafe(32)' in source
    assert "HttpOnly" in source
    assert "SameSite=Strict" in source
    assert "SameSite=Strict; Max-Age=0" in source
    assert "Max-Age={_BROWSER_SESSION_TTL_S}" not in source
    assert "super()._is_authorized()" in source
    assert "_browser_session_is_valid" in source


def test_browser_session_lifetime_is_sliding_and_expires(monkeypatch) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(devhub_api.time, "monotonic", lambda: clock["now"])
    devhub_api._BROWSER_SESSIONS.clear()

    session_id = devhub_api._create_browser_session()
    assert session_id
    assert devhub_api._browser_session_is_valid(session_id) is True

    clock["now"] += devhub_api._BROWSER_SESSION_TTL_S - 1
    assert devhub_api._browser_session_is_valid(session_id) is True

    clock["now"] += devhub_api._BROWSER_SESSION_TTL_S + 1
    assert devhub_api._browser_session_is_valid(session_id) is False
