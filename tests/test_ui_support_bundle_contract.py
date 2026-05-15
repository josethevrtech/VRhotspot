from pathlib import Path
import unittest


class TestUiSupportBundleContract(unittest.TestCase):
    def test_support_bundle_download_contract(self):
        html = Path("assets/index.html").read_text(encoding="utf-8")
        self.assertIn("Support Bundle", html)
        self.assertIn('id="btnDownloadSupportBundle"', html)
        self.assertIn('id="supportBundleMsg"', html)

        src = Path("assets/ui.js").read_text(encoding="utf-8")
        self.assertIn("async function apiBlob", src)
        self.assertIn("function filenameFromContentDisposition", src)
        self.assertIn("function safeZipFilename", src)
        self.assertIn("async function downloadSupportBundle", src)
        self.assertIn("apiBlob('/v1/diagnostics/support_bundle', { method: 'GET' })", src)
        self.assertIn("res.headers.get('Content-Disposition')", src)
        self.assertIn("link.download = filename", src)
        self.assertIn("btnDownloadSupportBundle.addEventListener('click', downloadSupportBundle)", src)
        self.assertIn("Your session expired. Sign in again to download the support bundle.", src)

        css = Path("assets/ui.css").read_text(encoding="utf-8")
        self.assertIn(".support-bundle-row", css)
