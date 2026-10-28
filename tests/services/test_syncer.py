import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeImageTool, FakeStore, FakeVcs
from wallman.domain.models import Collection, Ref, Resolution, Wallpaper
from wallman.domain.years import YearSpan
from wallman.services.syncer import Syncer
from wallman.services.unit_of_work import UnitOfWork

R = Resolution(2560, 1440)


def wp(file, **kw):
    return Wallpaper(file=file, title="T", artist="A", license="public-domain", resolution=R, **kw)


class SyncerTest(unittest.TestCase):
    def build(self, collections, files, external=(), sizes=None, tracked=()):
        self.cat = FakeCatalogue(collections)
        self.store = FakeStore(files, set(external))
        self.tool = FakeImageTool({self.store.path(f, n): r for (f, n), r in (sizes or {}).items()})
        self.vcs = FakeVcs({self.store.path(f, n) for f, n in tracked})
        self.recorded = []
        cat, store, vcs = self.cat, self.store, self.vcs

        def factory():
            u = UnitOfWork(cat, store, vcs, lambda uow: None)
            rf, rc = u.record_file, u.record_collection
            u.record_file = lambda p: (self.recorded.append(("file", p)), rf(p))[1]
            u.record_collection = lambda f: (self.recorded.append(("coll", f)), rc(f))[1]
            return u
        self.syncer = Syncer(self.cat, self.store, self.tool, self.vcs, factory)

    def col(self, folder="painting/x", wallpapers=()):
        return Collection(folder=folder, medium="painting", movement="x", wallpapers=list(wallpapers))

    def test_deleted_tracked_file_removal_is_staged(self):
        self.build([self.col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg"}},
                   tracked=[("painting/x", "a.jpg"), ("painting/x", "b.jpg")])
        self.syncer.sync(stage=True)
        self.assertIn(("stage_rm", [self.store.path("painting/x", "b.jpg")]), self.vcs.calls)

    def test_deleted_file_removal_not_staged_without_stage(self):
        self.build([self.col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg"}},
                   tracked=[("painting/x", "b.jpg")])
        self.syncer.sync(stage=False)
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_rm"], [])

    def test_unnameable_file_is_kept_as_is(self):
        self.build([self.col()], {"painting/x": {"!!!.jpg"}}, sizes={("painting/x", "!!!.jpg"): R})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.added, [Ref("painting/x", "!!!.jpg")])
        self.assertEqual(rep.renamed, [])

    def test_deleted_file_entry_removed(self):
        self.build([self.col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg"}})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.removed, [Ref("painting/x", "b.jpg")])
        self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["a.jpg"])
        self.assertIn(("coll", "painting/x"), self.recorded)

    def test_missing_downloadable_kept(self):
        self.build([self.col(wallpapers=[wp("a.jpg", download="http://u", sha256="x")])], {"painting/x": set()})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.removed, [])
        self.assertEqual(len(self.cat.load("painting/x").wallpapers), 1)
        self.assertEqual(self.cat.saved, [])

    def test_missing_in_external_removed(self):
        self.build([self.col(wallpapers=[wp("a.jpg", download="http://u", sha256="x")])], {"painting/x": set()},
                   external={"painting/x"})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.removed, [Ref("painting/x", "a.jpg")])
        self.assertEqual(self.cat.load("painting/x").wallpapers, [])  # external toml is kept, empty

    def test_new_image_renamed_and_listed_as_personal(self):
        self.build([self.col()], {"painting/x": {"Van Gogh_Starry Night_1889.JPG"}},
                   sizes={("painting/x", "Van Gogh_Starry Night_1889.JPG"): R})
        rep = self.syncer.sync(stage=False)
        new = "van-gogh_starry-night_1889.jpg"
        self.assertEqual(rep.renamed, [(Ref("painting/x", "Van Gogh_Starry Night_1889.JPG"), Ref("painting/x", new))])
        self.assertEqual(rep.added, [Ref("painting/x", new)])
        self.assertEqual(self.store.files["painting/x"], {new})
        w = self.cat.load("painting/x").wallpapers[0]
        self.assertEqual((w.file, w.title, w.artist, w.year, w.license, w.resolution),
                         (new, "Starry Night", "Van Gogh", YearSpan(1889, 1889), "personal", R))
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_mv"], [])
        self.assertIn(("file", self.store.path("painting/x", "Van Gogh_Starry Night_1889.JPG")), self.recorded)

    def test_single_part_name_gets_unknown_artist_and_unique_name(self):
        self.build([self.col(wallpapers=[wp("sunset.jpg")])], {"painting/x": {"sunset.jpg", "Sunset.jpg"}},
                   sizes={("painting/x", "Sunset.jpg"): R})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.added, [Ref("painting/x", "sunset-2.jpg")])
        self.assertEqual(self.cat.load("painting/x").wallpapers[1].artist, "Unknown")

    def test_tracked_rename_uses_vcs(self):
        self.build([self.col()], {"painting/x": {"Foo_Bar.png"}}, sizes={("painting/x", "Foo_Bar.png"): R},
                   tracked=[("painting/x", "Foo_Bar.png")])
        self.syncer.sync(stage=False)
        mv = [c for c in self.vcs.calls if c[0] == "stage_mv"]
        self.assertEqual(len(mv), 1)
        self.assertEqual([c for c in self.store.calls if c[0] == "move"], [])

    def test_external_image_not_renamed(self):
        self.build([self.col()], {"painting/x": {"Foo_Bar.png"}}, external={"painting/x"},
                   sizes={("painting/x", "Foo_Bar.png"): R})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.renamed, [])
        self.assertEqual(rep.added, [Ref("painting/x", "Foo_Bar.png")])

    def test_unidentifiable_image_skipped(self):
        self.build([self.col()], {"painting/x": {"bad.jpg"}})
        rep = self.syncer.sync(stage=False)
        self.assertEqual(rep.skipped, [Ref("painting/x", "bad.jpg")])
        self.assertEqual(rep.added, [])
        self.assertEqual(self.cat.saved, [])

    def test_new_folder_gets_toml(self):
        self.build([], {"painting/new": {"a-b.jpg"}}, sizes={("painting/new", "a-b.jpg"): R})
        rep = self.syncer.sync(stage=False)
        self.assertIn("painting/new", self.cat.data)
        self.assertEqual(rep.added, [Ref("painting/new", "a-b.jpg")])
        self.assertEqual(self.cat.load("painting/new").wallpapers[0].artist, "Unknown")
        self.assertIn(("coll", "painting/new"), self.recorded)

    def test_stage_flag_stages_metadata_only(self):
        self.build([self.col(wallpapers=[wp("a.jpg"), wp("gone.jpg")])], {"painting/x": {"a.jpg", "n-m.jpg"}},
                   sizes={("painting/x", "n-m.jpg"): R})
        self.syncer.sync(stage=False)
        self.assertEqual(self.vcs.calls, [])
        self.build([self.col(wallpapers=[wp("a.jpg"), wp("gone.jpg")])], {"painting/x": {"a.jpg", "n-m.jpg"}},
                   sizes={("painting/x", "n-m.jpg"): R})
        toml = self.store.path("painting/x", "wallpapers.toml")
        self.vcs.index[toml] = self.cat.render(self.col(wallpapers=[wp("a.jpg"), wp("gone.jpg")]))
        self.syncer.sync(stage=True)
        self.assertEqual(self.vcs.calls, [("stage_blob", toml)])  # gone.jpg leaves the index toml, n-m.jpg stays out
        self.assertEqual(sorted(self.cat.blob_entries(self.vcs.index[toml])), ["a.jpg"])

    def test_emptied_collection_removed_with_dir_and_toml_unstaged(self):
        self.build([self.col(wallpapers=[wp("gone.jpg")])], {"painting/x": set()},
                   tracked=[("painting/x", "wallpapers.toml")])
        toml = self.store.path("painting/x", "wallpapers.toml")
        self.vcs.index[toml] = self.cat.render(self.col(wallpapers=[wp("gone.jpg")]))
        self.syncer.sync(stage=True)
        self.assertNotIn("painting/x", self.cat.data)
        self.assertIn(("remove_dir", "painting/x"), self.store.calls)
        self.assertEqual(self.vcs.calls, [("unstage_path", toml)])
        self.assertIn(("coll", "painting/x"), self.recorded)

    def test_emptied_collection_untracked_toml_no_vcs_call(self):
        self.build([self.col(wallpapers=[wp("gone.jpg")])], {"painting/x": set()})
        self.syncer.sync(stage=True)
        self.assertNotIn("painting/x", self.cat.data)
        self.assertEqual(self.vcs.calls, [])

    def test_nothing_to_do_changes_nothing(self):
        self.build([self.col(wallpapers=[wp("a.jpg")])], {"painting/x": {"a.jpg"}})
        rep = self.syncer.sync(stage=True)
        self.assertEqual((rep.removed, rep.added, rep.renamed, rep.skipped), ([], [], [], []))
        self.assertEqual(self.vcs.calls, [])


if __name__ == "__main__":
    unittest.main()
