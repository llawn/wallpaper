"""I5: rm / mv through the real wiring (bootstrap, GitCli, TomlCatalogue) in a temp git repo.

A folder whose wallpapers.toml has an invalid sibling entry must refuse, leaving the toml and the index alone.
"""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.cli.test_cli import git, make_image
from tests.fakes import FakeFetcher
from tests.services import fixture
from wallman.cli.main import main

INVALID = '[[wallpaper]]\nfile = "b_broken.jpg"\ntitle = "Broken"\nlicense = "public-domain"\nresolution = [3840, 2160]\n'


@unittest.skipUnless(shutil.which("magick") and shutil.which("git"), "ImageMagick or git not installed")
class StructuralRefusalTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root, self.cache = base / "repo", base / "cache"
        self.images = self.root / "images"
        fixture.write_collection(self.images, "painting/x", "painting", "x", [
            fixture.entry("a_valid.jpg", "Valid", "A", "public-domain"), INVALID])
        fixture.write_collection(self.images, "painting/y", "painting", "y", [
            fixture.entry("c_ok.jpg", "Ok", "C", "public-domain")])
        for rel in ("painting/x/a_valid.jpg", "painting/x/b_broken.jpg", "painting/y/c_ok.jpg"):
            make_image(self.images / rel, size="64x36")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.root), *args], fetcher=FakeFetcher(), cache_dir=self.cache)
        return code, out.getvalue() + err.getvalue()

    def assert_refused(self, *args):
        toml = (self.images / "painting/x/wallpapers.toml").read_bytes()
        status = git(self.root, "status", "--porcelain")
        code, out = self.run_cli(*args)
        self.assertEqual(code, 3, out)
        self.assertIn("structural problem", out)
        self.assertEqual(git(self.root, "status", "--porcelain"), status)
        self.assertEqual((self.images / "painting/x/wallpapers.toml").read_bytes(), toml)

    def test_rm_last_valid_entry_refused(self):
        self.assert_refused("rm", "painting/x/a_valid.jpg")
        self.assertTrue((self.images / "painting/x/a_valid.jpg").is_file())

    def test_mv_out_of_broken_folder_refused(self):
        self.assert_refused("mv", "painting/x/a_valid.jpg", "painting/y")

    def test_mv_into_broken_folder_refused(self):
        self.assert_refused("mv", "painting/y/c_ok.jpg", "painting/x")
        self.assertTrue((self.images / "painting/y/c_ok.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
