from pathlib import Path
import re
import unittest


class TestUiLoginSplashContract(unittest.TestCase):
    def test_ui_has_login_splash_contract(self):
        src = Path("assets/ui.js").read_text(encoding="utf-8")
        self.assertIn("login-splash", src)
        self.assertRegex(src, r"function\s+renderLoginSplash\s*\(")

        startup_guard = re.compile(
            r"const\s+tok\s*=\s*migrateLegacyToken\(\)\s*\|\|\s*getStoredToken\(\)\s*;"
            r".*?if\s*\(\s*!tok\s*\)\s*\{\s*renderLoginSplash\(\)\s*;\s*return\s*;",
            re.S,
        )
        self.assertIsNotNone(startup_guard.search(src))
