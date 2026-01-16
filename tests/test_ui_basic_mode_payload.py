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
