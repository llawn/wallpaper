import hashlib
import unittest

from tests.services.support import R, ServiceCase, col, wp
from wallman.domain.models import CropBox, Ref, Resolution
from wallman.errors import ChecksumMismatch, DownloadFailed, InvalidImage, NotFound, TooSmall
from wallman.services.cropper import Cropper

URL = "https://d/orig.jpg"
ORIG = b"ORIGINAL-BYTES"
SHA = hashlib.sha256(ORIG).hexdigest()
BOX = CropBox(100, 50, 3200, 1800)


class CropperTest(ServiceCase):
    def cropper(self):
        return Cropper(self.cat, self.store, self.tool, self.vcs, self.uow, self.fetcher)

    def setup_entry(self, license="public-domain", processing=(), download=URL, sha=SHA, external=(), folder="painting/x",
                    tracked=True):
        e = wp("a.jpg", license=license, download=download, sha256=sha if download else None, processing=processing,
               resolution=Resolution(3000, 1700))
        self.build([col(folder, [e], "painting")], {folder: {"a.jpg"}}, external=external,
                   tracked=[(folder, "a.jpg")] if tracked else [], responses={URL: ORIG}, default=Resolution(4000, 3000))
        self.disk(folder, "a.jpg", b"CURRENT")
        return Ref(folder, "a.jpg")

    def test_recrop_from_original_when_downloadable(self):
        ref = self.setup_entry()
        self.publish_all(ref.folder)
        w = self.cropper().crop(ref, BOX)
        img = self.store.path(ref.folder, ref.file)
        self.assertEqual(self.fetcher.calls, [URL])
        crop_call = [c for c in self.tool.calls if c[0] == "crop"][-1]
        self.assertNotEqual(crop_call[1], img)  # source is the downloaded original, not the current file
        self.assertEqual((crop_call[2], crop_call[3]), (BOX, img))
        self.assertEqual((w.resolution, w.processing, w.crop), (Resolution(3200, 1800), ("crop",), BOX))
        self.assertEqual(self.cat.load(ref.folder).wallpapers[0], w)
        # everything published: the index takes the working toml itself, in the one batched `git add`
        self.assertEqual(self.vcs.calls, [("stage_add", [img, self.toml(ref.folder)])])
        self.assertEqual(self.commits, 1)
        self.assertEqual(sorted(p.name for p in img.parent.iterdir()), ["a.jpg", "wallpapers.toml"])  # temps cleaned

    def test_recrop_applies_convert_first(self):
        ref = self.setup_entry(processing=("convert", "crop"))
        self.cropper().crop(ref, BOX)
        kinds = [c[0] for c in self.tool.calls]
        self.assertEqual(kinds, ["convert", "crop"])
        self.assertEqual(self.tool.calls[1][1], self.tool.calls[0][2])  # crop reads the converted file

    # I4: rendered (SVG) entries are cropped from the current rendered file, never from the browser-sized SVG;
    # the stored box stays relative to the full render so `just fetch` replays render + crop exactly.
    def test_render_entry_crops_current_file_without_download(self):
        ref = self.setup_entry(processing=("render",))
        img = self.store.path(ref.folder, ref.file)
        self.tool.sizes[img] = Resolution(3840, 2160)
        w = self.cropper().crop(ref, BOX)
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual([c[0] for c in self.tool.calls], ["crop"])
        self.assertEqual(self.tool.calls[0][1], img)
        self.assertEqual((w.processing, w.crop, w.resolution), (("render", "crop"), BOX, Resolution(3200, 1800)))

    def test_render_entry_recrop_composes_with_previous_box(self):
        ref = self.setup_entry(processing=("render", "crop"))
        coll = self.cat.load(ref.folder)
        coll.wallpapers[0] = coll.wallpapers[0].__class__(**{**coll.wallpapers[0].__dict__, "crop": BOX,
                                                               "resolution": Resolution(3200, 1800)})
        self.cat.save(coll)
        img = self.store.path(ref.folder, ref.file)
        self.tool.sizes[img] = Resolution(3200, 1800)
        w = self.cropper().crop(ref, CropBox(10, 20, 3000, 1688))
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual(w.crop, CropBox(110, 70, 3000, 1688))
        self.assertEqual(w.processing, ("render", "crop"))

    def test_render_entry_box_checked_against_current_file(self):
        ref = self.setup_entry(processing=("render",))
        self.tool.sizes[self.store.path(ref.folder, ref.file)] = Resolution(3840, 2160)
        with self.assertRaises(InvalidImage):
            self.cropper().crop(ref, CropBox(1000, 0, 3200, 1800))

    def test_render_entry_not_downloaded_yet(self):
        ref = self.setup_entry(processing=("render",))
        self.store.path(ref.folder, ref.file).unlink()
        self.store.files[ref.folder].discard(ref.file)
        with self.assertRaises(NotFound) as cm:
            self.cropper().crop(ref, BOX)
        self.assertIn("just fetch", str(cm.exception))

    def test_recrop_download_failure_rolls_back(self):
        ref = self.setup_entry()
        self.fetcher.responses[URL] = DownloadFailed("offline")
        before = self.snapshot()
        with self.assertRaises(DownloadFailed):
            self.cropper().crop(ref, BOX)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.vcs.calls, self.cat.saved, self.commits, self.tool.calls), ([], [], 0, []))

    def test_checksum_mismatch_changes_nothing(self):
        ref = self.setup_entry(sha="0" * 64)
        before = self.snapshot()
        with self.assertRaises(ChecksumMismatch):
            self.cropper().crop(ref, BOX)
        self.assertEqual((self.snapshot(), self.vcs.calls, self.cat.saved), (before, [], []))

    def test_too_small_changes_nothing_and_skips_download(self):
        ref = self.setup_entry()
        before = self.snapshot()
        with self.assertRaises(TooSmall):
            self.cropper().crop(ref, CropBox(0, 0, 1600, 900))
        self.assertEqual((self.snapshot(), self.vcs.calls, self.cat.saved, self.fetcher.calls), (before, [], [], []))

    def test_box_outside_original_is_invalid(self):
        ref = self.setup_entry()
        with self.assertRaises(InvalidImage):
            self.cropper().crop(ref, CropBox(2000, 0, 3200, 1800))
        self.assertEqual(self.vcs.calls, [])

    def test_without_download_crops_current_file_in_place(self):
        ref = self.setup_entry(download=None)
        self.tool.sizes[self.store.path(ref.folder, ref.file)] = Resolution(4000, 3000)
        w = self.cropper().crop(ref, BOX)
        img = self.store.path(ref.folder, ref.file)
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual([c for c in self.tool.calls if c[0] == "crop"], [("crop", img, BOX, img)])
        self.assertEqual((w.crop, w.processing, w.resolution), (None, ("crop",), Resolution(3200, 1800)))

    def test_failure_during_crop_restores_current_file(self):
        ref = self.setup_entry(download=None)
        img = self.store.path(ref.folder, ref.file)
        self.tool.sizes[img] = Resolution(4000, 3000)

        def boom(src, box, dst):
            dst.write_bytes(b"half-written")
            raise InvalidImage("magick died")
        self.tool.crop = boom
        with self.assertRaises(InvalidImage):
            self.cropper().crop(ref, BOX)
        self.assertEqual(img.read_bytes(), b"CURRENT")
        self.assertEqual((self.vcs.calls, self.cat.saved), ([], []))

    def test_non_shareable_stages_toml_only(self):
        ref = self.setup_entry(license="all-rights-reserved")
        self.publish_all(ref.folder)
        self.cropper().crop(ref, BOX)
        self.assertEqual(self.vcs.calls, [("stage_add", [self.toml(ref.folder)])])

    def test_unpublished_stages_nothing(self):
        ref = self.setup_entry()
        self.cropper().crop(ref, BOX)
        self.assertEqual(self.vcs.calls, [])

    def test_processing_not_duplicated(self):
        ref = self.setup_entry(processing=("crop",))
        self.assertEqual(self.cropper().crop(ref, BOX).processing, ("crop",))

    def test_external_collection_skips_size_rule(self):
        ref = self.setup_entry(external={"painting/x"}, download=None)
        self.tool.sizes[self.store.path(ref.folder, ref.file)] = Resolution(4000, 3000)
        self.assertEqual(self.cropper().crop(ref, CropBox(0, 0, 1600, 900)).resolution, Resolution(1600, 900))

    def test_unknown_ref(self):
        self.setup_entry()
        with self.assertRaises(NotFound):
            self.cropper().crop(Ref("painting/x", "zz.jpg"), BOX)


if __name__ == "__main__":
    unittest.main()
