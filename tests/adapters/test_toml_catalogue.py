import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from wallman.adapters.toml_catalogue import TomlCatalogue
from wallman.domain.models import Collection, CropBox, Resolution, Wallpaper
from wallman.domain.years import YearSpan
from wallman.errors import NameConflict, NotFound, WallmanError

HEADER = "#:schema ../../wallman/schema/wallpapers.schema.json\n# second comment line\n\n"
SAMPLE = HEADER + '''[collection]
medium = "painting"
movement = "impressionism"
count = 2
[[wallpaper]]
file = "a_b.jpg"
title = "Jour d'été"
artist = "A"
year = 1879
license = "public-domain"
resolution = [3840, 2347]
[[wallpaper]]
file = "c_d.jpg"
title = "D"
artist = "C"
license = "all-rights-reserved"
license_detail = "© C"
download = "https://example.org/d.jpg"
sha256 = "%s"
resolution = [3000, 2000]
processing = ["crop"]
crop = [0, 100, 3000, 1688]
original = "originals/c_d.jpg"
''' % ("a" * 64)


class FakeStore:
    def __init__(self, folders=(), external=()):
        self.folders = list(folders)
        self.external = set(external)

    def is_external(self, folder):
        return folder in self.external

    def collection_folders(self):
        return self.folders


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = FakeStore(["painting/x"])
        self.cat = TomlCatalogue(self.root, self.store)

    def write(self, folder, text):
        d = self.root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "wallpapers.toml").write_text(text, encoding="utf-8")
        return d / "wallpapers.toml"


class TomlCatalogueTest(Base):
    def test_load_parses_entries_and_header(self):
        self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        self.assertEqual((c.folder, c.medium, c.movement, c.header), ("painting/x", "painting", "impressionism", HEADER))
        self.assertEqual(len(c.wallpapers), 2)
        self.assertEqual(c.wallpapers[0].year, YearSpan(1879, 1879))
        self.assertEqual(c.wallpapers[0].resolution, Resolution(3840, 2347))
        w = c.wallpapers[1]
        self.assertEqual((w.processing, w.crop, w.original), (("crop",), CropBox(0, 100, 3000, 1688), "originals/c_d.jpg"))

    def test_zero_or_negative_resolution_is_structural(self):
        for res in ("[0, 2160]", "[3840, -1]"):
            with self.subTest(res=res):
                self.write("painting/x", SAMPLE.replace("resolution = [3840, 2347]", f"resolution = {res}"))
                codes = [(i.file, i.code) for i in self.cat.structural_issues("painting/x")]
                self.assertIn(("a_b.jpg", "field-type"), codes)
                self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["c_d.jpg"])

    def test_load_missing_raises_not_found(self):
        with self.assertRaises(NotFound):
            self.cat.load("painting/none")

    def test_external_flag_comes_from_store(self):
        self.write("painting/x", SAMPLE)
        self.store.external.add("painting/x")
        self.assertTrue(self.cat.load("painting/x").external)

    def test_collections_uses_store_folders_and_skips_missing(self):
        self.write("painting/x", SAMPLE)
        self.store.folders.append("painting/nothing")
        self.assertEqual([c.folder for c in self.cat.collections()], ["painting/x"])

    def test_roundtrip_keeps_header_comments(self):
        path = self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        self.cat.save(c)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(HEADER + "[collection]"))
        self.assertEqual(self.cat.load("painting/x").wallpapers, c.wallpapers)
        self.assertEqual(tomllib.loads(text), tomllib.loads(SAMPLE))

    def test_save_is_atomic(self):
        path = self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        c.wallpapers.pop()
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.cat.save(c)
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)
        self.assertEqual(os.listdir(path.parent), ["wallpapers.toml"])

    def test_save_writes_fields_in_canonical_order(self):
        w = Wallpaper("a.jpg", "T", "A", "CC-BY-4.0", Resolution(10, 20), year=2000, license_detail="d", source="https://s",
                      download="https://d", sha256="b" * 64, processing=("crop",), original="o/a.jpg", crop=CropBox(1, 2, 3, 4))
        self.cat.save(Collection("painting/x", "painting", "x", [w]))
        text = (self.root / "painting/x/wallpapers.toml").read_text(encoding="utf-8")
        keys = [ln.split(" =")[0] for ln in text.splitlines() if " = " in ln][3:]
        self.assertEqual(keys, ["file", "title", "artist", "year", "license", "license_detail", "source", "download",
                                "sha256", "resolution", "processing", "crop", "original"])
        self.assertIn("crop = [1, 2, 3, 4]\n", text)
        self.assertIn('processing = ["crop"]\n', text)
        self.assertIn("resolution = [10, 20]\n", text)

    def test_count_follows_entries(self):
        self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        c.wallpapers.pop()
        self.cat.save(c)
        self.assertEqual(tomllib.loads((self.root / "painting/x/wallpapers.toml").read_text(encoding="utf-8"))["collection"]["count"], 1)

    def test_strings_are_escaped_and_unicode_kept(self):
        w = Wallpaper("a.jpg", 'Say "hi" \\ é\t', "A", "MIT", Resolution(1, 2))
        self.cat.save(Collection("painting/x", "painting", "x", [w]))
        self.assertEqual(self.cat.load("painting/x").wallpapers[0].title, 'Say "hi" \\ é\t')

    def test_personal_license_gets_todo_comment(self):
        w = Wallpaper("a.jpg", "T", "A", "personal", Resolution(1, 2))
        self.cat.save(Collection("personal/x", "personal", "x", [w]))
        text = (self.root / "personal/x/wallpapers.toml").read_text(encoding="utf-8")
        self.assertIn('license = "personal"  # TODO: set the real license (and source) to share it\n', text)
        self.assertEqual(self.cat.load("personal/x").wallpapers[0].license, "personal")

    def test_create_new_folder_toml(self):
        c = self.cat.create("linux/desktops/foo")
        self.assertEqual((c.medium, c.movement), ("linux", "foo"))
        self.assertEqual(c.header, "#:schema ../../../../wallman/schema/wallpapers.schema.json\n\n")
        text = (self.root / "linux/desktops/foo/wallpapers.toml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(c.header + "[collection]"))
        self.assertEqual(self.cat.load("linux/desktops/foo").wallpapers, [])

    def test_create_refuses_existing_toml(self):
        path = self.write("painting/x", SAMPLE)
        with self.assertRaises(NameConflict):
            self.cat.create("painting/x")
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)

    def test_delete_removes_toml(self):
        path = self.write("painting/x", SAMPLE)
        self.cat.delete("painting/x")
        self.assertFalse(path.exists())
        with self.assertRaises(NotFound):
            self.cat.delete("painting/x")

    def test_save_refused_when_entry_has_unknown_key(self):
        text = SAMPLE.replace('artist = "C"\n', 'artist = "C"\nmystery = 1\n')
        path = self.write("painting/x", text)
        c = self.cat.load("painting/x")
        with self.assertRaises(WallmanError):
            self.cat.save(c)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_save_refused_when_collection_has_unknown_key(self):
        text = SAMPLE.replace("count = 2\n", "count = 2\nbogus = 1\n")
        path = self.write("painting/x", text)
        c = self.cat.load("painting/x")
        with self.assertRaises(WallmanError):
            self.cat.save(c)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_create_slugs_movement(self):
        self.assertEqual(self.cat.create("painting/Art Nouveau").movement, "art-nouveau")


class StructuralIssuesTest(Base):
    def codes(self, text):
        self.write("painting/x", text)
        return {(i.code, i.file) for i in self.cat.structural_issues("painting/x")}

    def test_clean_file_has_no_issues(self):
        self.write("painting/x", SAMPLE)
        self.assertEqual(self.cat.structural_issues("painting/x"), [])

    def test_invalid_toml(self):
        self.write("painting/x", "[collection\n")
        issues = self.cat.structural_issues("painting/x")
        self.assertEqual([(i.code, i.level) for i in issues], [("invalid-toml", "error")])

    def test_undecodable_toml_is_an_issue_and_load_is_a_wallman_error(self):
        p = self.write("painting/x", SAMPLE)
        p.write_bytes(b'[collection]\nmedium = "\xff"\n')
        self.assertEqual([i.code for i in self.cat.structural_issues("painting/x")], ["invalid-toml"])
        with self.assertRaises(WallmanError):
            self.cat.load("painting/x")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_unreadable_toml_is_an_issue_and_load_is_a_wallman_error(self):
        p = self.write("painting/x", SAMPLE)
        p.chmod(0)
        self.addCleanup(p.chmod, 0o644)
        self.assertEqual([i.code for i in self.cat.structural_issues("painting/x")], ["unreadable"])
        with self.assertRaises(WallmanError):
            self.cat.load("painting/x")

    def test_count_mismatch(self):
        self.assertIn(("count", None), self.codes(SAMPLE.replace("count = 2", "count = 5")))

    def test_field_type_missing_and_unknown(self):
        text = HEADER + '''[collection]
medium = "painting"
movement = "x"
count = 1
bogus = 1
[[wallpaper]]
file = "a.jpg"
title = 3
artist = "A"
resolution = [1, 2, 3]
mystery = "x"
'''
        got = self.codes(text)
        self.assertIn(("field-type", "a.jpg"), got)
        self.assertIn(("missing-field", "a.jpg"), got)  # license
        self.assertIn(("unknown-field", "a.jpg"), got)
        self.assertIn(("unknown-field", None), got)

    def test_load_skips_structurally_invalid_entries(self):
        text = SAMPLE.replace('artist = "C"\n', "")
        self.write("painting/x", text)
        self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["a_b.jpg"])
        self.assertIn(("missing-field", "c_d.jpg"), {(i.code, i.file) for i in self.cat.structural_issues("painting/x")})


class ParseCacheTest(Base):
    """A 10,000-entry wallpapers.toml takes ~0.5 s to parse: one parse per file version, none after save()."""

    def count_parses(self):
        real = tomllib.loads
        calls = []
        patcher = mock.patch("wallman.adapters.toml_catalogue.tomllib.loads",
                             side_effect=lambda text: calls.append(1) or real(text))
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_repeated_reads_parse_once(self):
        self.write("painting/x", SAMPLE)
        calls = self.count_parses()
        self.cat.load("painting/x")
        self.cat.structural_issues("painting/x")
        self.cat.collections()
        self.assertEqual(len(calls), 1)

    def test_loaded_lists_are_independent(self):
        self.write("painting/x", SAMPLE)
        self.cat.load("painting/x").wallpapers.pop()
        self.assertEqual(len(self.cat.load("painting/x").wallpapers), 2)

    def test_external_edit_is_seen(self):
        path = self.write("painting/x", SAMPLE)
        self.cat.load("painting/x")
        st = path.stat()
        path.write_text(SAMPLE.replace('title = "D"', 'title = "E"'), encoding="utf-8")  # same size
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # same mtime: the ctime still differs
        self.assertEqual(self.cat.load("painting/x").wallpapers[1].title, "E")
        path.write_text("[collection\n", encoding="utf-8")
        self.assertEqual([i.code for i in self.cat.structural_issues("painting/x")], ["invalid-toml"])
        with self.assertRaises(WallmanError):
            self.cat.load("painting/x")
        path.unlink()
        with self.assertRaises(NotFound):
            self.cat.load("painting/x")

    def test_save_stores_exactly_what_a_parse_gives(self):
        self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        w = c.wallpapers[0]
        c.wallpapers[0] = Wallpaper(
            file=w.file, title='Quote " back\\slash \t tab \x7f del é 日本', artist="A", license="CC0-1.0",
            resolution=Resolution(3840, 2160), year=YearSpan(1880, 1889), tags=("night", "blue", "night"),
            license_detail="", source="https://example.org/a", processing=("crop",), crop=CropBox(1, 2, 3, 4))
        calls = self.count_parses()
        self.cat.save(c)
        seeded = self.cat.load("painting/x")
        self.assertEqual((len(calls), self.cat.structural_issues("painting/x")), (0, []))
        fresh = TomlCatalogue(self.root, self.store)
        self.assertEqual(seeded, fresh.load("painting/x"))
        self.assertEqual(fresh.structural_issues("painting/x"), [])
        self.assertEqual(seeded.wallpapers[0].tags, ("blue", "night"))

    def test_save_of_a_bad_resolution_is_parsed_back(self):
        self.write("painting/x", SAMPLE)
        c = self.cat.load("painting/x")
        c.wallpapers[0] = Wallpaper(file="a_b.jpg", title="T", artist="A", license="CC0-1.0", resolution=Resolution(0, 5))
        self.cat.save(c)
        self.assertIn(("a_b.jpg", "field-type"), [(i.file, i.code) for i in self.cat.structural_issues("painting/x")])


class RealCollectionTest(unittest.TestCase):
    def test_every_real_toml_roundtrips(self):
        real = Path(os.environ.get("WALLMAN_REAL_ROOT", "/home/llawn/Pictures/Wallpapers/wallpapers"))
        if not real.is_dir():
            self.skipTest(f"{real} not present")
        real = real / "images" if (real / "images").is_dir() else real  # works before and after the move
        folders = []
        for dirpath, dirnames, filenames in os.walk(real):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
            if "wallpapers.toml" in filenames:
                folders.append(Path(dirpath).relative_to(real).as_posix())
        if not folders:
            self.skipTest("no wallpapers.toml found")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in folders:
                (root / f).mkdir(parents=True)
                shutil.copy(real / f / "wallpapers.toml", root / f / "wallpapers.toml")
            cat = TomlCatalogue(root, FakeStore(folders))
            for f in folders:
                with self.subTest(folder=f):
                    path = root / f / "wallpapers.toml"
                    before = path.read_bytes()
                    c = cat.load(f)
                    self.assertEqual(cat.structural_issues(f), [])
                    cat.save(c)
                    self.assertEqual(cat.load(f), TomlCatalogue(root, FakeStore(folders)).load(f))  # seeded == parsed
                    after = path.read_bytes()
                    self.assertEqual(tomllib.loads(after.decode()), tomllib.loads(before.decode()))
                    marker = b"[collection]"
                    self.assertEqual(after[:after.index(marker)], before[:before.index(marker)])


if __name__ == "__main__":
    unittest.main()


class YearTagsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cat = TomlCatalogue(self.root, FakeStore(["painting/x"]))

    def toml(self, extra):
        d = self.root / "painting/x"
        d.mkdir(parents=True, exist_ok=True)
        (d / "wallpapers.toml").write_text(
            '[collection]\nmedium = "painting"\nmovement = "x"\ncount = 1\n[[wallpaper]]\nfile = "a.jpg"\n'
            'title = "T"\nartist = "A"\nlicense = "public-domain"\nresolution = [10, 20]\n' + extra, encoding="utf-8")
        return d / "wallpapers.toml"

    def test_span_and_tags_roundtrip(self):
        path = self.toml("year = [1880, 1889]\ntags = [\"zebra\", \"alpha\"]\n")
        c = self.cat.load("painting/x")
        self.assertEqual(c.wallpapers[0].year, YearSpan(1880, 1889))
        self.assertEqual(c.wallpapers[0].tags, ("zebra", "alpha"))
        self.cat.save(c)
        data = tomllib.loads(path.read_text(encoding="utf-8"))["wallpaper"][0]
        self.assertEqual((data["year"], data["tags"]), ([1880, 1889], ["alpha", "zebra"]))  # sorted on write
        self.assertEqual(self.cat.structural_issues("painting/x"), [])

    def test_single_year_written_as_int_and_tags_after_year(self):
        w = Wallpaper("a.jpg", "T", "A", "CC0-1.0", Resolution(10, 20), year=YearSpan(1889, 1889), tags=("b", "a", "b"))
        self.cat.save(Collection("painting/x", "painting", "x", [w]))
        text = (self.root / "painting/x/wallpapers.toml").read_text(encoding="utf-8")
        self.assertIn("year = 1889\n", text)
        self.assertIn('tags = ["a", "b"]\n', text)
        keys = [ln.split(" =")[0] for ln in text.splitlines() if " = " in ln]
        self.assertEqual(keys.index("tags"), keys.index("year") + 1)

    def test_bad_year_and_tag_shapes_are_structural(self):
        for extra in ('year = "1889"\n', "year = [1, 2, 3]\n", 'year = ["a", "b"]\n', "tags = [1]\n", 'tags = "x"\n'):
            self.toml(extra)
            codes = {i.code for i in self.cat.structural_issues("painting/x")}
            self.assertIn("field-type", codes, extra)


class RenderTest(unittest.TestCase):
    """render() / blob_entries(): the generated index wallpapers.toml (publish model)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "painting/impressionism").mkdir(parents=True)
        self.path = self.tmp / "painting/impressionism/wallpapers.toml"
        self.path.write_text(SAMPLE, encoding="utf-8")
        from tests.fakes import FakeStore
        self.cat = TomlCatalogue(self.tmp, FakeStore(root=self.tmp))

    def test_render_of_a_loaded_collection_is_what_save_writes(self):
        coll = self.cat.load("painting/impressionism")
        rendered = self.cat.render(coll)
        self.cat.save(coll)
        self.assertEqual(rendered, self.path.read_bytes())

    def test_render_of_a_subset_keeps_the_header_and_counts_the_subset(self):
        coll = self.cat.load("painting/impressionism")
        coll.wallpapers = coll.wallpapers[1:]
        text = self.cat.render(coll).decode()
        self.assertTrue(text.startswith(HEADER))
        self.assertEqual(tomllib.loads(text)["collection"]["count"], 1)
        self.assertNotIn("a_b.jpg", text)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SAMPLE)  # nothing written

    def test_blob_entries_maps_each_file_to_its_table_text(self):
        coll = self.cat.load("painting/impressionism")
        entries = self.cat.blob_entries(SAMPLE.encode())
        self.assertEqual(list(entries), ["a_b.jpg", "c_d.jpg"])
        for w in coll.wallpapers:
            self.assertEqual(entries[w.file], self.cat.entry_text(w))

    def test_blob_entries_reads_non_canonical_tables(self):
        data = b'[collection]\n[[wallpaper]]\nresolution = [1, 2]\nfile = \'lit "x".jpg\'  # c\n\n[[wallpaper]]\n' \
               b'file = "esc \\"q\\".jpg"\n[[wallpaper]]\ntitle = "no file"\n'
        self.assertEqual(sorted(self.cat.blob_entries(data)), ['esc "q".jpg', 'lit "x".jpg'])
        self.assertEqual(self.cat.blob_entries(b""), {})
