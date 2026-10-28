import os
import tempfile
import unittest
from pathlib import Path

from wallman.adapters.fs_store import FsStore
from wallman.errors import PathOutsideRepo


class FsStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = FsStore(self.root)

    def mk(self, rel, text=""):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_path_confinement(self):
        for bad in ("../x", "/etc", "a/../../x"):
            with self.assertRaises(PathOutsideRepo):
                self.store.resolve(bad)
        with self.assertRaises(PathOutsideRepo):
            self.store.resolve("painting/x", "../../../etc")
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside)
        with self.assertRaises(PathOutsideRepo):
            self.store.resolve("link", "f.png")
        with self.assertRaises(PathOutsideRepo):
            self.store.path("link", "f.png")
        self.assertEqual(self.store.resolve("painting", "a.jpg"), self.root / "painting" / "a.jpg")

    def test_move_delete_confined(self):
        a = self.mk("painting/a.png")
        self.store.move(a, self.root / "painting" / "b.png")
        self.assertTrue((self.root / "painting/b.png").exists())
        self.store.delete(self.root / "painting/b.png")
        self.assertFalse((self.root / "painting/b.png").exists())
        with self.assertRaises(PathOutsideRepo):
            self.store.delete(Path("/etc/hostname"))
        with self.assertRaises(PathOutsideRepo):
            self.store.move(self.mk("painting/c.png"), Path(self._tmp.name) / "c.png")

    def test_remove_dir_only_when_empty(self):
        a = self.mk("painting/x/a.png")
        self.store.remove_dir("painting/x")
        self.assertTrue((self.root / "painting/x").is_dir())
        a.unlink()
        self.store.remove_dir("painting/x")
        self.assertFalse((self.root / "painting/x").exists())
        self.store.remove_dir("painting/x")  # already gone: no error

    def test_images_sorted_case_insensitive(self):
        for n in ("b.png", "a.JPG", "c.jpeg", "d.txt", "e.svg"):
            self.mk(f"painting/x/{n}")
        (self.root / "painting/x/sub").mkdir()
        self.mk("painting/x/sub/z.png")
        self.assertEqual(self.store.images("painting/x"), ["a.JPG", "b.png", "c.jpeg"])

    def test_collection_folders_skip_submodule_without_toml(self):
        self.mk("painting/impress/wallpapers.toml")
        self.mk("painting/.hidden/wallpapers.toml")
        self.mk("painting/sub/.git", "gitdir: x")
        self.mk("painting/sub/wallpapers.toml")
        self.mk("painting/sub/deeper/wallpapers.toml")
        self.mk("painting/nogit/.git", "gitdir: x")
        self.mk("painting/nogit/inner/wallpapers.toml")
        self.mk("other/x/wallpapers.toml")
        self.mk("linux/a/b/wallpapers.toml")
        self.assertEqual(self.store.collection_folders(),
                         ["linux/a/b", "painting/impress", "painting/sub"])

    def test_image_folders_include_toml_less_and_skip_submodule_without_toml(self):
        self.mk("painting/new/a.jpg")
        self.mk("painting/old/wallpapers.toml")
        self.mk("painting/old/b.png")
        self.mk("painting/empty/wallpapers.toml")
        self.mk("painting/.hidden/c.jpg")
        self.mk("painting/ext/.git", "gitdir: x")
        self.mk("painting/ext/d.jpg")
        self.mk("painting/sub/.git", "gitdir: x")
        self.mk("painting/sub/wallpapers.toml")
        self.mk("painting/sub/e.jpg")
        self.mk("painting/sub/deeper/f.jpg")
        self.mk("other/g.jpg")
        self.assertEqual(self.store.image_folders(), ["painting/new", "painting/old", "painting/sub"])

    def test_external_detected(self):
        self.mk("painting/sub/.git", "gitdir: x")
        self.mk("painting/sub/wallpapers.toml")
        self.mk("painting/plain/wallpapers.toml")
        self.assertTrue(self.store.is_external("painting/sub"))
        self.assertFalse(self.store.is_external("painting/plain"))


if __name__ == "__main__":
    unittest.main()


class CollectionFoldersCacheTest(unittest.TestCase):
    """collection_folders() keeps each directory's listing while its mtime is unchanged: every change it can
    report (a toml or .git created or removed, a folder added, removed or renamed) is seen on the next call."""

    def test_changes_are_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = FsStore(root)
            (root / "painting" / "a").mkdir(parents=True)
            (root / "painting" / "a" / "wallpapers.toml").write_text("")
            for i in range(50):
                (root / "painting" / "a" / f"img{i}.jpg").write_text("x")
            self.assertEqual(store.collection_folders(), ["painting/a"])
            self.assertEqual(store.collection_folders(), ["painting/a"])
            (root / "painting" / "b").mkdir()
            self.assertEqual(store.collection_folders(), ["painting/a"])
            (root / "painting" / "b" / "wallpapers.toml").write_text("")
            self.assertEqual(store.collection_folders(), ["painting/a", "painting/b"])
            (root / "painting" / "b").rename(root / "painting" / "c")
            self.assertEqual(store.collection_folders(), ["painting/a", "painting/c"])
            (root / "painting" / "c" / "deep").mkdir()
            (root / "painting" / "c" / "deep" / "wallpapers.toml").write_text("")
            self.assertEqual(store.collection_folders(), ["painting/a", "painting/c", "painting/c/deep"])
            (root / "painting" / "c" / ".git").mkdir()  # an external collection: nothing below it
            self.assertEqual(store.collection_folders(), ["painting/a", "painting/c"])
            (root / "painting" / "a" / "wallpapers.toml").unlink()
            self.assertEqual(store.collection_folders(), ["painting/c"])
