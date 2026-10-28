import unittest

from tests.services.support import ServiceCase, col, wp
from wallman.domain.models import Ref
from wallman.domain.models import Issue
from wallman.errors import NotFound, WallmanError
from wallman.services.remover import Remover


class RemoverTest(ServiceCase):
    def remover(self):
        return Remover(self.cat, self.store, self.vcs, self.uow)

    def test_delete_tracked_uses_git_rm(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}},
                   tracked=[("painting/x", "a.jpg")])
        self.publish_all("painting/x")
        n = self.remover().delete([Ref("painting/x", "a.jpg")])
        self.assertEqual(n, 1)
        a = self.store.path("painting/x", "a.jpg")
        self.assertIn(("stage_rm", [a]), self.vcs.calls)
        self.assertEqual([c for c in self.store.calls if c[0] == "delete"], [])
        self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["b.jpg"])
        self.assertIn(("stage_add", [self.toml("painting/x")]), self.vcs.calls)  # b.jpg: all that is left
        self.assertNotIn("a.jpg", self.vcs.index[self.toml("painting/x")].decode())
        self.assertEqual(self.commits, 1)

    def test_delete_ignored_removes_file(self):
        self.build([col(wallpapers=[wp("a.jpg", license="all-rights-reserved"), wp("b.jpg")])],
                   {"painting/x": {"a.jpg", "b.jpg"}})
        a = self.disk("painting/x", "a.jpg", b"A")
        self.remover().delete([Ref("painting/x", "a.jpg")])
        self.assertIn(("delete", a), self.store.calls)
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_rm"], [])

    def test_delete_last_image_removes_folder_and_toml(self):
        self.build([col(wallpapers=[wp("a.jpg")])], {"painting/x": {"a.jpg"}},
                   tracked=[("painting/x", "a.jpg"), ("painting/x", "wallpapers.toml")])
        self.publish_all("painting/x")
        self.remover().delete([Ref("painting/x", "a.jpg")])
        self.assertNotIn("painting/x", self.cat.data)
        self.assertIn(("remove_dir", "painting/x"), self.store.calls)
        self.assertIn(("unstage_path", self.toml("painting/x")), self.vcs.calls)
        self.assertEqual(self.vcs.index, {})

    def test_delete_last_image_of_external_collection_keeps_empty_toml(self):
        self.build([col("ml4w", [wp("a.jpg")], "ml4w")], {"ml4w": {"a.jpg"}}, external={"ml4w"},
                   tracked=[("ml4w", "a.jpg"), ("ml4w", "wallpapers.toml")])
        self.remover().delete([Ref("ml4w", "a.jpg")])
        self.assertEqual(self.cat.load("ml4w").wallpapers, [])
        self.assertNotIn(("remove_dir", "ml4w"), self.store.calls)
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_rm" and self.toml("ml4w") in c[1]], [])
        self.assertIn(("stage_add", [self.toml("ml4w")]), self.vcs.calls)

    def test_delete_listed_but_not_downloaded_entry(self):
        self.build([col(wallpapers=[wp("a.jpg", download="u", sha256="s"), wp("b.jpg")])], {"painting/x": {"b.jpg"}})
        self.assertEqual(self.remover().delete([Ref("painting/x", "a.jpg")]), 1)
        self.assertEqual([c for c in self.store.calls if c[0] == "delete"], [])
        self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["b.jpg"])

    def test_batch_across_collections(self):
        self.build([col("painting/x", [wp("a.jpg"), wp("b.jpg")]), col("poster/y", [wp("c.jpg")], "poster")],
                   {"painting/x": {"a.jpg", "b.jpg"}, "poster/y": {"c.jpg"}})
        n = self.remover().delete([Ref("painting/x", "a.jpg"), Ref("painting/x", "b.jpg"), Ref("poster/y", "c.jpg")])
        self.assertEqual(n, 3)
        self.assertEqual(self.cat.data, {})
        self.assertEqual(self.commits, 1)

    def test_unknown_ref_raises_and_rolls_back(self):
        self.build([col(wallpapers=[wp("a.jpg")])], {"painting/x": {"a.jpg"}})
        before = self.snapshot()
        with self.assertRaises(NotFound):
            self.remover().delete([Ref("painting/x", "a.jpg"), Ref("painting/x", "zzz.jpg")])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commits, 0)

    def test_structurally_broken_folder_refused_before_git(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}},
                   tracked=[("painting/x", "a.jpg")])
        self.cat.issues["painting/x"] = [Issue("painting/x", "c.jpg", "missing-field", "missing artist", "error")]
        before = self.snapshot()
        with self.assertRaises(WallmanError):
            self.remover().delete([Ref("painting/x", "a.jpg")])
        self.assertEqual(self.vcs.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_unknown_collection_raises(self):
        self.build()
        with self.assertRaises(NotFound):
            self.remover().delete([Ref("painting/none", "a.jpg")])


if __name__ == "__main__":
    unittest.main()
