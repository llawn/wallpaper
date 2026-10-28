import unittest

from tests.services.support import ServiceCase, col, wp
from wallman.domain.years import YearSpan
from wallman.domain.models import CropBox, Ref, Resolution
from wallman.errors import (DownloadFailed, InvalidImage, LicenseNeedsDownload, RatioNeedsCrop, TooSmall)
from wallman.services.importer import Importer, NewWallpaper


def meta(**kw):
    base = dict(folder="painting/impressionism", title="Water Lilies", artist="Claude Monet", year=1906,
                license="public-domain", license_detail=None, source="https://example.org/a", crop=None)
    base.update(kw)
    return NewWallpaper(**base)


class ImporterTest(ServiceCase):
    def setUp(self):
        self.build([col("painting/impressionism")], {"painting/impressionism": set()},
                   default=Resolution(3840, 2160))

    def importer(self):
        return Importer(self.cat, self.store, self.tool, self.vcs, self.uow, self.uploads)

    def up(self, name="m.jpg", data=b"IMG", res=None):
        if res:
            self.tool.default = res
        return self.uploads.from_bytes(name, data)

    def test_destination_typo_medium_refused(self):
        u = self.up()
        before = self.snapshot()
        for bad in ("paintng/x", "", "/", "painting/../poster", "painting/./x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.importer().add(u, meta(folder=bad))
        self.assertEqual((self.snapshot(), self.vcs.calls), (before, []))

    def test_destination_inside_external_collection_refused(self):
        self.store.external.add("ml4w")
        with self.assertRaises(ValueError):
            self.importer().add(self.up(), meta(folder="ml4w/sub"))
        self.assertEqual(self.vcs.calls, [])

    def test_destination_is_normalised(self):
        ref = self.importer().add(self.up(), meta(folder="/painting/new/"))
        self.assertEqual(ref.folder, "painting/new")
        self.assertIn("painting/new", self.cat.data)
        self.assertNotIn("painting/new/", self.cat.data)

    def test_add_shareable_publishes_nothing(self):
        u = self.up()
        ref = self.importer().add(u, meta())
        self.assertEqual(ref, Ref("painting/impressionism", "claude-monet_water-lilies_1906.jpg"))
        img = self.store.path(ref.folder, ref.file)
        self.assertEqual(img.read_bytes(), b"IMG")
        self.assertEqual(self.vcs.calls, [])  # not published: Add to git publishes it
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.title, w.artist, w.year, w.license, w.resolution, w.processing, w.crop, w.download),
                         ("Water Lilies", "Claude Monet", YearSpan(1906, 1906), "public-domain", Resolution(3840, 2160), (), None, None))
        self.assertEqual(w.source, "https://example.org/a")
        self.assertEqual(self.commits, 1)
        self.assertFalse(u.path.exists())  # upload discarded after success

    def test_add_non_shareable_publishes_nothing(self):
        self.tool.default = Resolution(3840, 2160)
        self.fetcher.responses["https://d/x.jpg"] = b"DL"
        u = self.uploads.from_url("https://d/x.jpg")
        ref = self.importer().add(u, meta(license="all-rights-reserved", license_detail="© 2009 Pixar"))
        self.assertEqual(self.vcs.calls, [])
        self.assertTrue(self.store.path(ref.folder, ref.file).exists())
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.download, w.license_detail), ("https://d/x.jpg", "© 2009 Pixar"))
        self.assertEqual(w.sha256, u.sha256)

    def test_add_rejects_non_image(self):
        self.tool.default = None
        with self.assertRaises(InvalidImage):
            self.up(data=b"nope")
        self.assertEqual(self.vcs.calls, [])
        self.assertEqual(self.cat.saved, [])

    def test_add_conflict_gets_suffix(self):
        first = self.importer().add(self.up(), meta())
        second = self.importer().add(self.up(data=b"IMG2"), meta())
        self.assertEqual(second.file, first.file.replace(".jpg", "-2.jpg"))
        third = self.importer().add(self.up(data=b"IMG3"), meta())
        self.assertTrue(third.file.endswith("-3.jpg"))

    def test_ratio_outside_range_requires_crop(self):
        u = self.up(res=Resolution(4000, 1000))
        before = self.snapshot()
        with self.assertRaisesRegex(RatioNeedsCrop, "4000x1000"):
            self.importer().add(u, meta())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.vcs.calls, self.cat.saved), ([], []))

    def test_crop_applied_and_recorded(self):
        u = self.up(res=Resolution(4000, 1000 * 3))
        box = CropBox(0, 100, 3200, 1800)
        ref = self.importer().add(u, meta(crop=box))
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.resolution, w.processing, w.crop), (Resolution(3200, 1800), ("crop",), None))
        self.assertEqual(self.tool.calls[-1], ("crop", u.path, box, self.store.path(ref.folder, ref.file)))

    def test_crop_result_below_minimum_raises_too_small(self):
        u = self.up(res=Resolution(4000, 3000))
        before = self.snapshot()
        with self.assertRaises(TooSmall):
            self.importer().add(u, meta(crop=CropBox(0, 0, 1600, 900)))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.vcs.calls, [])

    def test_uncropped_below_minimum_raises_too_small(self):
        u = self.up(res=Resolution(1600, 900))
        with self.assertRaises(TooSmall):
            self.importer().add(u, meta())

    def test_non_shareable_without_url_raises(self):
        u = self.up()
        before = self.snapshot()
        with self.assertRaises(LicenseNeedsDownload):
            self.importer().add(u, meta(license="official-wallpaper"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.vcs.calls, [])
        self.assertTrue(u.path.exists())

    def test_add_url_failure_leaves_repo_untouched(self):
        self.fetcher.responses["https://d/x.jpg"] = DownloadFailed("boom")
        before = self.snapshot()
        with self.assertRaises(DownloadFailed):
            self.importer().add(self.uploads.from_url("https://d/x.jpg"), meta())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.vcs.calls, self.cat.saved), ([], []))

    def test_cropped_download_only_records_crop_box(self):
        self.fetcher.responses["https://d/x.jpg"] = b"DL"
        self.tool.default = Resolution(4000, 3000)
        u = self.uploads.from_url("https://d/x.jpg")
        box = CropBox(0, 0, 4000, 2250)
        ref = self.importer().add(u, meta(license="all-rights-reserved", license_detail="© X", crop=box))
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.crop, w.processing, w.download), (box, ("crop",), "https://d/x.jpg"))

    def test_webp_upload_stored_as_converted_jpeg(self):
        self.fetcher.responses["https://d/x.webp"] = b"RIFF\x10\x00\x00\x00WEBPVP8 "
        u = self.uploads.from_url("https://d/x.webp")
        ref = self.importer().add(u, meta())
        self.assertTrue(ref.file.endswith(".jpg"))
        self.assertEqual(self.store.path(ref.folder, ref.file).read_bytes(), b"converted")
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.processing, w.sha256, w.crop), (("convert",), u.sha256, None))

    def test_svg_upload_with_crop_is_rendered_then_cropped(self):
        self.fetcher.responses["https://d/x.svg"] = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        u = self.uploads.from_url("https://d/x.svg")
        box = CropBox(0, 0, 3200, 1800)
        ref = self.importer().add(u, meta(crop=box))
        self.assertTrue(ref.file.endswith(".png"))
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.processing, w.crop, w.resolution), (("render", "crop"), box, Resolution(3200, 1800)))
        crop = [c for c in self.tool.calls if c[0] == "crop"][0]
        self.assertEqual(crop[1], u.path)  # cropped from the rendered PNG

    def test_external_converted_upload_gets_the_new_extension(self):
        self.build([col("ml4w")], {"ml4w": set()}, external={"ml4w"}, default=Resolution(1000, 1000))
        u = self.uploads.from_bytes("Some File.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ")
        ref = self.importer().add(u, meta(folder="ml4w", artist=None, license="MIT"))
        self.assertEqual(ref.file, "Some File.jpg")

    def test_crop_box_outside_image_is_invalid(self):
        u = self.up(res=Resolution(4000, 3000))
        with self.assertRaises(InvalidImage):
            self.importer().add(u, meta(crop=CropBox(1000, 0, 4000, 2250)))

    def test_new_folder_creates_collection(self):
        ref = self.importer().add(self.up(), meta(folder="painting/new-movement"))
        self.assertIn("painting/new-movement", self.cat.data)
        self.assertEqual(ref.folder, "painting/new-movement")

    def test_external_collection_skips_ratio_and_size_rules_and_keeps_name(self):
        self.build([col("ml4w")], {"ml4w": set()}, external={"ml4w"}, default=Resolution(1000, 1000))
        u = self.uploads.from_bytes("Some File.png", b"P")
        ref = self.importer().add(u, meta(folder="ml4w", artist=None, license="MIT"))
        self.assertEqual(ref.file, "Some File.png")
        self.assertEqual(self.cat.load("ml4w").wallpapers[0].resolution, Resolution(1000, 1000))

    def test_personal_medium_skips_ratio_and_size(self):
        self.build([col("personal/mine", medium="personal")], {"personal/mine": set()}, default=Resolution(1000, 1000))
        ref = self.importer().add(self.uploads.from_bytes("a.jpg", b"P"), meta(folder="personal/mine", license="personal"))
        self.assertEqual(ref.folder, "personal/mine")

    def test_failure_mid_write_rolls_back_files(self):
        self.tool.default = Resolution(4000, 3000)
        u = self.uploads.from_bytes("a.jpg", b"P")
        before = self.snapshot()
        self.cat.save = lambda c: (_ for _ in ()).throw(RuntimeError("disk full"))
        with self.assertRaises(RuntimeError):
            self.importer().add(u, meta(crop=CropBox(0, 0, 3200, 1800)))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.vcs.calls, self.commits), ([], 0))
        self.assertTrue(u.path.exists())


if __name__ == "__main__":
    unittest.main()
