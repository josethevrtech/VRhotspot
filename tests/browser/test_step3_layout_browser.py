"""Real-browser rendered-geometry checks for the Pro Step 3 password row.

These drive the actual portal (real stylesheet order, real daemon responses)
in a real Chromium-family browser and assert the rendered geometry contract
that jsdom cannot measure. They are skipped unless Playwright is installed
and a portal is reachable.

Environment:
    VRHOTSPOT_BROWSER_TEST_URL    portal UI URL (default http://127.0.0.1:8732/ui)
    VRHOTSPOT_BROWSER_TEST_TOKEN  API token used on the login splash (required)
    VRHOTSPOT_BROWSER_EXECUTABLE  optional browser binary (e.g. system Chrome)
"""

import os
import urllib.request

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

PORTAL_URL = os.environ.get(
    "VRHOTSPOT_BROWSER_TEST_URL", "http://127.0.0.1:8732/ui"
)
TOKEN = os.environ.get("VRHOTSPOT_BROWSER_TEST_TOKEN", "")
EXECUTABLE = os.environ.get("VRHOTSPOT_BROWSER_EXECUTABLE", "")


def _portal_reachable() -> bool:
    health = PORTAL_URL.rsplit("/", 1)[0] + "/healthz"
    try:
        with urllib.request.urlopen(health, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not TOKEN, reason="VRHOTSPOT_BROWSER_TEST_TOKEN not set"),
    pytest.mark.skipif(not _portal_reachable(), reason="portal is not reachable"),
]


@pytest.fixture(scope="module")
def pro_page():
    with playwright_sync.sync_playwright() as p:
        launch_kwargs = {}
        if EXECUTABLE:
            launch_kwargs["executable_path"] = EXECUTABLE
        browser = p.chromium.launch(**launch_kwargs)
        # bypass_csp lets Playwright's string predicates run under the
        # portal's strict script-src CSP; it does not change app behavior.
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000}, bypass_csp=True
        )
        # External font hosts are unreachable in CI sandboxes.
        page.route("**fonts.googleapis.com**", lambda route: route.abort())
        page.route("**fonts.gstatic.com**", lambda route: route.abort())
        page.goto(PORTAL_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if page.locator("#loginToken").is_visible():
            page.fill("#loginToken", TOKEN)
            page.click("#btnLoginSubmit")
        page.wait_for_selector("#uiModeToggle", state="attached", timeout=15000)
        page.wait_for_timeout(1200)
        page.evaluate(
            """() => {
                const toggle = document.getElementById('uiModeToggle');
                toggle.checked = true;
                toggle.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        page.wait_for_function(
            "document.body.dataset.proGuidedStage === 'ready'", timeout=15000
        )
        page.wait_for_timeout(400)
        yield page
        browser.close()


def _rect(page, selector):
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height,
                    left: r.left, right: r.right, top: r.top};
        }""",
        selector,
    )


def test_password_row_is_composed_before_ready(pro_page):
    state = pro_page.evaluate(
        """() => {
            const row = document.querySelector('.pro-password-row');
            const cell = row ? row.querySelector(':scope > .pro-password-input-cell') : null;
            const kids = row ? Array.from(row.children) : [];
            const reveal = document.getElementById('btnRevealPass');
            const qr = document.getElementById('btnShowQr');
            return {
                rows: document.querySelectorAll('.pro-password-row').length,
                cellExists: !!cell,
                inputInCell: !!cell && cell.contains(document.getElementById('wpa2_passphrase')),
                revealInRow: !!reveal && reveal.parentElement === row,
                qrInRow: !!qr && qr.parentElement === row,
                ordered: !!cell && kids.indexOf(cell) < kids.indexOf(reveal)
                    && kids.indexOf(reveal) < kids.indexOf(qr),
            };
        }"""
    )
    assert state["rows"] == 1
    assert state["cellExists"] and state["inputInCell"]
    assert state["revealInRow"] and state["qrInRow"]
    assert state["ordered"]


def test_password_row_rendered_geometry(pro_page):
    input_rect = _rect(pro_page, "#wpa2_passphrase")
    reveal_rect = _rect(pro_page, "#btnRevealPass")
    qr_rect = _rect(pro_page, "#btnShowQr")
    assert input_rect and reveal_rect and qr_rect

    # Identical top positions: one row, no wrapping.
    assert abs(input_rect["top"] - reveal_rect["top"]) < 1
    assert abs(input_rect["top"] - qr_rect["top"]) < 1

    # Identical heights.
    assert abs(input_rect["h"] - reveal_rect["h"]) < 1
    assert abs(input_rect["h"] - qr_rect["h"]) < 1

    # Reveal and QR are equal-size squares.
    assert abs(reveal_rect["w"] - reveal_rect["h"]) < 1
    assert abs(qr_rect["w"] - qr_rect["h"]) < 1
    assert abs(reveal_rect["w"] - qr_rect["w"]) < 1

    # QR is not on a second line: it sits to the right of reveal on the
    # same baseline, and both are to the right of the input.
    assert input_rect["right"] <= reveal_rect["left"] + 1
    assert reveal_rect["right"] <= qr_rect["left"] + 1

    # Reveal is not detached at the far right: the buttons hug the input
    # within one grid gap rather than floating across the row.
    max_gap = 24
    assert reveal_rect["left"] - input_rect["right"] <= max_gap
    assert qr_rect["left"] - reveal_rect["right"] <= max_gap


def _assert_row_geometry(pro_page, context):
    input_rect = _rect(pro_page, "#wpa2_passphrase")
    reveal_rect = _rect(pro_page, "#btnRevealPass")
    qr_rect = _rect(pro_page, "#btnShowQr")
    assert input_rect and reveal_rect and qr_rect, context
    assert abs(input_rect["top"] - reveal_rect["top"]) < 1, f"{context}: tops"
    assert abs(input_rect["top"] - qr_rect["top"]) < 1, f"{context}: tops"
    assert abs(input_rect["h"] - reveal_rect["h"]) < 1, f"{context}: heights"
    assert abs(input_rect["h"] - qr_rect["h"]) < 1, f"{context}: heights"
    assert abs(reveal_rect["w"] - reveal_rect["h"]) < 1, f"{context}: eye square"
    assert abs(qr_rect["w"] - qr_rect["h"]) < 1, f"{context}: QR square"
    assert input_rect["right"] <= reveal_rect["left"] + 1, f"{context}: wrap"
    assert reveal_rect["right"] <= qr_rect["left"] + 1, f"{context}: wrap"
    assert reveal_rect["left"] - input_rect["right"] <= 24, f"{context}: detached eye"
    assert qr_rect["left"] - reveal_rect["right"] <= 24, f"{context}: detached eye"
    dupes = pro_page.evaluate(
        """() => ['wpa2_passphrase', 'btnRevealPass', 'btnShowQr'].map(
            (id) => document.querySelectorAll(`[id="${id}"]`).length)"""
    )
    assert dupes == [1, 1, 1], f"{context}: duplicated controls {dupes}"
    assert pro_page.evaluate(
        "document.body.dataset.proGuidedStage"
    ) == "ready", f"{context}: stage demoted"


INJECTIONS = [
    ("sibling", """(row, cell, input) => {
        const icon = document.createElement('div');
        icon.className = 'thirdparty-icon';
        icon.style.cssText = 'width:24px;height:24px;background:red;';
        row.appendChild(icon);
    }"""),
    ("abs-control", """(row, cell, input) => {
        const btn = document.createElement('button');
        btn.style.cssText = 'position:absolute;right:4px;top:4px;width:20px;height:20px;';
        (cell || row).appendChild(btn);
    }"""),
    ("wrapper", """(row, cell, input) => {
        const wrap = document.createElement('div');
        wrap.className = 'thirdparty-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
    }"""),
    ("shadow-host", """(row, cell, input) => {
        const host = document.createElement('div');
        const shadow = host.attachShadow({mode: 'open'});
        const inner = document.createElement('span');
        inner.textContent = 'pm';
        shadow.appendChild(inner);
        row.appendChild(host);
    }"""),
]


def test_late_third_party_injection_keeps_row_stable(pro_page):
    """Password-manager style DOM injection 1-3s after ready must not break
    the rendered row for at least five seconds, demote readiness, duplicate
    controls, or spin the composer in a reconcile loop."""
    for name, inject_js in INJECTIONS:
        pro_page.wait_for_timeout(1500)  # inject 1-3 seconds after steady state
        pro_page.evaluate(
            f"""() => {{
                const row = document.querySelector('.pro-password-row');
                const cell = document.querySelector('.pro-password-input-cell');
                const input = document.getElementById('wpa2_passphrase');
                ({inject_js})(row, cell, input);
            }}"""
        )
        deadline = 5000
        elapsed = 0
        while elapsed < deadline:
            pro_page.wait_for_timeout(500)
            elapsed += 500
            _assert_row_geometry(pro_page, f"{name} +{elapsed}ms")
    # No mutation/reconcile loop: at idle the decorators must leave the
    # password row completely mutation-quiet (a DOM fight would show here).
    pro_page.evaluate(
        """() => {
            window.__rowStructuralMutations = 0;
            const input = document.getElementById('wpa2_passphrase');
            new MutationObserver((records) => {
                for (const record of records) {
                    // The base app's config poller refreshes input attributes
                    // as data flow; anything else is a decorator DOM fight.
                    if (record.type === 'childList' || record.target !== input) {
                        window.__rowStructuralMutations += 1;
                    }
                }
            }).observe(document.querySelector('.pro-password-row'), {
                childList: true, subtree: true, attributes: true,
            });
        }"""
    )
    pro_page.wait_for_timeout(1500)
    mutations = pro_page.evaluate("window.__rowStructuralMutations")
    assert mutations == 0, f"password row saw {mutations} structural mutations while idle"
    assert pro_page.evaluate("document.body.dataset.proGuidedStage") == "ready"


def _watch_adapter_select(pro_page):
    pro_page.evaluate(
        """() => {
            const sel = document.getElementById('ap_adapter');
            window.__selChildList = 0;
            new MutationObserver((records) => {
                for (const r of records) {
                    if (r.type === 'childList') window.__selChildList += 1;
                }
            }).observe(sel, {childList: true, subtree: true});
            window.__selNodes = Array.from(sel.options);
            window.__selValue = sel.value;
            window.__selRect = sel.getBoundingClientRect().width;
        }"""
    )


def _assert_adapter_select_stable(pro_page, seconds, drive_reloads):
    """Sample for `seconds`; the select must never blank, resize, lose its
    selection, rebuild unchanged options, or drop its friendly label."""
    elapsed = 0
    while elapsed < seconds * 1000:
        if drive_reloads:
            pro_page.evaluate("window.loadAdapters()")
        pro_page.wait_for_timeout(1000)
        elapsed += 1000
        sample = pro_page.evaluate(
            """() => {
                const sel = document.getElementById('ap_adapter');
                return {
                    childList: window.__selChildList,
                    count: sel.options.length,
                    value: sel.value,
                    width: sel.getBoundingClientRect().width,
                    sameNodes: Array.from(sel.options).every(
                        (o, i) => o === window.__selNodes[i]),
                    firstLabel: sel.options[0] ? sel.options[0].textContent : null,
                };
            }"""
        )
        assert sample["childList"] == 0, f"+{elapsed}ms: option nodes rebuilt"
        assert sample["sameNodes"], f"+{elapsed}ms: option identity lost"
        assert sample["count"] > 0, f"+{elapsed}ms: selector blanked out"
        assert sample["value"] == pro_page.evaluate("window.__selValue"), (
            f"+{elapsed}ms: selection lost"
        )
        assert abs(sample["width"] - pro_page.evaluate("window.__selRect")) < 1, (
            f"+{elapsed}ms: selector width changed"
        )
        assert sample["firstLabel"] and "Wi-Fi" in sample["firstLabel"], (
            f"+{elapsed}ms: friendly label lost ({sample['firstLabel']!r})"
        )


def test_adapter_selector_is_stable_under_polling(pro_page):
    _watch_adapter_select(pro_page)
    # Normal polling plus explicit inventory refreshes for 10 seconds.
    _assert_adapter_select_stable(pro_page, 10, drive_reloads=True)

    # Repeat after Basic -> Pro -> Basic -> Pro.
    for advanced in (False, True, False, True):
        pro_page.evaluate(
            """(advanced) => {
                const toggle = document.getElementById('uiModeToggle');
                toggle.checked = advanced;
                toggle.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            advanced,
        )
        if advanced:
            pro_page.wait_for_function(
                "document.body.dataset.proGuidedStage === 'ready'", timeout=15000
            )
        else:
            pro_page.wait_for_function(
                "document.body.dataset.uiMode === 'basic'", timeout=15000
            )
        pro_page.wait_for_timeout(500)
    pro_page.wait_for_timeout(1000)
    _watch_adapter_select(pro_page)
    _assert_adapter_select_stable(pro_page, 10, drive_reloads=True)
