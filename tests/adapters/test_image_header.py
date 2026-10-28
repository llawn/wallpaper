import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from wallman.adapters.image_header import read_size
from wallman.adapters.magick import Magick


def _magick(path: Path, *args: str, size: str = "64x48") -> None:
    subprocess.run(["magick", "-size", size, "xc:red", *args, str(path)], check=True)


class ReadSizeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _size(self, p):
        r = read_size(p)
        return None if r is None else (r.width, r.height)

    def test_baseline_jpeg(self):
        p = self.dir / "a.jpg"
        _magick(p)
        self.assertEqual(self._size(p), (64, 48))

    def test_progressive_jpeg(self):
        p = self.dir / "p.jpg"
        _magick(p, "-interlace", "JPEG")
        self.assertEqual(self._size(p), (64, 48))

    def test_png(self):
        p = self.dir / "a.png"
        _magick(p)
        self.assertEqual(self._size(p), (64, 48))

    def test_jpeg_with_app1_before_sof(self):
        src = self.dir / "a.jpg"
        _magick(src)
        data = src.read_bytes()
        payload = b"Exif\x00\x00" + b"x" * 300
        app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        p = self.dir / "exif.jpg"
        p.write_bytes(data[:2] + app1 + data[2:])
        self.assertEqual(self._size(p), (64, 48))

    def test_truncated(self):
        for name, args in (("t.jpg", ()), ("t.png", ())):
            src = self.dir / name
            _magick(src, *args)
            for cut in (0, 1, 2, 10, 20):
                p = self.dir / f"cut{cut}-{name}"
                p.write_bytes(src.read_bytes()[:cut])
                self.assertIsNone(read_size(p), (name, cut))

    def test_garbage_and_unknown(self):
        t = self.dir / "t.jpg"
        t.write_text("not an image")
        self.assertIsNone(read_size(t))
        g = self.dir / "a.gif"
        _magick(g)
        self.assertIsNone(read_size(g))

    def test_zero_dimensions_is_none(self):
        p = self.dir / "z.jpg"
        p.write_bytes(b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x00\x00\x00" + b"\x00" * 10)
        self.assertIsNone(read_size(p))

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            read_size(self.dir / "nope.jpg")


class IdentifyFallbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_uses_header_without_subprocess(self):
        p = self.dir / "a.png"
        _magick(p)
        from unittest import mock
        with mock.patch("wallman.adapters.magick._run", side_effect=AssertionError("spawned")):
            r = Magick().identify(p)
        self.assertEqual((r.width, r.height), (64, 48))

    def test_falls_back_for_gif_and_webp(self):
        for ext in ("gif", "webp"):
            p = self.dir / f"a.{ext}"
            _magick(p)
            r = Magick().identify(p)
            self.assertEqual((r.width, r.height), (64, 48), ext)


if __name__ == "__main__":
    unittest.main()
