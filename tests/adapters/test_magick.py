import subprocess
import tempfile
import unittest
from pathlib import Path

from wallman.adapters.magick import Magick
from wallman.domain.models import CropBox
from wallman.errors import InvalidImage


class MagickTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.png = self.dir / "src.png"
        subprocess.run(["magick", "-size", "400x300", "xc:red", str(self.png)], check=True)
        self.tool = Magick()

    def test_identify_and_crop(self):
        r = self.tool.identify(self.png)
        self.assertEqual((r.width, r.height), (400, 300))
        dst = self.dir / "out.png"
        self.tool.crop(self.png, CropBox(0, 38, 400, 225), dst)
        r = self.tool.identify(dst)
        self.assertEqual((r.width, r.height), (400, 225))

    def test_identify_rejects_text_file(self):
        t = self.dir / "t.png"
        t.write_text("not an image")
        with self.assertRaises(InvalidImage):
            self.tool.identify(t)

    def test_identify_missing_file(self):
        with self.assertRaises(InvalidImage):
            self.tool.identify(self.dir / "nope.png")

    def test_thumbnail_is_jpeg_whatever_the_extension(self):
        dst = self.dir / "k.1.part"
        self.tool.thumbnail(self.png, dst, 100)
        self.assertEqual(dst.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertEqual(self.tool.identify(dst).width, 100)

    def test_convert_thumbnail_render(self):
        jpg = self.dir / "o.jpg"
        self.tool.convert(self.png, jpg)
        self.assertEqual(self.tool.identify(jpg).width, 400)
        th = self.dir / "th.jpg"
        self.tool.thumbnail(self.png, th, 100)
        self.assertEqual(self.tool.identify(th).width, 100)
        svg = self.dir / "a.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
                       '<rect width="100" height="50" fill="blue"/></svg>')
        out = self.dir / "a.png"
        self.tool.render_svg(svg, out)
        r = self.tool.identify(out)
        self.assertEqual((r.width, r.height), (3840, 1920))


if __name__ == "__main__":
    unittest.main()
