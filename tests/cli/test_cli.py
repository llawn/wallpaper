import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeFetcher
from tests.services import fixture
from wallman import bootstrap
from wallman.cli.main import main

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_image(path: Path, size="3840x2160", color="red") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["magick", "-size", size, f"xc:{color}", str(path)], check=True)
    return path.read_bytes()


@unittest.skipUnless(shutil.which("magick"), "ImageMagick not installed")
class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root = base / "repo"
        self.cache = base / "cache"
        self.images = self.root / "images"
        self.images.mkdir(parents=True)
        fixture.write_collection(self.images, "painting/impressionism", "painting", "impressionism", [
            fixture.entry("claude-monet_water-lilies_1906.jpg", "Water Lilies", "Claude Monet", "public-domain",
                          1906, "https://example.org/monet")])
        make_image(self.images / "painting/impressionism/claude-monet_water-lilies_1906.jpg")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        self.fetcher = FakeFetcher()
        self.src = base / "src"

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.root), *args], fetcher=self.fetcher, cache_dir=self.cache)
        return code, out.getvalue(), err.getvalue()

    def staged(self):
        return git(self.root, "diff", "--cached", "--name-only").split()

    ADD = ["--artist", "Edgar Degas", "--title", "Blue Dancers", "--year", "1893", "--license", "public-domain",
           "--source", "https://example.org/degas"]

    def test_validate_exit_codes(self):
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 0)
        self.assertIn("1 folders checked, 0 problem(s)", out)
        (self.images / "painting/impressionism/claude-monet_water-lilies_1906.jpg").unlink()
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("✗ painting/impressionism/claude-monet_water-lilies_1906.jpg: missing —", out)
        self.assertIn("1 problem(s)", out)

    def test_sync_stage_stages_deleted_image(self):
        fixture.write_collection(self.images, "painting/impressionism", "painting", "impressionism", [
            fixture.entry("claude-monet_water-lilies_1906.jpg", "Water Lilies", "Claude Monet", "public-domain",
                          1906, "https://example.org/monet"),
            fixture.entry("b_two.jpg", "Two", "B", "public-domain")])
        make_image(self.images / "painting/impressionism/b_two.jpg")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "two")
        (self.images / "painting/impressionism/b_two.jpg").unlink()
        code, out, err = self.run_cli("sync", "--stage")
        self.assertEqual(code, 0, out + err)
        self.assertIn("D\timages/painting/impressionism/b_two.jpg",
                      git(self.root, "diff", "--cached", "--name-status").splitlines())

    def test_crop_command(self):
        ref = "painting/impressionism/claude-monet_water-lilies_1906.jpg"
        code, out, err = self.run_cli("crop", ref, "100,50,3200,1800")
        self.assertEqual(code, 0, out + err)
        self.assertIn("3200x1800", out)
        size = subprocess.run(["magick", "identify", "-format", "%wx%h", str(self.images / ref)],
                              capture_output=True, text=True).stdout
        self.assertEqual(size, "3200x1800")
        self.assertIn(f"images/{ref}", self.staged())
        code, _, err = self.run_cli("crop", ref, "0,0,10")
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("crop", ref, "0,0,1600,900")
        self.assertEqual(code, 3)
        self.assertIn("below", err)

    def test_add_then_rm_roundtrip(self):
        img = self.src / "d.jpg"
        make_image(img, color="blue")
        code, out, _ = self.run_cli("add", str(img), "painting/impressionism", *self.ADD)
        self.assertEqual(code, 0, out)
        ref = "painting/impressionism/edgar-degas_blue-dancers_1893.jpg"
        self.assertEqual(out.strip(), ref)
        self.assertTrue((self.images / ref).is_file())
        self.assertIn(f"images/{ref}", self.staged())
        self.assertIn("Blue Dancers", (self.images / "painting/impressionism/wallpapers.toml").read_text())
        self.assertEqual(self.run_cli("validate")[0], 0)
        rc = self.run_cli("rm", ref)
        self.assertEqual(rc[0], 0, rc)
        self.assertFalse((self.images / ref).exists())
        self.assertNotIn("Blue Dancers", (self.images / "painting/impressionism/wallpapers.toml").read_text())
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_add_url_downloads_and_records_sha(self):
        data = make_image(self.src / "u.jpg", color="green")
        self.fetcher.responses["https://example.org/x/d.jpg"] = data
        code, out, _ = self.run_cli("add-url", "https://example.org/x/d.jpg", "painting/impressionism", *self.ADD)
        self.assertEqual(code, 0, out)
        text = (self.images / "painting/impressionism/wallpapers.toml").read_text()
        self.assertIn('download = "https://example.org/x/d.jpg"', text)
        self.assertIn("sha256", text)

    def test_mv_moves_to_new_folder(self):
        ref = "painting/impressionism/claude-monet_water-lilies_1906.jpg"
        code, out, _ = self.run_cli("mv", ref, "painting/nocturne")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.images / "painting/nocturne/claude-monet_water-lilies_1906.jpg").is_file())
        self.assertFalse((self.images / ref).exists())

    def test_sync_stage_stages_metadata(self):
        make_image(self.images / "painting/impressionism/pierre-renoir_boating_1880.jpg", color="blue")
        code, out, _ = self.run_cli("sync", "--stage")
        self.assertEqual(code, 0, out)
        self.assertIn("images/painting/impressionism/wallpapers.toml", self.staged())
        self.assertIn("README.md", self.staged())
        self.assertIn("Boating", (self.images / "painting/impressionism/wallpapers.toml").read_text())

    def test_sync_without_stage_leaves_index_unchanged(self):
        make_image(self.images / "painting/impressionism/pierre-renoir_boating_1880.jpg", color="blue")
        code, out, _ = self.run_cli("sync")
        self.assertEqual(code, 0, out)
        self.assertIn("Boating", (self.images / "painting/impressionism/wallpapers.toml").read_text())
        self.assertEqual(self.staged(), [])

    def test_guard_refuses_non_shareable_staged(self):
        self.assertEqual(self.run_cli("guard")[0], 0)
        fixture.write_collection(self.images, "poster", "poster", "film", [
            fixture.entry("studio_poster_2001.jpg", "Poster", "Some Studio", "all-rights-reserved", 2001)])
        make_image(self.images / "poster/studio_poster_2001.jpg")
        git(self.root, "add", "-f", "images/poster/studio_poster_2001.jpg")
        code, out, _ = self.run_cli("guard")
        self.assertEqual(code, 1)
        self.assertIn("commit refused", out)
        self.assertIn("images/poster/studio_poster_2001.jpg", out)

    def test_gitignore_and_readme_write_files(self):
        self.assertEqual(self.run_cli("gitignore")[0], 0)
        self.assertEqual(self.run_cli("readme")[0], 0)
        self.assertIn("generated", (self.root / ".gitignore").read_text())
        self.assertTrue((self.root / "README.md").is_file())

    def test_images_prefix_is_accepted_in_refs_and_dest(self):
        ref = "painting/impressionism/claude-monet_water-lilies_1906.jpg"
        code, out, _ = self.run_cli("mv", f"images/{ref}", "images/painting/nocturne")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.images / "painting/nocturne/claude-monet_water-lilies_1906.jpg").is_file())
        code, out, _ = self.run_cli("rm", "images/painting/nocturne/claude-monet_water-lilies_1906.jpg")
        self.assertEqual(code, 0, out)

    def test_generated_files_live_at_repo_root_with_images_prefix(self):
        self.run_cli("gitignore")
        self.run_cli("readme")
        self.assertFalse((self.images / "README.md").exists())
        self.assertFalse((self.images / ".gitignore").exists())
        self.assertIn("(images/painting/impressionism/wallpapers.toml)", (self.root / "README.md").read_text())

    def test_build_without_images_folder_is_a_clear_error(self):
        bare = self.root.parent / "bare"
        bare.mkdir()
        out, errb = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errb):
            code = main(["--root", str(bare), "validate"], cache_dir=self.cache)
        self.assertEqual(code, 3)
        self.assertIn("no images/ folder", errb.getvalue())

    def test_nothing_outside_images_is_listed(self):
        make_image(self.root / "stray.jpg")
        make_image(self.root / "docs/shot.png")
        make_image(self.root / "painting/loose/at-root_image.jpg")  # media-looking folder beside images/, not inside
        fixture.write_collection(self.root, "painting/rootside", "painting", "rootside", [])
        app = bootstrap.build(self.root, self.cache)
        self.assertEqual(app.store.collection_folders(), ["painting/impressionism"])
        self.assertEqual(app.store.image_folders(), ["painting/impressionism"])
        self.assertEqual([c.folder for c in app.catalogue.collections()], ["painting/impressionism"])
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 0, out)
        self.assertIn("1 folders checked, 0 problem(s)", out)

    def test_fetch_reports_nothing_missing(self):
        code, out, _ = self.run_cli("fetch")
        self.assertEqual(code, 0)
        self.assertIn("0 downloaded", out)

    def test_wallman_error_exits_3(self):
        code, _, err = self.run_cli("rm", "painting/impressionism/nope.jpg")
        self.assertEqual(code, 3)
        self.assertTrue(err.startswith("error: "))

    def test_missing_file_exits_3(self):
        code, _, err = self.run_cli("add", str(self.src / "nope.jpg"), "painting/a", *self.ADD)
        self.assertEqual(code, 3)
        self.assertIn("error:", err)

    def test_bad_crop_is_usage_error(self):
        code, _, _ = self.run_cli("add", "x.jpg", "painting/a", *self.ADD, "--crop", "1,2,3")
        self.assertEqual(code, 2)

    def test_serve_is_importable_and_help_exits_0(self):
        from wallman.web.server import serve
        self.assertTrue(callable(serve))
        code, out, _ = self.run_cli("serve", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--no-open", out)

    def test_build_exposes_every_service(self):
        app = bootstrap.build(self.root, self.cache)
        self.assertEqual(app.images_root, self.images)
        self.assertEqual(app.thumbs_dir, self.cache / "thumbs")
        for name in ("store", "catalogue", "tool", "vcs", "fetcher", "uploads", "importer", "remover", "library",
                     "cropper", "syncer", "fetch_service", "validator", "gitignore", "readme", "uow_factory"):
            self.assertIsNotNone(getattr(app, name), name)

    def test_pre_commit_hook_refuses_unlisted_staged_image(self):
        hook_dir = self.root / "scripts" / "hooks"
        hook_dir.mkdir(parents=True)
        shutil.copy(Path(__file__).parents[2] / "scripts/hooks/pre-commit", hook_dir / "pre-commit")
        shutil.copytree(Path(__file__).parents[2] / "wallman", self.root / "wallman",
                        ignore=shutil.ignore_patterns("__pycache__"))
        git(self.root, "config", "core.hooksPath", "scripts/hooks")
        make_image(self.images / "painting/impressionism/pierre-renoir_boating_1880.jpg", color="blue")
        git(self.root, "add", "-A")
        env = {**ENV, "WALLPAPERS_SYNC": "0"}
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.root),
                            "commit", "-q", "-m", "x"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("commit refused", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
