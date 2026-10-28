import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeImageTool, FakeStore
from wallman.domain.models import Collection, Issue, Resolution, Wallpaper
from wallman.services.validator import Validator

R = Resolution(3840, 2160)


def wp(file, license="public-domain", res=R):
    return Wallpaper(file=file, title="t", artist="a", license=license, resolution=res)


class ValidatorTest(unittest.TestCase):
    def test_reports_known_issue_codes(self):
        good = Collection("painting/good", "painting", "good", [wp("a_b.jpg")])
        bad = Collection("painting/bad", "painting", "bad", [
            wp("a_small.jpg", res=Resolution(1000, 500)), wp("a_gone.jpg"), wp("Bad Name.jpg")])
        cat = FakeCatalogue([good, bad])
        cat.issues["painting/bad"] = [Issue("painting/bad", None, "count", "count wrong", "error")]
        store = FakeStore({
            "painting/good": {"a_b.jpg"},
            "painting/bad": {"a_small.jpg", "Bad Name.jpg", "extra.jpg", "broken.jpg"},
            "painting/broken": set(),
        })
        cat.issues["painting/broken"] = [Issue("painting/broken", None, "invalid-toml", "x", "error")]
        tool = FakeImageTool({
            store.path("painting/good", "a_b.jpg"): R,
            store.path("painting/bad", "a_small.jpg"): Resolution(1000, 500),
            store.path("painting/bad", "Bad Name.jpg"): R,
            store.path("painting/bad", "extra.jpg"): R,
        })
        issues = Validator(cat, store, tool).run()
        codes = {(i.folder, i.file, i.code) for i in issues}
        self.assertEqual({i.folder for i in issues if i.folder == "painting/good"}, set())
        for expected in [
            ("painting/bad", None, "count"),
            ("painting/bad", "a_small.jpg", "too-small"),
            ("painting/bad", "a_gone.jpg", "missing"),
            ("painting/bad", "Bad Name.jpg", "name"),
            ("painting/bad", "extra.jpg", "unlisted"),
            ("painting/bad", "broken.jpg", "invalid-image"),
            ("painting/broken", None, "invalid-toml"),
        ]:
            self.assertIn(expected, codes)
        inv = next(i for i in issues if i.code == "invalid-image")
        self.assertEqual(inv.level, "error")

    def test_original_resolved_from_repo_root(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        images = repo / "images"
        w = Wallpaper(file="a_b.jpg", title="t", artist="a", license="public-domain", resolution=R,
                      original="../../../_originals/a_b.jpg")
        store = FakeStore({"painting/x": {"a_b.jpg"}}, root=images)
        tool = FakeImageTool({store.path("painting/x", "a_b.jpg"): R})
        run = lambda: {i.code for i in Validator(FakeCatalogue([Collection("painting/x", "painting", "x", [w])]),
                                                 store, tool).run()}
        self.assertIn("original-missing", run())
        (repo / "_originals").mkdir()
        (repo / "_originals/a_b.jpg").write_bytes(b"o")
        self.assertNotIn("original-missing", run())


if __name__ == "__main__":
    unittest.main()
