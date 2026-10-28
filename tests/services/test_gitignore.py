import dataclasses
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue
from wallman.domain.models import Collection, Resolution, Wallpaper
from wallman.services.gitignore import BEGIN, END, LEGACY_BEGIN, GitignoreService

R = Resolution(3840, 2160)


def wp(file, license, original=None):
    return Wallpaper(file=file, title="t", artist="a", license=license, resolution=R, original=original)


class GitignoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cat = FakeCatalogue([
            Collection("painting/a", "painting", "a", [
                wp("pd.jpg", "public-domain"), wp("x_one.jpg", "all-rights-reserved", "../../../_originals/o.jpg"),
                wp("y_two.jpg", "official-wallpaper")]),
            Collection("ml4w", "ml4w", "ml4w", [wp("ext.jpg", "personal")], external=True),
        ])
        self.svc = GitignoreService(self.root, self.cat)

    def lines(self):
        return (self.root / ".gitignore").read_text().splitlines()

    def test_block_lists_only_non_shareable(self):
        n = self.svc.write()
        self.assertEqual(n, 3)
        self.assertEqual(self.lines()[1:2], ["# not redistributable: just fetch downloads them"])
        self.assertEqual(self.lines()[0], BEGIN)
        self.assertEqual(self.lines()[2:-1], ["/_originals/o.jpg", "/images/painting/a/x_one.jpg", "/images/painting/a/y_two.jpg"])
        self.assertEqual(self.lines()[-1], END)

    def test_legacy_marker_is_replaced(self):
        (self.root / ".gitignore").write_text(
            f"keep\n\n{LEGACY_BEGIN}\n/old.jpg\n{END}\ntail\n", encoding="utf-8")
        self.svc.write()
        text = (self.root / ".gitignore").read_text()
        self.assertNotIn(LEGACY_BEGIN, text)
        self.assertNotIn("/old.jpg", text)
        self.assertEqual(text.count(BEGIN), 1)
        self.assertIn("keep", text)
        self.assertIn("tail", text)

    def test_block_preserves_user_lines(self):
        (self.root / ".gitignore").write_text(f"*.pyc\n\n{BEGIN}\n/stale\n{END}\n.venv/\n")
        self.svc.write()
        self.svc.write()  # idempotent
        text = (self.root / ".gitignore").read_text()
        self.assertTrue(text.startswith("*.pyc\n\n" + BEGIN))
        self.assertTrue(text.endswith(END + "\n.venv/\n"))
        self.assertNotIn("/stale", text)
        self.assertEqual(text.count(BEGIN), 1)

    def test_external_collections_excluded(self):
        self.cat.data["ml4w"].wallpapers[0] = wp("ext.jpg", "all-rights-reserved")
        self.svc.write()
        self.assertNotIn("/images/ml4w/ext.jpg", self.lines())

    def test_check_staged_refuses_unlisted_and_non_shareable(self):
        out = self.svc.check_staged([
            Path("images/painting/a/pd.jpg"), Path("images/painting/a/x_one.jpg"), Path("images/painting/a/unknown.PNG"),
            Path("images/painting/a/wallpapers.toml"), self.root / "images/painting/a/y_two.jpg"])
        self.assertEqual(out[0], "✗ commit refused: these images may not be redistributed")
        self.assertEqual(out[1:4], [
            "  images/painting/a/x_one.jpg  (all-rights-reserved)",
            "  images/painting/a/unknown.PNG  (not listed in any wallpapers.toml)",
            "  images/painting/a/y_two.jpg  (official-wallpaper)"])
        self.assertEqual(len(out), 6)
        self.assertEqual(self.svc.check_staged([Path("images/painting/a/pd.jpg"), Path("README.md")]), [])

    def test_originals_live_at_repo_root(self):
        out = self.svc.check_staged([Path("_originals/o.jpg")])
        self.assertEqual(out[1], "  _originals/o.jpg  (all-rights-reserved)")


if __name__ == "__main__":
    unittest.main()


class IncrementalGitignoreTest(unittest.TestCase):
    """The server keeps one GitignoreService: after each edit only the changed folders are recomputed, and the file
    must be byte-identical to what a fresh service writes for the same data."""

    def setUp(self):
        from wallman.adapters.fs_store import FsStore
        from wallman.adapters.toml_catalogue import TomlCatalogue
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        images = self.root / "images"
        for folder in ("painting/a", "painting/b", "photography/c"):
            (images / folder).mkdir(parents=True)
        self.cat = TomlCatalogue(images, FsStore(images))
        self.cat.save(Collection("painting/a", "painting", "a", [
            wp("pd.jpg", "public-domain"), wp("x_one.jpg", "all-rights-reserved", "../../../_originals/o.jpg")]))
        self.cat.save(Collection("painting/b", "painting", "b", [wp(f"b{i}.jpg", "official-wallpaper") for i in range(50)]))
        self.cat.save(Collection("photography/c", "photography", "c", [wp("c.jpg", "CC-BY-4.0")]))
        (self.root / ".gitignore").write_text("*.pyc\n")
        self.svc = GitignoreService(self.root, self.cat)

    def fresh_bytes(self):
        from wallman.adapters.fs_store import FsStore
        from wallman.adapters.toml_catalogue import TomlCatalogue
        mine = (self.root / ".gitignore").read_bytes()
        images = self.root / "images"
        GitignoreService(self.root, TomlCatalogue(images, FsStore(images))).write()  # parses every toml again
        return mine, (self.root / ".gitignore").read_bytes()

    def test_incremental_write_is_byte_identical_to_a_fresh_one(self):
        from unittest import mock
        from wallman.services import gitignore
        self.svc.write()
        mine, fresh = self.fresh_bytes()
        self.assertEqual(mine, fresh)
        restricted = lambda ws, k, lic: ws[:k] + [Wallpaper(file=ws[k].file, title="t", artist="a", license=lic,  # noqa: E731
                                                            resolution=R)] + ws[k + 1:]
        edits = [  # (folder, edit, recomputed whole?)
            ("photography/c", lambda ws: restricted(ws, 0, "all-rights-reserved"), False),  # becomes restricted
            ("painting/b", lambda ws: restricted(ws, 7, "CC0-1.0"), False),  # one of 50 no longer restricted
            ("painting/b", lambda ws: ws[:3] + [dataclasses.replace(ws[3], file="b3-renamed.jpg")] + ws[4:], False),
            ("photography/c", lambda ws: restricted(ws, 0, "CC-BY-4.0"), False),  # licence round trip: back ...
            ("photography/c", lambda ws: restricted(ws, 0, "all-rights-reserved"), False),  # ... and restricted again
            ("painting/b", lambda ws: ws[:10] + [wp("b99.jpg", "CC0-1.0", "../../../_originals/b99.jpg")], True),
            ("painting/a", lambda ws: [wp("x_one.jpg", "public-domain")], True),  # original gone
            ("painting/b", lambda ws: ws[:1] + [wp("b98.jpg", "official-wallpaper", "../../../_originals/b99.jpg")]
             + ws[2:], True),  # two entries list the same original: which one wins depends on the order
            ("painting/b", lambda ws: restricted(ws, 0, "official-wallpaper"), True),  # still a duplicate path
        ]
        for folder, edit, whole in edits:
            with self.subTest(folder=folder, whole=whole):
                c = self.cat.load(folder)
                c.wallpapers = edit(c.wallpapers)
                self.cat.save(c)
                with mock.patch.object(gitignore, "_folder_licenses", wraps=gitignore._folder_licenses) as spy:
                    self.svc.write()
                self.assertEqual([call.args[0].folder for call in spy.call_args_list], [folder] if whole else [])
                mine, fresh = self.fresh_bytes()
                self.assertEqual(mine, fresh)
                self.assertTrue(mine.startswith(b"*.pyc\n\n" + BEGIN.encode()))
        self.assertIn(b"/images/painting/b/b3-renamed.jpg\n", mine)
        self.cat.delete("painting/b")  # a whole folder gone: its memo must not leak into the file
        self.svc.write()
        mine, fresh = self.fresh_bytes()
        self.assertEqual(mine, fresh)
        self.assertNotIn(b"/images/painting/b/", mine)
        self.assertIn(b"/images/photography/c/c.jpg\n", mine)
