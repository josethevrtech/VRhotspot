from pathlib import Path
import re
import unittest


class TestUiLoginSplashContract(unittest.TestCase):
    def test_ui_has_login_splash_contract(self):
        src = Path("assets/ui.js").read_text(encoding="utf-8")
        self.assertIn("login-splash", src)
        self.assertRegex(src, r"function\s+renderLoginSplash\s*\(")

        startup_guard = re.compile(
            r"const\s+companionBridge\s*=\s*companionAuthBridgeAvailable\(\)\s*;"
            r".*?if\s*\(\s*companionBridge\s*\).*?"
            r"requestCompanionAuthToken\(\).*?"
            r"else\s*\{\s*tok\s*=\s*migrateLegacyToken\(\)\s*\|\|\s*getStoredToken\(\)\s*;"
            r".*?if\s*\(\s*!tok\s*\)\s*\{\s*renderLoginSplash\(\)\s*;\s*return\s*;",
            re.S,
        )
        self.assertIsNotNone(startup_guard.search(src))

    def test_companion_auth_bridge_uses_fixed_origin_memory_only_contract(self):
        src = Path("assets/ui.js").read_text(encoding="utf-8")

        self.assertIn(
            "const COMPANION_AUTH_ORIGIN = 'http://127.0.0.1:8732';",
            src,
        )
        self.assertIn(
            "const COMPANION_AUTH_HANDLER = 'vrHotspotCompanionAuth';",
            src,
        )
        self.assertIn("path !== '/ui'", src)
        self.assertIn("!path.startsWith('/assets/')", src)
        self.assertIn("type: 'token_request'", src)
        self.assertIn("type: 'auth_accepted'", src)
        self.assertIn("type: 'auth_cleared'", src)
        self.assertIn("companionSessionToken = token;", src)
        self.assertIn(
            "clearStoredTokenEverywhere({ notifyCompanion: false });",
            src,
        )
        self.assertNotIn("COMPANION_AUTH_ORIGIN + '?'", src)
        self.assertNotIn("COMPANION_AUTH_ORIGIN + '#'", src)
