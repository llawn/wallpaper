"""License docs (spec 3.5): renderer safety, doc completeness, pages."""
import unittest
from pathlib import Path

from tests.web.test_api import ApiBase
from wallman.domain.licenses import LICENSES, SHAREABLE
from wallman.web.markdown import parse_front_matter, render_markdown

DOCS = Path(__file__).resolve().parents[2] / "wallman" / "docs" / "licenses"
SECTIONS = ["In short", "You may", "You must", "You cannot", "In this collection"]


class RendererTest(unittest.TestCase):
    def test_escapes_html(self):
        out = render_markdown("<script>alert(1)</script>\n\n# <b>x</b>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("<h1>&lt;b&gt;x&lt;/b&gt;</h1>", out)

    def test_blocks_and_inline(self):
        out = render_markdown("## T\n\nA **bold** and *it* and `c<d`.\n\n- one\n- two\n\n1. a\n2. b\n\n### S")
        for frag in ("<h2>T</h2>", "<strong>bold</strong>", "<em>it</em>", "<code>c&lt;d</code>", "<ul><li>one</li><li>two</li></ul>",
                     "<ol><li>a</li><li>b</li></ol>", "<h3>S</h3>"):
            self.assertIn(frag, out)

    def test_links(self):
        ok = render_markdown("[x](https://example.org/a?b=1&c=2) [y](/docs/licenses/MIT)")
        self.assertIn('<a href="https://example.org/a?b=1&amp;c=2"', ok)
        self.assertIn('<a href="/docs/licenses/MIT"', ok)
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "//evil.org", "/other", "/docs/../x"):
            out = render_markdown(f"[x]({bad})")
            self.assertNotIn("<a ", out, bad)
            self.assertNotIn("href", out, bad)
        self.assertNotIn('"onmouseover', render_markdown('[x](https://a.b/"onmouseover=1)'))
        self.assertNotIn("<a ", render_markdown('[x](https://a.b/" onmouseover=1)'))

    def test_front_matter(self):
        meta, body = parse_front_matter("---\nname: A: b\nshareable: true\n---\n# H\n")
        self.assertEqual(meta, {"name": "A: b", "shareable": "true"})
        self.assertEqual(body.strip(), "# H")
        self.assertEqual(parse_front_matter("# H")[0], {})


class DocsCompleteTest(unittest.TestCase):
    def test_every_license_has_a_valid_doc(self):
        for lid in sorted(LICENSES):
            path = DOCS / f"{lid}.md"
            self.assertTrue(path.is_file(), lid)
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
            self.assertEqual(set(meta), {"name", "url", "shareable", "kind"}, lid)
            self.assertTrue(meta["name"], lid)
            self.assertTrue(meta["url"].startswith("https://") or (meta["url"] == "" and lid in {"all-rights-reserved", "personal", "public-domain"}), lid)
            self.assertEqual(meta["shareable"], "true" if lid in SHAREABLE else "false", lid)
            self.assertIn(meta["kind"], {"open", "specific", "public-domain"}, lid)
            for s in SECTIONS:
                self.assertIn(f"## {s}", body, f"{lid}: {s}")

    def test_collection_section_matches_shareable(self):
        for lid in sorted(LICENSES):
            _, body = parse_front_matter((DOCS / f"{lid}.md").read_text(encoding="utf-8"))
            section = body.split("## In this collection", 1)[1]
            if lid in SHAREABLE:
                self.assertIn("Committed to git", section, lid)
                self.assertNotIn("Download-only", section, lid)
            else:
                self.assertIn("Download-only", section, lid)
                self.assertNotIn("Committed to git", section, lid)

    def test_no_stray_docs(self):
        self.assertEqual({p.stem for p in DOCS.glob("*.md")}, set(LICENSES))

    def test_open_licenses_have_texts(self):
        for lid in sorted(LICENSES):
            meta, _ = parse_front_matter((DOCS / f"{lid}.md").read_text(encoding="utf-8"))
            text = DOCS / "texts" / f"{lid}.txt"
            if meta["kind"] == "open":
                self.assertGreater(len(text.read_text(encoding="utf-8")), 500, lid)
            else:
                self.assertFalse(text.exists(), lid)
        self.assertEqual({p.stem for p in (DOCS / "texts").glob("*.txt")} - LICENSES, set())


class DocsPagesTest(ApiBase):
    def test_index_lists_everything_grouped(self):
        status, headers, body = self.call("GET", "/docs/licenses")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("/api/theme", page)
        for lid in LICENSES:
            self.assertIn(f'href="/docs/licenses/{lid}"', page)
        for kind in ("Open licenses", "Specific terms", "Public domain"):
            self.assertIn(kind, page)
        self.assertIn("Shareable", page)

    def test_trailing_slash_serves_index(self):
        status, _, body = self.call("GET", "/docs/licenses/")
        self.assertEqual(status, 200)
        self.assertIn("Open licenses", body.decode())

    def test_license_page_has_summary_and_text(self):
        status, _, body = self.call("GET", "/docs/licenses/MIT")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("In short", page)
        self.assertIn("<pre", page)
        self.assertIn("Permission is hereby granted", page)
        status, _, body = self.call("GET", "/docs/licenses/personal")
        self.assertEqual(status, 200)
        self.assertNotIn("<pre", body.decode())

    def test_text_is_escaped(self):
        _, _, body = self.call("GET", "/docs/licenses/BSD-3-Clause")
        page = body.decode()
        self.assertIn("&lt;year&gt; &lt;owner&gt;", page)
        self.assertNotIn("<year>", page)

    def test_unknown_and_traversal_404(self):
        for p in ("/docs/licenses/nope", "/docs/licenses/..%2Fx", "/docs/licenses/MIT/extra", "/docs/licenses/mit"):
            status, headers, body = self.call("GET", p)
            self.assertEqual(status, 404, p)
            self.assertTrue(headers["Content-Type"].startswith("text/html"), p)

    def test_auth_required(self):
        for p in ("/docs/licenses", "/docs/licenses/MIT", "/docs/licenses/nope"):
            self.assertEqual(self.call("GET", p, token=False)[0], 401, p)

    def test_cookie_auth(self):
        status, _, _ = self.call("GET", "/docs/licenses", token=False, headers={"Cookie": f"wallman_token={self.token}"})
        self.assertEqual(status, 200)

    def test_post_rejected(self):
        self.assertEqual(self.call("POST", "/docs/licenses", body={})[0], 405)

    def test_api_licenses_has_doc_link(self):
        _, lic = self.api("GET", "/api/licenses")
        self.assertTrue(all(l["doc"] == f"/docs/licenses/{l['id']}" for l in lic))
