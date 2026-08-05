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
            const step3Tip = document.querySelector(
                '[data-field="band_preference"] .tip').getBoundingClientRect();
            return {
                tips,
                introGone: !document.querySelector('#proStepAdvanced .pro-advanced-group-help'),
                step3: {w: step3Tip.width, h: step3Tip.height},
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
    basic_tip = pro_page.evaluate(
        """() => {
            const tip = Array.from(document.querySelectorAll('.tip')).find(
                (node) => node.getBoundingClientRect().width > 0);
            if (!tip) return null;
            const r = tip.getBoundingClientRect();
            return {w: r.width, h: r.height};
        }"""
    )
    assert basic_tip, "no visible info icon found in Basic mode"
    assert abs(basic_tip["w"] - 16) < 1 and abs(basic_tip["h"] - 16) < 1, (
        f"Basic icon is {basic_tip['w']:.1f}x{basic_tip['h']:.1f}, expected 16x16"
    )

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
                saveRestartVisible: r(saveRestart).width > 0,
                repairInStep5: !!document.querySelector('#proStepAction #btnRepair'),
                repairInRecovery: !!document.querySelector(
                    '#tab-troubleshooting .troubleshooting-actions #btnRepair'),
                visibleCount: visible.length,
                secondaryTrack: !!document.querySelector(
                    '#proStepAction .pro-guided-secondary-actions'),
                dupes: ['btnStart', 'btnSaveConfig', 'btnSaveRestart', 'btnRepair'].map(
                    (id) => document.querySelectorAll(`[id="${id}"]`).length),
                stateCopyLeft: r(action.firstElementChild).x < r(buttons).x,
            };
        }"""
    )
    assert data["startText"] in ("Start Hotspot", "Stop Hotspot")
    assert data["saveRestartVisible"] is False, "Save & Restart must not be visible"
    assert data["repairInStep5"] is False, "Repair Network must not be in Step 5"
    assert data["repairInRecovery"], "Repair Network must sit in Troubleshooting recovery actions"
    assert data["visibleCount"] == 2, "action column must hold exactly primary + save row"
    assert data["secondaryTrack"] is False, "no leftover secondary track"
    assert data["dupes"] == [1, 1, 1, 1]
    assert data["stateCopyLeft"], "status copy must stay aligned on the left"
    # Primary is full-width in its column; Save Changes aligns beneath it.
    assert abs(data["startRect"]["x"] - data["saveRect"]["x"]) < 1
    assert data["saveRect"]["w"] <= data["startRect"]["w"] + 1
    assert data["saveRect"]["top"] > data["startRect"]["top"]

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
