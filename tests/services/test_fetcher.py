import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeFetcher, FakeImageTool, FakeStore
from wallman.domain.models import Collection, CropBox, Ref, Resolution, Wallpaper
from wallman.errors import DownloadFailed
from wallman.services.fetcher import FetchService

R = Resolution(2560, 1440)
DATA = b"image bytes"
SHA = hashlib.sha256(DATA).hexdigest()


def wp(file, **kw):
    kw.setdefault("download", "http://u/" + file)
    kw.setdefault("sha256", SHA)
    return Wallpaper(file=file, title="T", artist="A", license="public-domain", resolution=R, **kw)


class FetchServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def build(self, wallpapers, files=None, responses=None, external=False, folder="animation/x"):
        c = Collection(folder=folder, medium="animation", movement="x", wallpapers=wallpapers)
        (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.store = FakeStore({folder: set(files or ())}, {folder} if external else set(), root=self.root)
        self.tool = FakeImageTool()
        self.fetcher = FakeFetcher(responses if responses is not None else {w.download: DATA for w in wallpapers})
        self.svc = FetchService(FakeCatalogue([c]), self.store, self.tool, self.fetcher, sleep=lambda s: None)
        self.folder = folder

    def test_plain_download_moves_temp_to_target(self):
        self.build([wp("a.jpg")])
        rep = self.svc.fetch()
        self.assertEqual(rep.downloaded, 1)
        self.assertEqual(self.store.files[self.folder], {"a.jpg"})
        self.assertEqual(self.tool.calls, [])

    def test_one_second_between_downloads(self):
        self.build([wp("a.jpg"), wp("b.jpg"), wp("c.jpg")])
        sleeps = []
        self.svc.sleep = sleeps.append
        self.assertEqual(self.svc.fetch().downloaded, 3)
        self.assertEqual(sleeps, [1.0, 1.0])  # between downloads, not before the first

    def test_checksum_mismatch_not_saved(self):
        self.build([wp("a.jpg", sha256="0" * 64)])
        rep = self.svc.fetch()
        self.assertEqual(rep.downloaded, 0)
        self.assertEqual(rep.failed, [(Ref(self.folder, "a.jpg"), "checksum mismatch")])
        self.assertEqual(self.store.files[self.folder], set())
        self.assertEqual(self.store.calls, [])
        self.assertEqual(list((self.root / self.folder).iterdir()), [])

    def test_convert_then_crop_applied(self):
        box = CropBox(1, 2, 3, 4)
        self.build([wp("a.jpg", processing=("convert", "crop"), crop=box)])
        self.tool.sizes[self.store.path(self.folder, ".fetch-a.jpg")] = R
        rep = self.svc.fetch()
        self.assertEqual(rep.downloaded, 1)
        dst = self.store.path(self.folder, "a.jpg")
        kinds = [c[0] for c in self.tool.calls]
        self.assertEqual(kinds, ["convert", "crop"])
        self.assertEqual(self.tool.calls[0][2], dst)
        self.assertEqual(self.tool.calls[1][1:], (dst, box, dst))
        self.assertEqual(self.store.files[self.folder], set())  # fake tools write nothing; temp is gone
        self.assertTrue(all(c[0] != "move" for c in self.store.calls))
        self.assertIn(("delete", self.store.path(self.folder, ".fetch-a.jpg")), self.store.calls)  # temp removed

    def test_render_uses_render_svg(self):
        self.build([wp("a.png", processing=("render",))])
        self.svc.fetch()
        self.assertEqual([c[0] for c in self.tool.calls], ["render_svg"])

    def test_skips_external_and_present_files(self):
        self.build([wp("a.jpg"), wp("b.jpg"), wp("nodl.jpg", download=None, sha256=None)], files={"a.jpg"})
        self.assertEqual(self.svc.fetch().downloaded, 1)
        self.assertEqual(self.fetcher.calls, ["http://u/b.jpg"])
        self.build([wp("a.jpg")], external=True)
        self.assertEqual(self.svc.fetch().downloaded, 0)
        self.assertEqual(self.fetcher.calls, [])

    def test_folder_filter_by_prefix(self):
        self.build([wp("a.jpg")])
        self.assertEqual(self.svc.fetch(["animation/y"]).downloaded, 0)
        self.assertEqual(self.svc.fetch(["animation"]).downloaded, 1)

    def test_error_recorded_and_others_continue(self):
        self.build([wp("a.jpg"), wp("b.jpg")], responses={"http://u/a.jpg": DownloadFailed("boom"), "http://u/b.jpg": DATA})
        rep = self.svc.fetch()
        self.assertEqual(rep.downloaded, 1)
        self.assertEqual(rep.failed, [(Ref(self.folder, "a.jpg"), "boom")])

    def test_conversion_error_removes_partial_files(self):
        self.build([wp("a.jpg", processing=("convert",))])

        def boom(src, dst):
            self.store.files[self.folder].add("a.jpg")
            raise DownloadFailed("convert failed")
        self.tool.convert = boom
        rep = self.svc.fetch()
        self.assertEqual(rep.downloaded, 0)
        self.assertEqual(rep.failed[0][1], "convert failed")
        self.assertEqual(self.store.files[self.folder], set())


if __name__ == "__main__":
    unittest.main()
