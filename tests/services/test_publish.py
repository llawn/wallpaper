"""Publish model (owner brief 2026-10-28): a wallpaper is its image plus its own entry in wallpapers.toml; the TOML in
the git index is generated from the working collection filtered to the published entries."""
import tomllib

from tests.services.support import ServiceCase, col, wp
from wallman.domain.models import Ref
from wallman.errors import NotShareable, WallmanError
from wallman.services.cropper import Cropper
from wallman.services.git_actions import GitActions
from wallman.services.importer import Importer, NewWallpaper
from wallman.services.library import Library
from wallman.services.remover import Remover
from wallman.services.syncer import Syncer
from wallman.domain.models import CropBox, Resolution

F = "painting/x"
DL = dict(license="all-rights-reserved", download="https://example.org/d.jpg", sha256="0" * 64)


class PublishCase(ServiceCase):
    def setup_folder(self, entries=None, external=(), folder=F):
        entries = entries or [wp("a.jpg", title="Alpha"), wp("b.jpg", title="Beta"), wp("c.jpg", title="Gamma", **DL)]
        self.build([col(folder, entries)], {folder: {w.file for w in entries}}, external=external,
                   default=Resolution(3840, 2160))

    def actions(self):
        return GitActions(self.cat, self.store, self.vcs, self.uow)

    def lib(self):
        return Library(self.cat, self.store, self.vcs, self.uow, self.fetcher)

    def img(self, file, folder=F):
        return self.store.path(folder, file)

    def blob(self, folder=F):
        data = self.vcs.index.get(self.toml(folder))
        return None if data is None else data.decode()

    def published(self, folder=F):
        blob = self.blob(folder)
        return [] if blob is None else [w["file"] for w in tomllib.loads(blob).get("wallpaper", [])]

    def staged_images(self):
        return [p for c in self.vcs.calls if c[0] == "stage_add" for p in c[1] if p.suffix == ".jpg"]

    def publish(self, *files, folder=F):
        self.actions().add([Ref(folder, f) for f in files])
        self.vcs.calls.clear()


class AddRemoveTest(PublishCase):
    def test_publish_shareable_stages_the_image_and_only_its_entry(self):
        self.setup_folder()
        self.actions().add([Ref(F, "a.jpg")])
        self.assertEqual(self.published(), ["a.jpg"])
        self.assertNotIn("Beta", self.blob())
        self.assertNotIn("Gamma", self.blob())
        self.assertEqual(self.staged_images(), [self.img("a.jpg")])
        self.assertEqual(tomllib.loads(self.blob())["collection"]["count"], 1)

    def test_publish_download_only_stages_the_entry_only(self):
        self.setup_folder()
        self.actions().add([Ref(F, "c.jpg")])
        self.assertEqual(self.published(), ["c.jpg"])
        self.assertIn('download = "https://example.org/d.jpg"', self.blob())
        self.assertEqual(self.staged_images(), [])

    def test_publishing_more_keeps_the_others_and_the_working_order(self):
        self.setup_folder()
        self.publish("c.jpg")
        self.actions().add([Ref(F, "a.jpg")])
        self.assertEqual(self.published(), ["a.jpg", "c.jpg"])
        self.assertEqual(tomllib.loads(self.blob())["collection"]["count"], 2)

    def test_personal_is_refused(self):
        self.setup_folder([wp("a.jpg", license="personal"), wp("b.jpg")])
        with self.assertRaises(NotShareable) as cm:
            self.actions().add([Ref(F, "b.jpg"), Ref(F, "a.jpg")])
        self.assertIn("personal", str(cm.exception))
        self.assertEqual((self.vcs.index, self.vcs.calls), ({}, []))  # all or nothing

    def test_external_collections_are_refused(self):
        self.setup_folder(external={F})
        with self.assertRaises(WallmanError) as cm:
            self.actions().add([Ref(F, "a.jpg")])
        self.assertIn("external", str(cm.exception))
        with self.assertRaises(WallmanError):
            self.actions().remove([Ref(F, "a.jpg")])
        self.assertEqual(self.vcs.calls, [])

    def test_unpublish_drops_the_entry_and_untracks_the_image_files_untouched(self):
        self.setup_folder()
        self.publish("a.jpg", "b.jpg")
        before = self.snapshot()
        done = self.actions().remove([Ref(F, "a.jpg"), Ref(F, "c.jpg")])
        self.assertEqual(done, [Ref(F, "a.jpg")])  # c.jpg was not published
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertIn(("untrack", [self.img("a.jpg")]), self.vcs.calls)
        self.assertEqual(self.snapshot(), before)

    def test_unpublishing_the_last_entry_removes_the_toml_from_the_index(self):
        self.setup_folder()
        self.publish("c.jpg")
        self.actions().remove([Ref(F, "c.jpg")])
        self.assertIsNone(self.blob())
        self.assertNotIn(self.toml(F), self.vcs.index)

    def test_folder_with_nothing_published_has_no_toml_in_the_index(self):
        self.setup_folder()
        self.actions().remove([Ref(F, "a.jpg")])
        self.assertEqual(self.vcs.index, {})

    def test_delete_plan_counts_published_entries_as_tracked(self):
        self.setup_folder()
        self.publish("c.jpg")
        plan = self.actions().delete_plan([Ref(F, "a.jpg"), Ref(F, "c.jpg")])
        self.assertEqual((plan.tracked, plan.untracked), ([Ref(F, "c.jpg")], [Ref(F, "a.jpg")]))


class EditTest(PublishCase):
    def test_publish_then_patch_title_regenerates_the_index_under_the_new_name(self):
        self.setup_folder([wp("x_alpha.jpg", title="Alpha", artist="X"), wp("b.jpg")])
        self.publish("x_alpha.jpg")
        self.vcs.tracked.add(self.img("x_alpha.jpg"))
        new = self.lib().update(Ref(F, "x_alpha.jpg"), {"title": "Omega"})
        self.assertEqual(new.file, "x_omega.jpg")
        self.assertEqual(self.published(), ["x_omega.jpg"])
        self.assertIn('title = "Omega"', self.blob())
        self.assertIn(("stage_mv", self.img("x_alpha.jpg"), self.img("x_omega.jpg")), self.vcs.calls)

    def test_patch_of_an_unpublished_entry_leaves_the_index_untouched(self):
        self.setup_folder()
        self.publish("a.jpg")
        before = dict(self.vcs.index)
        self.lib().update(Ref(F, "b.jpg"), {"tags": ["sea"]})
        self.assertEqual(self.vcs.index, before)
        self.assertEqual([c for c in self.vcs.calls if c[0] in ("stage_blob", "unstage_path", "stage_add")], [])

    def test_patch_of_a_published_entry_stages_the_new_values(self):
        self.setup_folder()
        self.publish("c.jpg")
        self.lib().update(Ref(F, "c.jpg"), {"tags": ["sea"]})
        self.assertIn('tags = ["sea"]', self.blob())

    def test_move_published_stays_published_in_the_target(self):
        self.setup_folder()
        self.build([col(F, [wp("a.jpg"), wp("b.jpg")]), col("painting/y", [wp("y.jpg")])],
                   {F: {"a.jpg", "b.jpg"}, "painting/y": {"y.jpg"}})
        self.publish("a.jpg", "b.jpg")
        self.vcs.tracked.update({self.img("a.jpg"), self.img("b.jpg")})
        self.lib().update(Ref(F, "a.jpg"), {"folder": "painting/y"})
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertEqual(self.published("painting/y"), ["a.jpg"])  # y.jpg stays unpublished

    def test_move_unpublished_stays_unpublished(self):
        self.build([col(F, [wp("a.jpg"), wp("b.jpg")]), col("painting/y", [wp("y.jpg")])],
                   {F: {"a.jpg", "b.jpg"}, "painting/y": {"y.jpg"}})
        self.publish("b.jpg")
        self.lib().update(Ref(F, "a.jpg"), {"folder": "painting/y"})
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertIsNone(self.blob("painting/y"))

    def test_relicense_published_to_download_only_keeps_the_entry_and_untracks_the_image(self):
        self.setup_folder()
        self.publish("a.jpg")
        self.vcs.tracked.add(self.img("a.jpg"))
        self.fetcher.responses["https://example.org/a.jpg"] = b"A"
        self.lib().update(Ref(F, "a.jpg"), {"license": "all-rights-reserved", "download": "https://example.org/a.jpg"})
        self.assertEqual(self.published(), ["a.jpg"])
        self.assertIn('license = "all-rights-reserved"', self.blob())
        self.assertIn(("untrack", [self.img("a.jpg")]), self.vcs.calls)

    def test_relicense_published_download_only_to_shareable_stages_the_image(self):
        self.setup_folder()
        self.publish("c.jpg")
        self.lib().update(Ref(F, "c.jpg"), {"license": "CC0-1.0"})
        self.assertEqual(self.published(), ["c.jpg"])
        self.assertEqual(self.staged_images(), [self.img("c.jpg")])

    def test_relicense_unpublished_to_shareable_does_not_publish(self):
        self.setup_folder()
        self.lib().update(Ref(F, "c.jpg"), {"license": "CC0-1.0"})
        self.assertEqual((self.staged_images(), self.vcs.index), ([], {}))

    def test_relicense_published_to_personal_unpublishes(self):
        self.setup_folder()
        self.publish("a.jpg", "b.jpg")
        self.vcs.tracked.add(self.img("a.jpg"))
        self.lib().update(Ref(F, "a.jpg"), {"license": "personal"})
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertIn(("untrack", [self.img("a.jpg")]), self.vcs.calls)

    def test_crop_of_a_published_shareable_restages_image_and_entry(self):
        self.setup_folder()
        self.publish("a.jpg")
        self.tool.default = Resolution(4000, 3000)
        Cropper(self.cat, self.store, self.tool, self.vcs, self.uow, self.fetcher).crop(
            Ref(F, "a.jpg"), CropBox(0, 0, 3200, 1800))
        self.assertIn("resolution = [3200, 1800]", self.blob())
        self.assertEqual(self.staged_images(), [self.img("a.jpg")])

    def test_crop_of_an_unpublished_entry_stages_nothing(self):
        self.setup_folder()
        self.tool.default = Resolution(4000, 3000)
        Cropper(self.cat, self.store, self.tool, self.vcs, self.uow, self.fetcher).crop(
            Ref(F, "a.jpg"), CropBox(0, 0, 3200, 1800))
        self.assertEqual((self.staged_images(), self.vcs.index), ([], {}))


class ImportDeleteSyncTest(PublishCase):
    def test_import_does_not_publish(self):
        self.setup_folder()
        self.publish("b.jpg")
        imp = Importer(self.cat, self.store, self.tool, self.vcs, self.uow, self.uploads)
        ref = imp.add(self.uploads.from_bytes("m.jpg", b"IMG"), NewWallpaper(
            folder=F, title="New", artist="Me", year=None, license="CC0-1.0", license_detail=None, source=None,
            crop=None))
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertNotIn(self.img(ref.file), self.staged_images())

    def test_delete_published_stages_removal_of_entry_and_image(self):
        self.setup_folder()
        self.publish("a.jpg", "c.jpg")
        self.vcs.tracked.add(self.img("a.jpg"))
        Remover(self.cat, self.store, self.vcs, self.uow).delete([Ref(F, "a.jpg")])
        self.assertEqual(self.published(), ["c.jpg"])
        self.assertIn(("stage_rm", [self.img("a.jpg")]), self.vcs.calls)

    def test_delete_unpublished_changes_nothing_in_git(self):
        self.setup_folder()
        self.publish("a.jpg")
        before = dict(self.vcs.index)
        Remover(self.cat, self.store, self.vcs, self.uow).delete([Ref(F, "b.jpg")])
        self.assertEqual(self.vcs.index, before)
        self.assertEqual([c for c in self.vcs.calls if c[0] != "status"], [])
        self.assertFalse(self.img("b.jpg").exists())

    def test_sync_stage_drops_a_deleted_published_image_and_never_publishes_new_ones(self):
        self.setup_folder()
        self.publish("a.jpg", "b.jpg")
        self.vcs.tracked.update({self.img("a.jpg"), self.img("b.jpg")})
        self.store.delete(self.img("a.jpg"))
        self.disk(F, "new_one.jpg", b"N")
        self.store.files[F].add("new_one.jpg")
        self.vcs.calls.clear()
        Syncer(self.cat, self.store, self.tool, self.vcs, self.uow).sync(stage=True)
        self.assertEqual(self.published(), ["b.jpg"])
        self.assertIn(("stage_rm", [self.img("a.jpg")]), self.vcs.calls)
        self.assertNotIn("new_one.jpg", self.blob())
        self.assertNotIn(self.img("new_one.jpg"), self.staged_images())
        self.assertIn("new_one.jpg", [w.file for w in self.cat.load(F).wallpapers])  # working entry added
