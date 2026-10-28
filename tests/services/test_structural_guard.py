"""C1/I1: a wallpapers.toml with an entry load() skips as invalid must never be deleted or half-staged.

Real TomlCatalogue + FsStore over a temp tree; git is the FakeVcs so the test can see every index call.
"""
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeFetcher, FakeImageTool, FakeVcs
from tests.services import fixture
from wallman.adapters.fs_store import FsStore
from wallman.adapters.toml_catalogue import TomlCatalogue
from wallman.domain.models import Ref, Resolution
from wallman.errors import WallmanError
from wallman.services.library import Library
from wallman.services.remover import Remover
from wallman.services.syncer import Syncer
from wallman.services.unit_of_work import UnitOfWork

VALID = fixture.entry("a_valid.jpg", "Valid", "A", "public-domain")
INVALID = '[[wallpaper]]\nfile = "b_broken.jpg"\ntitle = "Broken"\nlicense = "public-domain"\nresolution = [3840, 2160]\n'


class StructuralGuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        fixture.write_collection(self.root, "painting/x", "painting", "x", [VALID, INVALID])  # no artist: invalid
        fixture.write_collection(self.root, "painting/y", "painting", "y", [fixture.entry("c_ok.jpg", "Ok", "C", "public-domain")])
        for folder, name in (("painting/x", "a_valid.jpg"), ("painting/x", "b_broken.jpg"), ("painting/y", "c_ok.jpg")):
            (self.root / folder / name).write_bytes(b"img")
        self.store = FsStore(self.root)
        self.cat = TomlCatalogue(self.root, self.store)
        self.vcs = FakeVcs({self.root / "painting/x/a_valid.jpg", self.root / "painting/y/c_ok.jpg"})
        self.tool = FakeImageTool(default=Resolution(3840, 2160))

    def uow(self):
        return UnitOfWork(self.cat, self.store, self.vcs, lambda u: None)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}

    def assert_untouched(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([c for c in self.vcs.calls if c[0] != "status"], [])

    def test_catalogue_delete_refuses_when_structurally_broken(self):
        before = self.snapshot()
        with self.assertRaises(WallmanError):
            self.cat.delete("painting/x")
        self.assertEqual(self.snapshot(), before)

    def test_remove_last_valid_entry_is_refused_before_git(self):
        before = self.snapshot()
        with self.assertRaises(WallmanError) as cm:
            Remover(self.cat, self.store, self.vcs, self.uow).delete([Ref("painting/x", "a_valid.jpg")])
        self.assertIn("structural", str(cm.exception))
        self.assert_untouched(before)

    def test_move_out_of_broken_folder_is_refused_before_git(self):
        before = self.snapshot()
        lib = Library(self.cat, self.store, self.vcs, self.uow, FakeFetcher())
        with self.assertRaises(WallmanError):
            lib.update(Ref("painting/x", "a_valid.jpg"), {"folder": "painting/y"})
        self.assert_untouched(before)

    def test_move_into_broken_folder_is_refused_before_git(self):
        before = self.snapshot()
        lib = Library(self.cat, self.store, self.vcs, self.uow, FakeFetcher())
        with self.assertRaises(WallmanError):
            lib.update(Ref("painting/y", "c_ok.jpg"), {"folder": "painting/x"})
        self.assert_untouched(before)

    def test_sync_skips_broken_folder_and_keeps_its_toml(self):
        (self.root / "painting/x/a_valid.jpg").unlink()  # the only valid entry is gone from disk
        (self.root / "painting/x/new-image.jpg").write_bytes(b"img")  # tracked rename candidate
        self.vcs.tracked.add(self.root / "painting/x/new-image.jpg")
        before = self.snapshot()
        report = Syncer(self.cat, self.store, self.tool, self.vcs, self.uow).sync(stage=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_mv"], [])
        self.assertEqual([f for f, _ in report.broken], ["painting/x"])

    def test_sync_reports_unparsable_toml_instead_of_aborting(self):
        (self.root / "painting/x/wallpapers.toml").write_text("[collection\nmedium = ")
        (self.root / "painting/y/d_new.jpg").write_bytes(b"img")
        report = Syncer(self.cat, self.store, self.tool, self.vcs, self.uow).sync(stage=False)
        self.assertEqual([f for f, _ in report.broken], ["painting/x"])
        self.assertIn("invalid-toml", report.broken[0][1])
        self.assertEqual(report.added, [Ref("painting/y", "d_new.jpg")])


if __name__ == "__main__":
    unittest.main()
