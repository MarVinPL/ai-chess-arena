"""Regression tests for the explicit, authenticated UI asset routes (Issue #312)."""
import io
import pathlib
import unittest

import server


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ResponseCapture:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}
        self.error = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass

    def send_error(self, status):
        self.error = status


class PanelAssetTests(unittest.TestCase):
    def test_html_references_only_local_external_assets(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="/assets/arena.css">', page)
        self.assertIn('<script src="/assets/arena.js"></script>', page)
        self.assertNotIn("<style>", page)
        self.assertNotIn("<script>", page)
        self.assertTrue(page.rstrip().endswith("</script></body></html>"))

    def test_js_and_css_match_expected_content_types(self):
        for url, path, content_type in (
            ("/assets/arena.css", "web/css/arena.css", "text/css; charset=utf-8"),
            ("/assets/arena.js", "web/js/arena.js", "text/javascript; charset=utf-8"),
        ):
            with self.subTest(url=url):
                response = ResponseCapture()
                server.Handler.send_panel_asset(response, url)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.wfile.getvalue(), (ROOT / path).read_bytes())

    def test_unlisted_and_traversal_paths_are_rejected(self):
        for path in (
            "/assets/../server.py", "/assets/.env", "/assets/secrets.json",
            "/assets/arena.css/../../arena.sqlite3", "/assets/", "/assets/private.js",
        ):
            with self.subTest(path=path):
                response = ResponseCapture()
                server.Handler.send_panel_asset(response, path)
                self.assertEqual(response.error, 404)
                self.assertEqual(response.wfile.getvalue(), b"")

    def test_routes_require_authentication_before_asset_dispatch(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        start = source.index("    def do_GET(self):")
        segment = source[start:source.index("        if path == '/api/model-permissions':", start)]
        self.assertLess(segment.index("if not user:"), segment.index("self.send_panel_asset(path)"))
        self.assertIn("if path.startswith('/assets/'):", segment)


if __name__ == "__main__":
    unittest.main()
