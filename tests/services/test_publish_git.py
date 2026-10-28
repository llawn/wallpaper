"""Publish model through the real wiring (bootstrap, GitCli, TomlCatalogue) in a temp git repository."""
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tests.cli.test_cli import git
from tests.fakes import FakeFetcher
from tests.services import fixture
from wallman import bootstrap
from wallman.domain.models import Ref

F = "painting/impressionism"
MONET = "claude-monet_water-lilies_1906.jpg"
DEGAS = "edgar-degas_dancers-pink_1885.jpg"


@unittest.skipUnless(shutil.which("git"), "git not installed")
class PublishGitTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "repo"
        fixture.build(self.root)
        (self.root / "images/ml4w/.git").unlink()  # a plain folder here
        for name in (MONET, DEGAS):
            (self.root / "images" / F / name).write_bytes(b"img:" + name.encode())
        git(self.root, "init", "-q")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")  # nothing published yet
        self.app = bootstrap.build(self.root, Path(tmp.name) / "cache", FakeFetcher())
        self.index_file = self.root / ".git" / "index"

    def index_toml(self, folder=F):
        r = subprocess.run(["git", "-C", str(self.root), "show", f":images/{folder}/wallpapers.toml"],
                           capture_output=True, text=True)
        return tomllib.loads(r.stdout) if r.returncode == 0 else None

    def staged(self):
        return git(self.root, "diff", "--cached", "--name-only").split()

    def test_publish_writes_a_filtered_index_toml_and_leaves_the_working_file(self):
        working = (self.root / "images" / F / "wallpapers.toml").read_bytes()
        self.app.git_actions.add([Ref(F, MONET)])
        toml = self.index_toml()
        self.assertEqual([w["file"] for w in toml["wallpaper"]], [MONET])
        self.assertEqual(toml["collection"]["count"], 1)
        self.assertIn(f"images/{F}/{MONET}", self.staged())
        self.assertEqual((self.root / "images" / F / "wallpapers.toml").read_bytes(), working)
        self.assertIsNone(self.index_toml("poster"))  # nothing published there: no toml in the index

    def test_rollback_after_a_publish_restores_the_index_byte_for_byte(self):
        self.app.git_actions.add([Ref(F, DEGAS)])
        before = self.index_file.read_bytes()
        self.app.index.warm()
        with mock.patch.object(self.app.index, "refresh", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.app.git_actions.add([Ref(F, MONET)])
        self.assertEqual(self.index_file.read_bytes(), before)

    def test_rollback_after_a_patch_of_a_published_entry_restores_the_index_byte_for_byte(self):
        self.app.git_actions.add([Ref(F, MONET)])
        before = self.index_file.read_bytes()
        working = (self.root / "images" / F / "wallpapers.toml").read_bytes()
        self.app.index.warm()
        with mock.patch.object(self.app.index, "refresh", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.app.library.update(Ref(F, MONET), {"title": "Nympheas"})
        self.assertEqual(self.index_file.read_bytes(), before)
        self.assertEqual((self.root / "images" / F / "wallpapers.toml").read_bytes(), working)
        self.assertTrue((self.root / "images" / F / MONET).exists())

    def test_patch_of_a_published_entry_renames_it_in_the_index_toml(self):
        self.app.git_actions.add([Ref(F, MONET)])
        new = self.app.library.update(Ref(F, MONET), {"title": "Nympheas"})
        self.assertEqual([w["title"] for w in self.index_toml()["wallpaper"]], ["Nympheas"])
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [new.file])
        self.assertIn(f"images/{F}/{new.file}", self.staged())
        self.assertNotIn(f"images/{F}/{MONET}", self.staged())

    def test_sync_stage_never_publishes_new_images(self):
        """What the pre-commit hook runs: `sync --stage`."""
        self.app.git_actions.add([Ref(F, MONET)])
        (self.root / "images" / F / "new_image.jpg").write_bytes(b"new")
        with mock.patch.object(self.app.syncer.tool, "identify", return_value=__import__(
                "wallman.domain.models", fromlist=["Resolution"]).Resolution(3840, 2160)):
            report = self.app.syncer.sync(stage=True)
        self.assertIn(Ref(F, "new_image.jpg"), report.added)
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [MONET])
        self.assertNotIn(f"images/{F}/new_image.jpg", self.staged())

    def test_sync_stage_drops_a_deleted_published_image(self):
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        git(self.root, "commit", "-q", "-m", "publish")
        (self.root / "images" / F / MONET).unlink()
        self.app.syncer.sync(stage=True)
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [DEGAS])
        self.assertIn(f"D\timages/{F}/{MONET}", git(self.root, "diff", "--cached", "--name-status").splitlines())

    # -- review C1: a stale published name never publishes a new entry ------------------------------------
    def test_hand_delete_sync_without_stage_then_same_name_readded_and_hook_run(self):
        from wallman.domain.models import Resolution
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        image = self.root / "images" / F / MONET
        image.unlink()
        self.app.syncer.sync(stage=False)
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [DEGAS])  # the stale name is gone
        image.write_bytes(b"a different picture")
        with mock.patch.object(self.app.syncer.tool, "identify", return_value=Resolution(3840, 2160)):
            self.app.syncer.sync(stage=True)  # what the pre-commit hook runs
        working = {w.file: w for w in self.app.catalogue.load(F).wallpapers}
        self.assertEqual(working[MONET].license, "personal")
        toml = self.index_toml()
        self.assertEqual([w["file"] for w in toml["wallpaper"]], [DEGAS])
        self.assertNotIn("personal", [w["license"] for w in toml["wallpaper"]])

    def test_rename_onto_a_stale_published_name_does_not_publish(self):
        self.app.git_actions.add([Ref(F, MONET)])
        # the entry leaves the working TOML outside wallman's units (hand edit); its index name is now stale
        coll = self.app.catalogue.load(F)
        coll.wallpapers = [w for w in coll.wallpapers if w.file != MONET]
        self.app.catalogue.save(coll)
        (self.root / "images" / F / MONET).unlink()
        new = self.app.library.update(Ref(F, DEGAS), {"artist": "Claude Monet", "title": "Water Lilies", "year": 1906})
        self.assertEqual(new.file, MONET)
        self.assertIsNone(self.index_toml())  # nothing published in the folder any more
        self.assertNotIn(f"images/{F}/{MONET}", git(self.root, "ls-files", "--", f"images/{F}"))

    def test_import_onto_a_stale_published_name_does_not_publish(self):
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        coll = self.app.catalogue.load(F)
        coll.wallpapers = [w for w in coll.wallpapers if w.file != MONET]
        self.app.catalogue.save(coll)  # hand edit
        self.app.library.update(Ref(F, DEGAS), {"tags": ["pink"]})  # any operation on the folder
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [DEGAS])

    def test_personal_entries_are_never_written_to_the_index_toml(self):
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        # an index TOML committed whole long ago, with a personal entry in it
        coll = self.app.catalogue.load(F)
        import dataclasses
        coll.wallpapers[0] = dataclasses.replace(coll.wallpapers[0], license="personal")
        self.app.catalogue.save(coll)
        git(self.root, "add", f"images/{F}/wallpapers.toml")
        self.app.library.update(Ref(F, DEGAS), {"tags": ["pink"]})
        self.assertEqual([w["file"] for w in self.index_toml()["wallpaper"]], [DEGAS])

    def test_remove_from_git_untracks_every_image_in_one_git_call(self):
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        calls = []
        real = self.app.vcs.untrack
        with mock.patch.object(self.app.vcs, "untrack", side_effect=lambda p: (calls.append(list(p)), real(p))[1]):
            done = self.app.git_actions.remove([Ref(F, MONET), Ref(F, DEGAS)])
        self.assertEqual(len(done), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 2)
        self.assertIsNone(self.index_toml())

    def test_a_toml_broken_by_hand_keeps_its_index_toml_through_an_unrelated_operation(self):
        self.app.git_actions.add([Ref(F, MONET), Ref(F, DEGAS)])
        self.app.index.warm()
        toml = self.root / "images" / F / "wallpapers.toml"
        toml.write_text(toml.read_text().replace('title = "Dancers | in Pink"\n', ""))  # missing-field
        self.assertTrue(self.app.catalogue.structural_issues(F))
        blob = git(self.root, "show", f":images/{F}/wallpapers.toml")
        staged = self.staged()
        self.app.git_actions.add([Ref("poster", "studio_poster_2001.jpg")])  # another folder
        self.app.library.update(Ref("poster", "studio_poster_2001.jpg"), {"tags": ["film"]})
        self.assertEqual(git(self.root, "show", f":images/{F}/wallpapers.toml"), blob)
        self.assertEqual([p for p in self.staged() if p.startswith(f"images/{F}/")],
                         [p for p in staged if p.startswith(f"images/{F}/")])
        from wallman.errors import WallmanError
        with self.assertRaises(WallmanError) as cm:  # operations on the broken folder itself refuse up front
            self.app.git_actions.remove([Ref(F, MONET)])
        self.assertIn("fix that wallpapers.toml first", str(cm.exception))
        self.assertEqual(git(self.root, "show", f":images/{F}/wallpapers.toml"), blob)


if __name__ == "__main__":
    unittest.main()
