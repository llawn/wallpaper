import tempfile
import unittest
from pathlib import Path

from tests.services import fixture
from wallman.adapters.fs_store import FsStore
from wallman.adapters.toml_catalogue import TomlCatalogue
from wallman.services.readme import ReadmeService

GOLDEN = Path(__file__).parent / "golden" / "README.md"


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        fixture.build(self.root)
        self.images = self.root / "images"
        self.svc = ReadmeService(self.root, TomlCatalogue(self.images, FsStore(self.images)))

    def test_readme_matches_golden(self):
        self.assertEqual(self.svc.write(), 6)
        self.assertEqual((self.root / "README.md").read_text(), GOLDEN.read_text())

    def test_wording_matches_the_rules(self):
        text = self.svc.render()
        self.assertIn("public domain, CC, GPL, LGPL, MIT, BSD or Apache", text)
        self.assertNotIn("public domain or CC", text)
        self.assertIn("`images/ml4w/`", text)
        self.assertNotIn("│   └── _originals/", text)  # originals live at the repo root, never under images/
        self.assertIn("├── _originals/", text)
        self.assertIn("`just sync --stage`", text)
        self.assertIn("stages their deletion", text)
        self.assertIn("just crop ", text)

    def test_no_counts_or_tables_per_image(self):
        text = self.svc.render()
        self.assertNotIn("Credits by folder", text)
        self.assertNotIn("| Images |", text)
        self.assertNotRegex(text, r"\b28\b")

    def test_adding_a_wallpaper_leaves_readme_unchanged(self):
        before = self.svc.render()
        toml = self.images / "painting/impressionism/wallpapers.toml"
        toml.write_text(toml.read_text() + "\n" + fixture.entry(
            "pierre-renoir_boating_1881.jpg", "Boating", "Pierre Renoir", "public-domain", 1881))
        self.assertEqual(self.svc.render(), before)

    def test_new_deep_folder_leaves_readme_unchanged(self):
        before = self.svc.render()
        fixture.write_collection(self.images, "animation/studio-ghibli/2099-new-film", "animation", "studio-ghibli", [
            fixture.entry("studio-ghibli_new-01_2099.jpg", "New", "Studio Ghibli", "ghibli-free-use", 2099)])
        self.assertEqual(self.svc.render(), before)
        self.assertNotIn("1988-my-neighbor-totoro", before)


if __name__ == "__main__":
    unittest.main()
