import hashlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeFetcher, FakeImageTool
from wallman.adapters.magick import Magick
from wallman.domain.models import Resolution
from wallman.errors import DownloadFailed, InvalidImage, UploadTooLarge
from wallman.services.uploads import Uploads


class UploadsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "uploads"
        self.tool = FakeImageTool()
        self.fetcher = FakeFetcher({"https://x/y/pic.PNG?a=1": b"png-bytes"})
        self.up = Uploads(self.cache, self.tool, self.fetcher, max_bytes=100)

    def test_from_bytes_writes_identifies_and_hashes(self):
        self.tool.default = Resolution(3000, 1700)
        u = self.up.from_bytes("My Pic.JPEG", b"abc")
        self.assertEqual(u.path.parent, self.cache)
        self.assertEqual(u.path.suffix, ".jpg")
        self.assertEqual(u.path.read_bytes(), b"abc")
        self.assertEqual((u.original_name, u.resolution, u.download), ("My Pic.JPEG", Resolution(3000, 1700), None))
        self.assertEqual(u.sha256, hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(self.up.get(u.id), u)

    def test_from_stream_copies_and_hashes(self):
        self.tool.default = Resolution(3000, 1700)
        u = self.up.from_stream("a.png", io.BytesIO(b"abcdef-extra"), 6)
        self.assertEqual(u.path.read_bytes(), b"abcdef")
        self.assertEqual(u.sha256, hashlib.sha256(b"abcdef").hexdigest())
        self.assertEqual(self.up.get(u.id), u)

    def test_from_stream_size_limit_and_cleanup(self):
        with self.assertRaises(UploadTooLarge):
            self.up.from_stream("a.jpg", io.BytesIO(b""), 101)
        with self.assertRaises(InvalidImage):  # body shorter than announced
            self.up.from_stream("a.jpg", io.BytesIO(b"abc"), 10)
        with self.assertRaises(InvalidImage):  # identify fails: not an image
            self.up.from_stream("bad.jpg", io.BytesIO(b"not an image"), 12)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_unknown_extension_becomes_jpg(self):
        self.tool.default = Resolution(3000, 1700)
        self.assertEqual(self.up.from_bytes("noext", b"a").path.suffix, ".jpg")

    def test_too_large_rejected_before_writing(self):
        with self.assertRaises(UploadTooLarge):
            self.up.from_bytes("a.jpg", b"x" * 101)
        self.assertFalse(self.cache.exists() and any(self.cache.iterdir()))

    def test_invalid_image_deletes_file(self):
        with self.assertRaises(InvalidImage):
            self.up.from_bytes("a.jpg", b"not an image")
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_from_url_records_download_and_ext(self):
        self.tool.default = Resolution(3000, 1700)
        u = self.up.from_url("https://x/y/pic.PNG?a=1")
        self.assertEqual(u.path.suffix, ".png")
        self.assertEqual((u.download, u.original_name), ("https://x/y/pic.PNG?a=1", "pic.PNG"))
        self.assertEqual(u.sha256, hashlib.sha256(b"png-bytes").hexdigest())

    def test_from_url_refuses_non_web_urls(self):
        with self.assertRaises(ValueError):
            self.up.from_url("file:///etc/passwd")
        self.assertEqual(self.fetcher.calls, [])

    def test_from_url_failure_propagates_and_leaves_cache_empty(self):
        with self.assertRaises(DownloadFailed):
            self.up.from_url("https://nope/a.jpg")
        self.assertEqual(list(self.cache.iterdir()) if self.cache.exists() else [], [])

    def test_discard_and_purge(self):
        self.tool.default = Resolution(3000, 1700)
        a = self.up.from_bytes("a.jpg", b"1")
        b = self.up.from_bytes("b.jpg", b"2")
        self.up.discard(a.id)
        self.assertFalse(a.path.exists())
        self.up.discard(a.id)  # idempotent
        self.up.purge()
        self.assertFalse(b.path.exists())
        self.assertEqual(list(self.cache.iterdir()), [])


WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"/>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FormatTest(unittest.TestCase):
    """I3: the stored format follows the bytes, not the file name; WebP/JXL/AVIF/... are converted, SVG rendered."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "uploads"
        self.tool = FakeImageTool(default=Resolution(3000, 1700))
        self.up = Uploads(self.cache, self.tool, FakeFetcher({"https://x/a.webp": WEBP}))

    def test_webp_is_converted_to_jpeg_and_keeps_download_checksum(self):
        u = self.up.from_url("https://x/a.webp")
        self.assertEqual((u.path.suffix, u.processing), (".jpg", ("convert",)))
        self.assertEqual(u.sha256, hashlib.sha256(WEBP).hexdigest())
        self.assertEqual([c[0] for c in self.tool.calls], ["convert"])
        self.assertEqual(list(self.cache.iterdir()), [u.path])  # the raw download is gone

    def test_webp_named_jpg_is_still_converted(self):
        u = self.up.from_bytes("lies.jpg", WEBP)
        self.assertEqual((u.path.suffix, u.processing), (".jpg", ("convert",)))

    def test_other_signatures_are_converted(self):
        for name, data in (("a.jxl", b"\xff\x0a" + b"\x00" * 10),
                           ("b.jxl", b"\x00\x00\x00\x0cJXL \r\n\x87\n"),
                           ("c.avif", b"\x00\x00\x00\x1cftypavif" + b"\x00" * 8),
                           ("d.gif", b"GIF89a" + b"\x00" * 8)):
            with self.subTest(name=name):
                self.assertEqual(self.up.from_bytes(name, data).processing, ("convert",))

    def test_svg_is_rendered_to_png(self):
        u = self.up.from_bytes("logo.svg", SVG)
        self.assertEqual((u.path.suffix, u.processing, u.resolution), (".png", ("render",), Resolution(3840, 2160)))
        self.assertEqual(u.sha256, hashlib.sha256(SVG).hexdigest())

    def test_png_named_jpg_is_stored_as_png(self):
        u = self.up.from_bytes("pic.jpg", PNG)
        self.assertEqual((u.path.suffix, u.processing), (".png", ()))
        self.assertEqual(u.path.read_bytes(), PNG)


@unittest.skipUnless(shutil.which("magick") and shutil.which("rsvg-convert"), "ImageMagick/rsvg-convert not installed")
class RealFormatTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.up = Uploads(self.dir / "uploads", Magick(), FakeFetcher())

    def test_magick_webp_becomes_a_real_jpeg(self):
        src = self.dir / "src.webp"
        subprocess.run(["magick", "-size", "2000x1000", "xc:green", str(src)], check=True)
        u = self.up.from_bytes("src.webp", src.read_bytes())
        self.assertEqual((u.path.suffix, u.processing, u.resolution), (".jpg", ("convert",), Resolution(2000, 1000)))
        self.assertEqual(u.path.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertEqual(u.sha256, hashlib.sha256(src.read_bytes()).hexdigest())

    def test_tiny_svg_is_rendered_to_png(self):
        u = self.up.from_bytes("a.svg", SVG)
        self.assertEqual((u.path.suffix, u.processing, u.resolution), (".png", ("render",), Resolution(3840, 1920)))
        self.assertEqual(u.path.read_bytes()[:8], PNG[:8])


if __name__ == "__main__":
    unittest.main()
