from pathlib import Path
import re
import unittest


class TestUiBasicModePayload(unittest.TestCase):
    def test_ui_basic_mode_uses_pick_basic_fields(self):
        src = Path("assets/ui.js").read_text(encoding="utf-8")
        self.assertIn("function pickBasicFields", src)
        # Updated regex to be more whitespace-tolerant
        pattern = re.compile(
            r"function\s+filterConfigForMode\s*\(\s*out\s*\).*?return\s+pickBasicFields\s*\(\s*out\s*\)\s*;",
            re.S,
        )
        self.assertIsNotNone(pattern.search(src))

    def test_basic_mode_ui_labels_and_refresh_contract(self):
        html = Path("assets/index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>Status & Control</h2>", html)
        self.assertNotIn("Status, Control & Connect", html)
        self.assertIn("<h2>Connection Setup</h2>", html)
        self.assertNotIn("<h2>Hotspot Setup</h2>", html)
        self.assertNotIn("<h2>Quick Setup</h2>", html)
        self.assertIn('id="btnCopyPass">Copy passphrase</button>', html)
        self.assertIn('<label for="qos_preset">Connection profiles</label>', html)
        self.assertIn('WPA2 is the standard default. For WPA3, use Pro mode.', html)
        self.assertNotIn('id="btnSaveConfigBasic"', html)
        self.assertNotIn('id="btnSaveRestartBasic"', html)

        basic_refresh = re.search(
            r'<select\s+id="refreshEveryBasic"[^>]*hidden[^>]*>(.*?)</select>',
            html,
            re.S,
        )
        self.assertIsNotNone(basic_refresh)
        opts = basic_refresh.group(1)
        self.assertIn('value="2000"', opts)
        self.assertNotIn('value="3000"', opts)
        self.assertNotIn('value="5000"', opts)

        src = Path("assets/ui.js").read_text(encoding="utf-8")
        self.assertIn("const BASIC_REFRESH_INTERVAL_MS = 2000;", src)
        self.assertIn("if (enabled && getUiMode() === 'basic') {", src)
        self.assertIn("const hideSecurityInBasic = (mode === 'basic' && key === 'ap_security');", src)
        self.assertIn("const hideCountryInBasic = (mode === 'basic' && key === 'country');", src)
        self.assertIn("out.ap_security = BASIC_DEFAULT_SECURITY;", src)
        self.assertIn("out.country = BASIC_DEFAULT_COUNTRY;", src)
        self.assertIn("const BASIC_QOS_DEFAULT = 'ultra_low_latency';", src)
        self.assertIn("scheduleBasicQosAutosave();", src)
