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


def test_password_privacy_and_label_alignment(pro_page):
    state = pro_page.evaluate(
        """() => ({
            hint: document.getElementById('passHint').textContent,
            placeholder: document.getElementById('wpa2_passphrase').placeholder,
            tip: document.querySelector(
                '[data-field="wpa2_passphrase"] .field-label-with-tip .tip'
            )?.getAttribute('data-tip'),
            tabindex: document.querySelector(
                '[data-field="wpa2_passphrase"] .field-label-with-tip .tip'
            )?.getAttribute('tabindex'),
        })"""
    )
    assert state["hint"] == "", "no saved-state text may appear below the password field"
    assert "saved" not in state["placeholder"].lower()
    assert not any(ch.isdigit() for ch in state["hint"])
    assert state["tip"] == "Use 8–63 characters. Control characters are not allowed."
    assert state["tabindex"] == "0"

    rows = pro_page.evaluate(
        """() => ['wpa2_passphrase', 'band_preference', 'ap_security'].map((key) => {
            const wrap = document.querySelector(`[data-field="${key}"] > .field-label-with-tip`);
            const label = wrap ? wrap.querySelector('label') : null;
            const tip = wrap ? wrap.querySelector('.tip') : null;
            const r = (el) => {
                const b = el.getBoundingClientRect();
                return {cy: b.top + b.height / 2, w: b.width, h: b.height, top: b.top};
            };
            return {key, exists: !!(wrap && label && tip),
                    label: label ? r(label) : null, tip: tip ? r(tip) : null};
        })"""
    )
    for row in rows:
        assert row["exists"], f"{row['key']} must use the shared label-row structure"
        # Within each label row the text and icon share one vertical center.
        assert abs(row["label"]["cy"] - row["tip"]["cy"]) < 1, (
            f"{row['key']}: label/icon centers differ "
            f"({row['label']['cy']:.2f} vs {row['tip']['cy']:.2f})"
        )
    # All info icons share identical dimensions.
    for row in rows[1:]:
        assert abs(row["tip"]["w"] - rows[0]["tip"]["w"]) < 0.5
        assert abs(row["tip"]["h"] - rows[0]["tip"]["h"]) < 0.5
    # Band and Security sit on the same grid row with no baseline drift.
    band, security = rows[1], rows[2]
    assert abs(band["label"]["cy"] - security["label"]["cy"]) < 1
    assert abs(band["tip"]["cy"] - security["tip"]["cy"]) < 1


def test_information_architecture(pro_page):
    ia = pro_page.evaluate(
        """() => {
            const shell = document.querySelector('#tab-troubleshooting .troubleshooting-shell');
            const quality = document.getElementById('proConnectionQuality');
            return {
                step3Internet: !!document.querySelector(
                    '#proStepHotspot [data-field="enable_internet"]'),
                connectivity: !!document.querySelector(
                    '#tab-troubleshooting #proConnectivityCard [data-field="enable_internet"]'),
                internetCount: document.querySelectorAll('[id="enable_internet"]').length,
                qualityParentIsShell: !!shell && !!quality && quality.parentElement === shell,
                qualityLast: !!shell && shell.lastElementChild === quality,
                qualityCount: document.querySelectorAll('[id="proConnectionQuality"]').length,
                workflowHasQuality: !!document.querySelector(
                    '#proGuidedWorkflow #proConnectionQuality'),
            };
        }"""
    )
    assert ia["step3Internet"] is False, "internet sharing must be out of Step 3"
    assert ia["connectivity"], "internet sharing must live in Troubleshooting > Connectivity"
    assert ia["internetCount"] == 1
    assert ia["qualityParentIsShell"] and ia["qualityLast"], (
        "Connection Quality must be the final Troubleshooting section"
    )
    assert ia["qualityCount"] == 1
    assert ia["workflowHasQuality"] is False


def test_step_two_has_no_generic_helper(pro_page):
    present = pro_page.evaluate(
        "document.body.textContent.includes("
        "'Choose the performance behavior that best matches this hotspot.')"
    )
    assert present is False


def test_step_four_clean_layout_and_moved_controls(pro_page):
    data = pro_page.evaluate(
        """() => {
            const rows = [];
            document.querySelectorAll('#proStepAdvanced .pro-config-details').forEach((d) => {
                d.open = true;
                const summary = d.querySelector(':scope > summary');
                rows.push({
                    title: summary.textContent.trim(),
                    hasChip: !!summary.querySelector('.pro-config-summary'),
                });
            });
            const r = (id) => {
                const node = document.getElementById(id);
                return node ? node.getBoundingClientRect().top : null;
            };
            const v = (id) => document.getElementById(id)?.value?.trim();
            const inStep4 = (id) => !!document.querySelector(`#proStepAdvanced [id="${id}"]`);
            const inPane = (sel) => !!document.querySelector(`#tab-troubleshooting ${sel}`);
            return {
                rows,
                tops: {
                    ch5: r('channel_5g'), ch6: r('channel_6g'),
                    width: r('channel_width'), tx: r('tx_power'),
                    gateway: r('lan_gateway_ip'), dns: r('dhcp_dns'),
                    dhcpStart: r('dhcp_start_ip'), dhcpEnd: r('dhcp_end_ip'),
                },
                values: {
                    width: v('channel_width'), gateway: v('lan_gateway_ip'),
                    dns: v('dhcp_dns'), timeout: v('ap_ready_timeout_s'),
                },
                moved: {
                    debugOut: !inStep4('debug'),
                    powerOut: !inStep4('wifi_power_save_disable'),
                    natOut: !inStep4('nat_accel'),
                    bridgeOut: !inStep4('bridge_mode'),
                    firewallOut: !inStep4('firewalld_enabled'),
                    noVirtOut: !inStep4('optimized_no_virt'),
                    debugIn: inPane('#proDebuggingCard #debug'),
                    powerIn: inPane('#proCompatibilityCard #wifi_power_save_disable'),
                    usbIn: inPane('#proCompatibilityCard #usb_autosuspend_disable'),
                    natIn: inPane('#proCompatibilityCard [data-field="nat_accel"]'),
                    bridgeIn: inPane('#proCompatibilityCard [data-field="bridge_mode"]'),
                    firewallIn: inPane('#proCompatibilityCard [data-field="firewalld_enabled"]'),
                    noVirtIn: inPane('#proCompatibilityCard [data-field="optimized_no_virt"]'),
                },
                dupes: ['debug', 'wifi_power_save_disable', 'nat_accel', 'bridge_mode',
                        'firewalld_enabled', 'optimized_no_virt'].map(
                    (id) => document.querySelectorAll(`[id="${id}"]`).length),
            };
        }"""
    )
    # Headers carry only the title: no mirrored summary chips.
    for row in data["rows"]:
        assert row["hasChip"] is False, f"{row['title']}: header still shows a summary chip"
    # Two-column alignment: paired fields share row tops at desktop width.
    tops = data["tops"]
    assert abs(tops["ch5"] - tops["ch6"]) < 2, "5 GHz / 6 GHz must share a row"
    assert abs(tops["width"] - tops["tx"]) < 2, "Width / TX power must share a row"
    assert abs(tops["gateway"] - tops["dns"]) < 2, "Gateway / DNS must share a row"
    assert abs(tops["dhcpStart"] - tops["dhcpEnd"]) < 2, "DHCP start/end must share a row"
    # The live values are the applied-state mirror.
    values = data["values"]
    assert values["width"], "channel width must show the applied value"
    assert values["gateway"] and values["dns"] and values["timeout"]
    # Compatibility/recovery controls moved to Troubleshooting, no clones.
    for key, ok in data["moved"].items():
        assert ok, f"relocation check failed: {key}"
    assert data["dupes"] == [1, 1, 1, 1, 1, 1]


STEP4_TIP_IDS = [
    "channel_5g", "channel_6g", "fallback_channel_2g", "channel_auto_select",
    "channel_width", "tx_power", "beacon_interval", "dtim_period",
    "short_guard_interval", "lan_gateway_ip", "dhcp_dns", "dhcp_start_ip",
    "dhcp_end_ip", "ap_ready_timeout_s", "cpu_governor_performance",
    "sysctl_tuning", "interrupt_coalescing",
]


def test_step_four_field_tips_and_icon_sizing(pro_page):
    data = pro_page.evaluate(
        """(ids) => {
            document.querySelectorAll('#proStepAdvanced .pro-config-details')
                .forEach((d) => d.open = true);
            const tips = ids.map((id) => {
                const control = document.getElementById(id);
                const label = document.querySelector(`label[for="${id}"]`)
                    || control?.closest('label');
                const tip = label ? label.querySelector('.hint.tip-only .tip') : null;
                if (!tip) return {id, missing: true};
                const r = tip.getBoundingClientRect();
                return {id, w: r.width, h: r.height,
                        text: tip.getAttribute('data-tip') || ''};
            });
            const step3Node = document.querySelector('[data-field="band_preference"] .tip');
            const step3Tip = step3Node.getBoundingClientRect();
            const step3Cs = getComputedStyle(step3Node);
            return {
                tips,
                introGone: !document.querySelector('#proStepAdvanced .pro-advanced-group-help'),
                step3: {w: step3Tip.width, h: step3Tip.height,
                        font: step3Cs.fontSize, lineHeight: step3Cs.lineHeight,
                        padding: step3Cs.padding, border: step3Cs.borderTopWidth,
                        boxSizing: step3Cs.boxSizing, transform: step3Cs.transform,
                        zoom: step3Cs.zoom},
            };
        }""",
        STEP4_TIP_IDS,
    )
    assert data["introGone"], "gray intro copy must be gone from Step 4 bodies"
    for tip in data["tips"]:
        assert not tip.get("missing"), f"{tip['id']}: label has no info tip"
        assert tip["text"], f"{tip['id']}: tip has no help text"
        assert abs(tip["w"] - 16) < 1 and abs(tip["h"] - 16) < 1, (
            f"{tip['id']}: icon is {tip['w']:.1f}x{tip['h']:.1f}, expected 16x16"
        )
    assert abs(data["step3"]["w"] - 16) < 1 and abs(data["step3"]["h"] - 16) < 1
    step3_metrics = data["step3"]

    # Opening a tooltip must not shift the layout.
    shift = pro_page.evaluate(
        """() => {
            const tip = document.querySelector('label[for="channel_5g"] .tip');
            const label = tip.closest('label');
            const before = label.getBoundingClientRect().top;
            tip.focus();
            const layer = document.querySelector('.floating-tip-layer');
            return {
                delta: label.getBoundingClientRect().top - before,
                layerVisible: !!layer && layer.getAttribute('aria-hidden') !== 'true',
            };
        }"""
    )
    assert abs(shift["delta"]) < 0.5, "opening a tooltip must not shift the layout"
    assert shift["layerVisible"], "focusing a tip must open the floating tooltip"

    # Basic-mode icons share the same canonical bounding box.
    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = false;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function("document.body.dataset.uiMode === 'basic'", timeout=15000)
    pro_page.wait_for_timeout(600)
    basic_tips = pro_page.evaluate(
        """() => Array.from(document.querySelectorAll('.tip')).map((tip) => {
            const r = tip.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return null;
            const cs = getComputedStyle(tip);
            return {w: r.width, h: r.height, cy: r.top + r.height / 2,
                    font: cs.fontSize, lineHeight: cs.lineHeight,
                    padding: cs.padding, border: cs.borderTopWidth,
                    boxSizing: cs.boxSizing, transform: cs.transform, zoom: cs.zoom};
        }).filter(Boolean)"""
    )
    assert basic_tips, "no visible info icon found in Basic mode"
    # Every Basic icon must match the Pro Step 3 icon exactly, not merely be
    # numerically close: same box, glyph metrics, and no scaling.
    reference = {"w": 16.0, "h": 16.0, "font": "11px", "lineHeight": "11px",
                 "padding": "0px", "border": "1px", "boxSizing": "border-box"}
    for tip in basic_tips + [{**t, "cy": 0} for t in [step3_metrics]]:
        assert abs(tip["w"] - reference["w"]) < 0.5, f"icon width {tip['w']}"
        assert abs(tip["h"] - reference["h"]) < 0.5, f"icon height {tip['h']}"
        assert tip["font"] == reference["font"], f"glyph size {tip['font']}"
        assert tip["lineHeight"] == reference["lineHeight"], f"line height {tip['lineHeight']}"
        assert tip["padding"] == reference["padding"], f"padding {tip['padding']}"
        assert tip["border"] == reference["border"], f"border {tip['border']}"
        assert tip["boxSizing"] == reference["boxSizing"]
        assert tip["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)"), (
            f"icons must not be scaled: {tip['transform']}"
        )
        assert tip["zoom"] in ("1", "normal"), f"icons must not be zoomed: {tip['zoom']}"

    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = true;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function(
        "document.body.dataset.proGuidedStage === 'ready'", timeout=15000
    )
    pro_page.wait_for_timeout(500)


def test_basic_step_one_controls(pro_page):
    """Basic hides the redundant Recommended button and shows Rescan as a
    full-width button matching Pro, without adapter flicker."""
    pro_rescan = pro_page.evaluate(
        """() => {
            const cs = getComputedStyle(document.getElementById('btnReloadAdapters'));
            const r = document.getElementById('btnReloadAdapters').getBoundingClientRect();
            return {h: r.height, border: cs.borderTopWidth, bg: cs.backgroundColor,
                    font: cs.fontSize, weight: cs.fontWeight,
                    transform: cs.textTransform, radius: cs.borderTopLeftRadius};
        }"""
    )
    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = false;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function("document.body.dataset.uiMode === 'basic'", timeout=15000)
    pro_page.wait_for_timeout(1200)

    basic = pro_page.evaluate(
        """() => {
            const rec = document.getElementById('btnUseRecommended');
            const recRect = rec ? rec.getBoundingClientRect() : null;
            const rescan = document.getElementById('btnReloadAdapters');
            const cs = getComputedStyle(rescan);
            const r = rescan.getBoundingClientRect();
            const select = document.getElementById('ap_adapter');
            const sr = select.getBoundingClientRect();
            const visibleRecommendedText = Array.from(
                document.querySelectorAll('button, a')
            ).filter((node) => {
                const box = node.getBoundingClientRect();
                return box.width > 0 && box.height > 0
                    && /^\\s*recommended\\s*$/i.test(node.textContent || '');
            }).length;
            return {
                recCount: document.querySelectorAll('[id="btnUseRecommended"]').length,
                recVisible: !!recRect && recRect.width > 0 && recRect.height > 0,
                recHidden: !rec || rec.hidden,
                recAria: rec ? rec.getAttribute('aria-hidden') : null,
                recTab: rec ? rec.tabIndex : null,
                recDisplay: rec ? getComputedStyle(rec).display : null,
                visibleRecommendedText,
                rescan: {h: r.height, w: r.width, top: r.top, left: r.left,
                         bottom: r.bottom,
                         border: cs.borderTopWidth, bg: cs.backgroundColor,
                         font: cs.fontSize, weight: cs.fontWeight,
                         transform: cs.textTransform, radius: cs.borderTopLeftRadius,
                         align: cs.justifyContent},
                selector: {w: sr.width, h: sr.height, left: sr.left, right: sr.right,
                           top: sr.top, bottom: sr.bottom},
                options: Array.from(select.options).map((o) => o.textContent),
            };
        }"""
    )
    assert basic["recCount"] == 1, "the live Recommended node must remain unique"
    assert basic["recVisible"] is False, "Recommended must not be visible in Basic"
    assert basic["recHidden"] and basic["recAria"] == "true"
    assert basic["recTab"] == -1 and basic["recDisplay"] == "none"
    assert basic["visibleRecommendedText"] == 0, "no visible control labelled Recommended"

    rescan, selector = basic["rescan"], basic["selector"]
    # Desktop: one aligned row — selector flexible, Rescan compact.
    assert 190 <= rescan["w"] <= 230, f"Rescan must be compact, got {rescan['w']:.0f}px"
    assert rescan["w"] < selector["w"], "Rescan must not dominate the row"
    assert rescan["left"] >= selector["right"] - 1, "Rescan sits beside the selector"
    assert abs(rescan["top"] - selector["top"]) < 2, "row tops must align"
    assert abs(rescan["bottom"] - selector["bottom"]) < 2, "row bottoms must align"
    assert abs(rescan["h"] - selector["h"]) < 2, "selector and Rescan share one height"
    assert rescan["border"] == pro_rescan["border"], "border must match Pro"
    assert rescan["bg"] == pro_rescan["bg"], "background must match Pro"
    assert rescan["font"] == pro_rescan["font"], "typography must match Pro"
    assert rescan["weight"] == pro_rescan["weight"]
    assert rescan["transform"] == pro_rescan["transform"], "capitalization must match Pro"
    assert rescan["radius"] == pro_rescan["radius"]
    assert rescan["align"] == "center", "label must be centered"

    # A real rescan must not rebuild unchanged options or blank the selector.
    pro_page.evaluate(
        """() => {
            const sel = document.getElementById('ap_adapter');
            window.__rescanMuts = 0;
            window.__rescanNodes = Array.from(sel.options);
            new MutationObserver((records) => {
                for (const r of records) {
                    if (r.type === 'childList') window.__rescanMuts += 1;
                }
            }).observe(sel, {childList: true, subtree: true});
        }"""
    )
    pro_page.click("#btnReloadAdapters")
    pro_page.wait_for_timeout(2500)
    after = pro_page.evaluate(
        """() => {
            const sel = document.getElementById('ap_adapter');
            return {
                muts: window.__rescanMuts,
                same: Array.from(sel.options).every((o, i) => o === window.__rescanNodes[i]),
                count: sel.options.length,
                labels: Array.from(sel.options).map((o) => o.textContent),
            };
        }"""
    )
    assert after["muts"] == 0, f"rescan rebuilt options ({after['muts']} mutations)"
    assert after["same"], "unchanged options must retain node identity"
    assert after["count"] > 0, "the selector must never blank out"
    assert after["labels"] == basic["options"], "labels must be unchanged"

    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = true;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function(
        "document.body.dataset.proGuidedStage === 'ready'", timeout=15000)
    pro_page.wait_for_timeout(600)


def test_apply_changes_and_restart_request_sequence(pro_page):
    """The real click path must save the config and issue exactly one
    /v1/restart, never /v1/stop."""
    pro_page.evaluate(
        """() => {
            window.__reqLog = [];
            if (!window.__origFetch) window.__origFetch = window.fetch;
            window.fetch = (url, init) => {
                window.__reqLog.push(
                    `${(init && init.method) || 'GET'} ${new URL(String(url), location.origin).pathname}`);
                return window.__origFetch(url, init);
            };
            document.getElementById('proServiceStateText').textContent = 'Running';
        }"""
    )
    pro_page.wait_for_timeout(400)
    original = pro_page.evaluate("document.getElementById('ssid').value")
    pro_page.fill("#ssid", f"{original}X")
    pro_page.wait_for_function(
        "document.getElementById('btnStart').dataset.proGuidedAction === 'apply'",
        timeout=8000,
    )
    assert pro_page.evaluate(
        "document.getElementById('btnStart').textContent.trim()"
    ) == "Apply Changes & Restart"

    pro_page.evaluate("window.__reqLog = []")
    pro_page.click("#btnStart")  # real user click, not a handler invocation
    pro_page.wait_for_function(
        "window.__reqLog.some((e) => e.endsWith('/v1/restart'))", timeout=15000
    )
    pro_page.wait_for_timeout(1500)
    log = pro_page.evaluate("window.__reqLog")
    restarts = [e for e in log if e.endswith("/v1/restart")]
    stops = [e for e in log if e.endswith("/v1/stop")]
    saves = [e for e in log if e == "POST /v1/config"]
    assert len(restarts) == 1, f"expected exactly one restart, got {restarts} in {log}"
    assert stops == [], f"the apply action must never stop the hotspot: {log}"
    assert saves, f"the apply action must persist the configuration: {log}"
    assert log.index(saves[0]) < log.index(restarts[0]), "config must save before restart"

    pro_page.evaluate(
        """(original) => {
            document.getElementById('proServiceStateText').textContent = 'Stopped';
            if (window.__origFetch) window.fetch = window.__origFetch;
        }""",
        original,
    )
    pro_page.fill("#ssid", original)
    pro_page.wait_for_timeout(1500)


def test_step_five_action_surface(pro_page):
    data = pro_page.evaluate(
        """() => {
            const action = document.querySelector('#proStepAction .pro-guided-action');
            const buttons = action.querySelector('.pro-guided-action-buttons');
            const start = document.getElementById('btnStart');
            const save = document.getElementById('btnSaveConfig');
            const saveRestart = document.getElementById('btnSaveRestart');
            const r = (el) => el.getBoundingClientRect();
            const visible = Array.from(buttons.children).filter(
                (node) => !node.hidden && node.getBoundingClientRect().height > 0);
            return {
                startText: start.textContent.trim(),
                startRect: {x: r(start).x, w: r(start).width, top: r(start).top},
                saveRect: {x: r(save).x, w: r(save).width, top: r(save).top},
                saveVisible: r(save).width > 0 && r(save).height > 0,
                saveRestartVisible: r(saveRestart).width > 0 && r(saveRestart).height > 0,
                repairInStep5: !!document.querySelector('#proStepAction #btnRepair'),
                repairInRecovery: !!document.querySelector(
                    '#tab-troubleshooting .troubleshooting-actions #btnRepair'),
                visibleCount: visible.length,
                secondaryTrack: !!document.querySelector(
                    '#proStepAction .pro-guided-secondary-actions'),
                dupes: ['btnStart', 'btnSaveConfig', 'btnSaveRestart', 'btnRepair'].map(
                    (id) => document.querySelectorAll(`[id="${id}"]`).length),
                stateCopyLeft: r(action.firstElementChild).x < r(buttons).x,
                columnWidth: r(buttons).width,
                trailingGap: r(buttons).bottom - r(start).bottom,
            };
        }"""
    )
    assert data["startText"] in ("Start Hotspot", "Stop Hotspot")
    assert data["saveVisible"] is False, "Save Changes must not be visible in Pro"
    assert data["saveRestartVisible"] is False, "Save & Restart must not be visible"
    assert data["dupes"][:3] == [1, 1, 1], "save controls must exist exactly once"
    assert data["repairInStep5"] is False, "Repair Network must not be in Step 5"
    assert data["repairInRecovery"], "Repair Network must sit in Troubleshooting recovery actions"
    assert data["visibleCount"] == 1, "the action column exposes exactly one control"
    assert data["secondaryTrack"] is False, "no leftover secondary track"
    assert data["dupes"] == [1, 1, 1, 1]
    assert data["stateCopyLeft"], "status copy must stay aligned on the left"
    # The primary fills its column with no detached space beneath it.
    assert abs(data["startRect"]["w"] - data["columnWidth"]) < 2, (
        "the primary must be full width in the action column"
    )
    assert data["trailingGap"] < 2, (
        f"no empty row may follow the primary (gap {data['trailingGap']:.1f}px)"
    )

    # Running + autosaved change: the primary must offer the apply action.
    state = pro_page.evaluate(
        """() => {
            const status = document.getElementById('proServiceStateText');
            const before = status.textContent;
            status.textContent = 'Running';
            return before;
        }"""
    )
    pro_page.wait_for_timeout(400)
    original_ssid = pro_page.evaluate("document.getElementById('ssid').value")
    pro_page.fill("#ssid", f"{original_ssid}X")  # trusted user edit
    pro_page.wait_for_function(
        "document.getElementById('btnStart').dataset.proGuidedAction === 'apply'",
        timeout=8000,
    )
    assert pro_page.evaluate(
        "document.getElementById('btnStart').textContent.trim()"
    ) == "Apply Changes & Restart"
    pro_page.evaluate(
        "(before) => { document.getElementById('proServiceStateText').textContent = before; }",
        state,
    )
    pro_page.fill("#ssid", original_ssid)
    pro_page.wait_for_timeout(1200)


def test_sidebar_state_and_order(pro_page):
    """One authoritative selection at a time, cyan only on the active item,
    order Set Up Hotspot -> Developer Hub -> Troubleshooting."""
    routes = ["overview", "devhub", "troubleshooting"]
    order = pro_page.evaluate(
        """() => Array.from(document.querySelectorAll('.nav-item')).map(
            (item) => item.dataset.tab)"""
    )
    assert order == ["overview", "devhub", "troubleshooting"], f"nav order {order}"
    for route in routes:
        pro_page.click(f'.nav-item[data-tab="{route}"]')
        pro_page.wait_for_timeout(300)
        state = pro_page.evaluate(
            """(route) => {
                const items = Array.from(document.querySelectorAll('.nav-item'));
                const activeItems = items.filter((i) => i.classList.contains('active'));
                const ariaItems = items.filter((i) => i.getAttribute('aria-current') === 'page');
                const accent = getComputedStyle(document.documentElement)
                    .getPropertyValue('--accent-primary').trim();
                const colors = items.map((i) => {
                    const cs = getComputedStyle(i);
                    const svg = i.querySelector('.pro-nav-svg');
                    return {tab: i.dataset.tab, color: cs.color,
                            svgColor: svg ? getComputedStyle(svg).color : null,
                            active: i.classList.contains('active')};
                });
                const panes = Array.from(document.querySelectorAll('.tab-pane.active'))
                    .map((p) => p.id);
                return {activeTabs: activeItems.map((i) => i.dataset.tab),
                        ariaTabs: ariaItems.map((i) => i.dataset.tab),
                        colors, panes, accent};
            }""",
            route,
        )
        assert state["activeTabs"] == [route], (
            f"{route}: expected one active item, got {state['activeTabs']}"
        )
        assert state["ariaTabs"] == [route], f"{route}: aria-current mismatch"
        assert state["panes"] == [f"tab-{route}"], f"{route}: visible panes {state['panes']}"
        active = [c for c in state["colors"] if c["active"]][0]
        inactive = [c for c in state["colors"] if not c["active"]]
        for item in inactive:
            assert item["color"] != active["color"], (
                f"{route}: inactive {item['tab']} shares the active color"
            )
            if item["svgColor"]:
                assert item["svgColor"] == item["color"], (
                    f"{route}: {item['tab']} icon does not inherit its item color"
                )
    pro_page.click('.nav-item[data-tab="overview"]')
    pro_page.wait_for_timeout(300)


def test_qr_glyph_and_privacy_shield(pro_page):
    data = pro_page.evaluate(
        """() => {
            const pro = document.getElementById('btnShowQr');
            const basic = document.getElementById('btnShowQrBasic');
            const shieldLabel = document.getElementById('privacyMode').closest('label');
            const shield = shieldLabel.querySelector('.privacy-shield');
            const sr = shield ? shield.getBoundingClientRect() : null;
            return {
                proGlyph: !!pro.querySelector('.qr-glyph'),
                proLabel: pro.getAttribute('aria-label'),
                proText: pro.textContent.trim(),
                basicGlyph: !!basic.querySelector('.qr-glyph'),
                basicLabel: basic.getAttribute('aria-label'),
                glyphHidden: pro.querySelector('.qr-glyph')?.getAttribute('aria-hidden'),
                shieldExists: !!shield,
                shieldSize: sr ? {w: sr.width, h: sr.height} : null,
                shieldHidden: shield?.getAttribute('aria-hidden'),
                privacyUnique: document.querySelectorAll('[id="privacyMode"]').length,
            };
        }"""
    )
    assert data["proGlyph"] and data["basicGlyph"], "both QR buttons must carry the glyph"
    assert data["proLabel"] == "Show hotspot QR code"
    assert data["basicLabel"] == "Show hotspot QR code"
    assert data["proText"] == "", "no visible QR text remains"
    assert data["glyphHidden"] == "true", "glyph must be decorative"
    assert data["shieldExists"], "Privacy Mode must show the shield"
    assert 14 <= data["shieldSize"]["w"] <= 18 and 14 <= data["shieldSize"]["h"] <= 18
    assert data["shieldHidden"] == "true", "shield must be decorative"
    assert data["privacyUnique"] == 1

    # The real QR action still opens the existing modal.
    pro_page.click("#btnShowQr")
    pro_page.wait_for_function(
        """() => {
            const modal = document.getElementById('qrModal');
            return modal && modal.style.display && modal.style.display !== 'none';
        }""",
        timeout=5000,
    )
    pro_page.click("#btnCloseQr")
    pro_page.wait_for_function(
        "document.getElementById('qrModal').style.display === 'none'", timeout=5000
    )


def test_basic_polish_icons_profiles_password(pro_page):
    """Basic uses the shared Pro icon component, uppercase profile labels,
    and discloses no password state."""
    pro_ref = pro_page.evaluate(
        """() => {
            const tip = document.querySelector('[data-field="band_preference"] .tip');
            const cs = getComputedStyle(tip);
            return {glyph: tip.textContent, font: cs.fontSize, weight: cs.fontWeight,
                    lh: cs.lineHeight, w: tip.getBoundingClientRect().width,
                    h: tip.getBoundingClientRect().height, holder: tip.parentElement.className};
        }"""
    )
    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = false;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function("document.body.dataset.uiMode === 'basic'", timeout=15000)
    pro_page.wait_for_timeout(1200)

    basic = pro_page.evaluate(
        """() => {
            const tips = Array.from(document.querySelectorAll(
                '.basic-guided-step-heading .tip')).map((tip) => {
                const cs = getComputedStyle(tip);
                const r = tip.getBoundingClientRect();
                const label = tip.closest('.basic-guided-step-heading')
                    .querySelector('h3').getBoundingClientRect();
                return {glyph: tip.textContent, font: cs.fontSize, weight: cs.fontWeight,
                        lh: cs.lineHeight, w: r.width, h: r.height,
                        holder: tip.parentElement.className,
                        centerDelta: Math.abs((r.top + r.height / 2)
                            - (label.top + label.height / 2)),
                        legacyClass: tip.classList.contains('basic-guided-tip')};
            });
            const profiles = Array.from(document.querySelectorAll(
                'input[name="qos_basic"]')).map((input) => {
                const span = input.nextElementSibling;
                const cs = getComputedStyle(span);
                return {value: input.value, text: span.textContent.trim(),
                        transform: cs.textTransform, spacing: cs.letterSpacing,
                        weight: cs.fontWeight};
            });
            const pass = document.getElementById('wpa2_passphrase_basic');
            const hint = document.getElementById('copyHint');
            const help = document.getElementById('basicGuidedPassSlotHelp');
            return {tips, profiles,
                    passPlaceholder: pass.placeholder,
                    passValue: pass.value,
                    hintText: hint ? hint.textContent : '',
                    helpText: help ? help.textContent : ''};
        }"""
    )
    assert len(basic["tips"]) == 5, "all five Basic step headings carry a tip"
    for tip in basic["tips"]:
        assert tip["glyph"] == pro_ref["glyph"], (
            f"Basic glyph {tip['glyph']!r} must equal Pro {pro_ref['glyph']!r}"
        )
        assert not tip["legacyClass"], "no legacy basic-guided-tip markup may remain"
        assert "tip-only" in tip["holder"], "Basic tips must use the shared holder markup"
        assert tip["font"] == pro_ref["font"] and tip["weight"] == pro_ref["weight"]
        assert tip["lh"] == pro_ref["lh"]
        assert abs(tip["w"] - pro_ref["w"]) < 0.5 and abs(tip["h"] - pro_ref["h"]) < 0.5
        assert tip["centerDelta"] < 1, "icon must share the label's vertical center"

    values = [p["value"] for p in basic["profiles"]]
    assert values == ["off", "ultra_low_latency", "vr"], "preset values untouched"
    assert [p["text"] for p in basic["profiles"]] == ["Standard", "Speed", "Stable"]
    for p in basic["profiles"]:
        assert p["transform"] == "uppercase", f"{p['text']} must render uppercase"
        assert p["spacing"] not in ("normal", "0px"), "letter spacing must be deliberate"

    assert "passphrase saved" not in basic["hintText"].lower()
    assert not any(ch.isdigit() for ch in basic["hintText"])
    assert basic["passValue"] == "", "real password stays out of the DOM until reveal"
    assert basic["helpText"] == "Use 8–63 characters. Control characters are not allowed."

    pro_page.evaluate(
        """() => {
            const t = document.getElementById('uiModeToggle');
            t.checked = true;
            t.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    pro_page.wait_for_function(
        "document.body.dataset.proGuidedStage === 'ready'", timeout=15000
    )
    pro_page.wait_for_timeout(500)


def test_basic_adapter_row_narrow_width(pro_page):
    """At narrow width the selector and Rescan stack cleanly."""
    page = pro_page.context.browser.new_page(
        viewport={"width": 620, "height": 900}, bypass_csp=True
    )
    try:
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
                const t = document.getElementById('uiModeToggle');
                t.checked = false;
                t.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        page.wait_for_function("document.body.dataset.uiMode === 'basic'", timeout=15000)
        page.wait_for_timeout(1200)
        data = page.evaluate(
            """() => {
                const sel = document.getElementById('ap_adapter');
                const rescan = document.getElementById('btnReloadAdapters');
                const sr = sel.getBoundingClientRect();
                const rr = rescan.getBoundingClientRect();
                return {
                    stacked: rr.top >= sr.bottom - 1,
                    selWithin: sr.right <= innerWidth,
                    rescanWithin: rr.right <= innerWidth,
                    rescanVisible: rr.width > 0 && rr.height > 0,
                    overflowX: document.documentElement.scrollWidth
                        <= document.documentElement.clientWidth,
                };
            }"""
        )
    finally:
        page.close()
    assert data["stacked"], "Rescan must stack beneath the selector at narrow width"
    assert data["selWithin"] and data["rescanWithin"], "no clipped controls"
    assert data["rescanVisible"]
    assert data["overflowX"], "no horizontal overflow at narrow width"


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
