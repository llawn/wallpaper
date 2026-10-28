"""The UI's static files are served from the real static dir with the right MIME types and no external code."""
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from tests.cli.test_cli import git
from tests.fakes import FakeFetcher
from tests.services import fixture
from wallman import bootstrap
from wallman.web.server import STATIC, make_server

FILES = {
    "index.html": "text/html",
    "style.css": "text/css",
    **{name: "text/javascript" for name in (
        "app.js", "api.js", "tree.js", "gallery.js", "details.js", "years.js", "add-dialog.js", "cropper.js", "status.js",
        "selection.js", "virtual-grid.js", "select-menu.js", "toolbar.js", "dialogs.js", "meta-form.js",
        "folder-picker.js", "year-input.js", "tag-input.js", "viewer.js")},
}


class StaticTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve() / "repo"
        (root / "images").mkdir(parents=True)
        fixture.write_collection(root / "images", "poster", "poster", "film", [
            fixture.entry("studio_poster_2001.jpg", "Poster", "Some Studio", "CC0-1.0", 2001)])
        git(root, "init", "-q")
        app = bootstrap.build(root, Path(tmp.name) / "cache", FakeFetcher())
        self.server, self.token = make_server(app, 0)  # the real static dir
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever)
        t.start()
        self.addCleanup(t.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get_content_type(), r.read().decode()

    def index(self):
        return self.get("/", {"Cookie": f"wallman_token={self.token}"})

    def test_static_files_served_with_mime(self):
        status, ctype, _ = self.index()
        self.assertEqual((status, ctype), (200, "text/html"))
        for name, expected in FILES.items():
            with self.subTest(name=name):
                status, ctype, body = self.get(f"/static/{name}")
                self.assertEqual((status, ctype), (200, expected))
                self.assertTrue(body.strip())

    def test_index_has_no_external_scripts(self):
        _, _, html = self.index()
        refs = re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="([^"]+)"', html)
        self.assertIn("/static/app.js", refs)
        self.assertIn("/static/style.css", refs)
        for ref in refs:
            self.assertTrue(ref.startswith(("/static/", "/api/theme?scheme=", "/docs/")), ref)
        self.assertIn("/api/theme?scheme=dark", refs)
        self.assertIn("/api/theme?scheme=light", refs)
        self.assertNotRegex(html, r"https?://")
        self.assertRegex(html, r'<script type="module" src="/static/app.js">')

    def test_theme_stylesheet_served(self):
        for scheme in ("dark", "light"):
            status, ctype, css = self.get(f"/api/theme?scheme={scheme}", {"Cookie": f"wallman_token={self.token}"})
            self.assertEqual((status, ctype), (200, "text/css"))
            for var in ("--m-primary:", "--m-surface:", "--container:", "--container-high:", "--primary-container:",
                        "--focus:", "--scrim:"):
                self.assertIn(var, css)

    def test_no_native_selects_or_inner_html(self):
        _, _, html = self.index()
        self.assertNotIn("<select", html)
        for name in FILES:
            if name.endswith(".js"):
                self.assertNotIn("innerHTML", (STATIC / name).read_text(), name)

    def test_no_inline_handlers_or_external_urls_in_sources(self):
        for name in FILES:
            with self.subTest(name=name):
                text = (STATIC / name).read_text()
                handler = re.search(r"\son[a-z]+\s*=\s*[\"']", text)
                self.assertIsNone(handler, f"inline event handler: {handler and handler.group()}")
                url = re.search(r"https?://[\w-]+\.[\w.-]+", text)  # a real host, not prose like "http:// or"
                self.assertIsNone(url, f"external URL: {url and url.group()}")
                if name.endswith(".js"):
                    for spec in re.findall(r'\bfrom\s+"([^"]+)"', text):
                        self.assertRegex(spec, r"^\./[a-z-]+\.js$")
                        self.assertTrue((STATIC / spec[2:]).is_file(), spec)


if __name__ == "__main__":
    unittest.main()
