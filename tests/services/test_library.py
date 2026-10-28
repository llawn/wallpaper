import hashlib
import unittest

from tests.services.support import R, ServiceCase, col, wp
from wallman.domain.models import Ref, Resolution
from wallman.errors import DownloadFailed, LicenseNeedsDownload, NameConflict, NotFound
from wallman.services.library import Library

NAME = "claude-monet_water-lilies_1906.jpg"


class LibraryTest(ServiceCase):
    def lib(self):
        return Library(self.cat, self.store, self.vcs, self.uow, self.fetcher)

    def one(self, tracked=True, external=(), license="public-domain", **kw):
        f = "painting/x"
        e = wp(NAME, title="Water Lilies", artist="Claude Monet", year=1906, license=license, **kw)
        self.build([col(f, [e])], {f: {NAME}}, external=external,
                   tracked=[(f, NAME)] if tracked else [])
        if tracked:
            self.publish_all(f)  # a tracked image: its entry is published
        return Ref(f, NAME)

    def test_rename_on_title_change_uses_git_mv(self):
        ref = self.one()
        new = self.lib().update(ref, {"title": "Nympheas"})
        self.assertEqual(new, Ref("painting/x", "claude-monet_nympheas_1906.jpg"))
        self.assertIn(("stage_mv", self.store.path(ref.folder, ref.file), self.store.path(new.folder, new.file)),
                      self.vcs.calls)
        w = self.cat.load("painting/x").wallpapers[0]
        self.assertEqual((w.file, w.title), (new.file, "Nympheas"))
        # git mv already staged the image at its new path: nothing is added, never with -f (Ruling v2-18); the
        # index toml is regenerated with the entry under its new name
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_add"], [("stage_add", [self.toml(new.folder)])])
        self.assertIn(f'file = "{new.file}"', self.vcs.index[self.toml(new.folder)].decode())
        self.assertEqual(self.commits, 1)

    def test_rename_untracked_uses_store_move(self):
        ref = self.one(tracked=False)
        new = self.lib().update(ref, {"year": 1907})
        self.assertEqual(new.file, "claude-monet_water-lilies_1907.jpg")
        self.assertEqual(self.store.calls, [("move", self.store.path(ref.folder, ref.file),
                                             self.store.path(new.folder, new.file))])
        self.assertEqual([c for c in self.vcs.calls if c[0] == "stage_mv"], [])
        img = self.store.path(new.folder, new.file)
        self.assertFalse(any(img in c[1] for c in self.vcs.calls if c[0] == "stage_add"))  # stays untracked (v2-18)
        self.assertEqual(self.store.path(new.folder, new.file).read_bytes(), b"orig:" + NAME.encode())
        self.assertFalse(self.store.path(ref.folder, ref.file).exists())

    def test_rename_collision_gets_suffix_never_overwrites(self):
        f = "painting/x"
        self.build([col(f, [wp(NAME, title="Water Lilies", artist="Claude Monet", year=1906),
                            wp("claude-monet_other_1906.jpg", title="Other", artist="Claude Monet", year=1906)])],
                   {f: {NAME, "claude-monet_other_1906.jpg"}})
        new = self.lib().update(Ref(f, "claude-monet_other_1906.jpg"), {"title": "Water Lilies"})
        self.assertEqual(new.file, "claude-monet_water-lilies_1906-2.jpg")

    def test_no_rename_when_name_unchanged(self):
        ref = self.one()
        new = self.lib().update(ref, {"source": "https://a.example/x"})
        self.assertEqual(new, ref)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.cat.load("painting/x").wallpapers[0].source, "https://a.example/x")

    def test_unknown_key_is_value_error_and_changes_nothing(self):
        ref = self.one()
        with self.assertRaises(ValueError):
            self.lib().update(ref, {"title": "N", "color": "red"})
        self.assertEqual((self.vcs.calls, self.cat.saved, self.commits), ([], [], 0))

    def test_wrong_value_types_are_value_errors(self):
        ref = self.one()
        for bad in ({"year": "x"}, {"year": True}, {"title": 5}, {"source": []}, {"title": ""}):
            with self.assertRaises(ValueError, msg=bad):
                self.lib().update(ref, bad)

    def test_unknown_ref_raises_not_found(self):
        self.one()
        with self.assertRaises(NotFound):
            self.lib().update(Ref("painting/x", "nope.jpg"), {"title": "N"})

    def test_move_between_folders(self):
        ref = self.one()
        self.cat.data["poster/y"] = col("poster/y", [], "poster")
        new = self.lib().update(ref, {"folder": "poster/y"})
        self.assertEqual(new, Ref("poster/y", NAME))
        self.assertNotIn("painting/x", self.cat.data)  # emptied source is dropped
        self.assertEqual([w.file for w in self.cat.load("poster/y").wallpapers], [NAME])
        self.assertIn(("stage_mv", self.store.path("painting/x", NAME), self.store.path("poster/y", NAME)), self.vcs.calls)

    def test_move_to_new_folder_creates_collection_and_keeps_source(self):
        f = "painting/x"
        self.build([col(f, [wp(NAME, title="Water Lilies", artist="Claude Monet", year=1906), wp("other.jpg")])],
                   {f: {NAME, "other.jpg"}}, tracked=[(f, NAME)])
        self.lib().update(Ref(f, NAME), {"folder": "painting/new"})
        self.assertIn("painting/new", self.cat.data)
        self.assertEqual([w.file for w in self.cat.load(f).wallpapers], ["other.jpg"])

    def test_move_name_conflict_raises_and_changes_nothing(self):
        ref = self.one()
        self.cat.data["poster/y"] = col("poster/y", [wp(NAME)], "poster")
        before = self.snapshot()
        with self.assertRaises(NameConflict):
            self.lib().update(ref, {"folder": "poster/y"})
        self.assertEqual((self.snapshot(), self.vcs.calls, self.store.calls), (before, [], []))

    def test_relicense_to_non_shareable_untracks(self):
        ref = self.one(download="https://d/x.jpg", sha256="a" * 64)
        self.lib().update(ref, {"license": "all-rights-reserved", "license_detail": "© X"})
        img = self.store.path(ref.folder, ref.file)
        self.assertIn(("untrack", [img]), self.vcs.calls)
        self.assertNotIn(img, self.vcs.tracked)
        self.assertIn(("stage_add", [self.toml(ref.folder)]), self.vcs.calls)  # still published: entry only
        self.assertEqual(self.cat.load(ref.folder).wallpapers[0].license, "all-rights-reserved")

    def test_relicense_to_non_shareable_needs_download(self):
        ref = self.one()
        before = self.snapshot()
        with self.assertRaises(LicenseNeedsDownload):
            self.lib().update(ref, {"license": "official-wallpaper"})
        self.assertEqual((self.snapshot(), self.vcs.calls, self.cat.saved), (before, [], []))

    def test_relicense_with_download_in_same_change_records_sha(self):
        ref = self.one()
        self.fetcher.responses["https://d/x.jpg"] = b"DL"
        self.lib().update(ref, {"license": "official-wallpaper", "download": "https://d/x.jpg"})
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.download, w.sha256), ("https://d/x.jpg", hashlib.sha256(b"DL").hexdigest()))

    def test_download_failure_changes_nothing(self):
        ref = self.one()
        self.fetcher.responses["https://d/x.jpg"] = DownloadFailed("offline")
        before = self.snapshot()
        with self.assertRaises(DownloadFailed):
            self.lib().update(ref, {"title": "N", "download": "https://d/x.jpg"})
        self.assertEqual((self.snapshot(), self.vcs.calls, self.cat.saved, self.store.calls, self.commits),
                         (before, [], [], [], 0))

    def test_leaving_personal_for_non_shareable_needs_download(self):
        ref = self.one(license="personal")
        with self.assertRaises(LicenseNeedsDownload):
            self.lib().update(ref, {"license": "all-rights-reserved", "license_detail": "© X"})
        self.assertEqual((self.vcs.calls, self.cat.saved), ([], []))

    def test_clearing_download_of_non_shareable_refused(self):
        ref = self.one(license="all-rights-reserved", download="https://d/x.jpg", sha256="a" * 64)
        with self.assertRaises(LicenseNeedsDownload):
            self.lib().update(ref, {"download": None})
        self.assertEqual((self.vcs.calls, self.cat.saved), ([], []))

    def test_relicense_to_shareable_requires_nothing_and_stages(self):
        ref = self.one(tracked=False, license="official-wallpaper", download="https://d/x.jpg", sha256="a" * 64)
        self.publish_all(ref.folder)  # published, metadata only
        self.lib().update(ref, {"license": "CC0-1.0"})
        img = self.store.path(ref.folder, ref.file)
        # one deferred batch at commit, forced: the image may still be in .gitignore (Ruling v2-17)
        self.assertIn(("stage_add", [img, self.toml(ref.folder)], "force"), self.vcs.calls)
        self.assertEqual([c for c in self.vcs.calls if c[0] == "untrack"], [])

    def test_update_in_external_collection_runs_git_in_submodule(self):
        f = "ml4w"
        self.build([col(f, [wp("Some File.jpg", title="Some File", artist="ML4W", license="MIT")], "ml4w")],
                   {f: {"Some File.jpg"}}, external={f}, tracked=[(f, "Some File.jpg")])
        ref = Ref(f, "Some File.jpg")
        new = self.lib().update(ref, {"title": "Renamed", "license": "Apache-2.0"})
        self.assertEqual(new, ref)  # never renamed
        self.assertEqual(self.cat.load(f).wallpapers[0].title, "Renamed")
        self.assertEqual([c for c in self.vcs.calls if c[0] in ("stage_mv", "untrack")], [])
        self.assertEqual(self.vcs.calls, [("stage_add", [self.toml(f)])])  # image untouched, only metadata staged

    def test_move_from_or_to_external_refused(self):
        ref = self.one(external={"painting/x"})
        self.cat.data["poster/y"] = col("poster/y", [], "poster")
        with self.assertRaises(ValueError):
            self.lib().update(ref, {"folder": "poster/y"})

    def test_move_destination_validated(self):
        ref = self.one()
        self.store.external.add("ml4w")
        before = self.snapshot()
        for bad in ("paintng/x", "ml4w/sub", "painting/..", ".."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.lib().update(ref, {"folder": bad})
        self.assertEqual((self.snapshot(), self.vcs.calls), (before, []))

    def test_move_destination_normalised(self):
        ref = self.one()
        new = self.lib().update(ref, {"folder": "painting/new/"})
        self.assertEqual(new.folder, "painting/new")
        self.assertIn("painting/new", self.cat.data)

    def test_empty_optional_strings_become_null(self):
        ref = self.one(source="https://example.org/a", license_detail="x")
        self.lib().update(ref, {"source": "", "license_detail": "", "artist": ""})
        w = self.cat.load(ref.folder).wallpapers[0]
        self.assertEqual((w.source, w.license_detail, w.artist), (None, None, "Unknown"))

    def test_download_must_be_a_web_url(self):
        ref = self.one()
        for url in ("file:///etc/passwd", "ftp://x/a.jpg", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.lib().update(ref, {"download": url})
        self.assertEqual(self.fetcher.calls, [])

    def test_invalid_license_value_error(self):
        ref = self.one()
        with self.assertRaises(ValueError):
            self.lib().update(ref, {"license": "wtfpl"})

    def test_get(self):
        ref = self.one()
        self.assertEqual(self.lib().get(ref).title, "Water Lilies")
        with self.assertRaises(NotFound):
            self.lib().get(Ref("painting/x", "nope.jpg"))


class SearchTest(ServiceCase):
    def setUp(self):
        self.build(
            [col("painting/a", [wp("claude-monet_lilies.jpg", title="Water Lilies", artist="Claude Monet"),
                                wp("manet_bar.jpg", title="Bar", artist="Edouard Manet", license="CC0-1.0"),
                                wp("tiny.jpg", title="Tiny", artist="Zed", resolution=Resolution(800, 600))]),
             col("painting/ab", [wp("zz.jpg", title="Other", artist="Q")]),
             col("poster/p", [wp("poster-file.jpg", title="Film", artist="Studio")], "poster")],
            {"painting/a": {"claude-monet_lilies.jpg", "manet_bar.jpg", "tiny.jpg"}, "painting/ab": {"zz.jpg"},
             "poster/p": {"poster-file.jpg"}})
        self.lib = Library(self.cat, self.store, self.vcs, self.uow, self.fetcher)

    def files(self, **kw):
        return [r.file for r, _, _ in self.lib.list(**kw)]

    def test_search_matches_artist_title_file(self):
        self.assertEqual(self.files(query="MONET"), ["claude-monet_lilies.jpg"])
        self.assertEqual(self.files(query="bar"), ["manet_bar.jpg"])
        self.assertEqual(self.files(query="poster-file"), ["poster-file.jpg"])
        self.assertEqual(self.files(query="water lil"), ["claude-monet_lilies.jpg"])

    def test_folder_filter_is_a_path_prefix(self):
        self.assertEqual(self.files(folder="painting/a"), ["claude-monet_lilies.jpg", "manet_bar.jpg", "tiny.jpg"])
        self.assertEqual(self.files(folder="painting"), self.files(folder="painting/a") + ["zz.jpg"])

    def test_license_filter_exact(self):
        self.assertEqual(self.files(license="CC0-1.0"), ["manet_bar.jpg"])

    def test_with_issues_only_entries_with_issues(self):
        rows = self.lib.list(with_issues=True)
        self.assertEqual([r.file for r, _, _ in rows], ["tiny.jpg"])
        self.assertIn("too-small", {i.code for i in rows[0][2]})

    def test_rows_carry_issues_for_every_entry(self):
        by = {r.file: issues for r, _, issues in self.lib.list()}
        self.assertEqual(by["manet_bar.jpg"], [])
        self.assertTrue(by["tiny.jpg"])


if __name__ == "__main__":
    unittest.main()


class YearTagFilterTest(ServiceCase):
    def lib(self):
        return Library(self.cat, self.store, self.vcs, self.uow, self.fetcher)

    def setUp(self):
        from wallman.domain.years import YearSpan
        self.Y = YearSpan

    def setup_lib(self):
        self.build([col("painting/x", [
            wp("a.jpg", title="A", year=self.Y(1880, 1889), tags=("sea", "blue")),
            wp("b.jpg", title="B", year=self.Y(1900, 1900), tags=("sea",)),
            wp("c.jpg", title="C", resolution=Resolution(1920, 1200)),
        ])], files={"painting/x": ["a.jpg", "b.jpg", "c.jpg"]})

    def names(self, **kw):
        return [r.file for r, _, _ in self.lib().list(**kw)]

    def test_filters(self):
        self.setup_lib()
        self.assertEqual(self.names(year=self.Y(1885, 1895)), ["a.jpg"])
        self.assertEqual(self.names(year=self.Y(1889, 1900)), ["a.jpg", "b.jpg"])
        self.assertEqual(self.names(tags=("sea",)), ["a.jpg", "b.jpg"])
        self.assertEqual(self.names(tags=("sea", "blue")), ["a.jpg"])
        self.assertEqual(self.names(ratio="16:10"), ["c.jpg"])
        self.assertEqual(self.names(query="blue"), ["a.jpg"])  # search matches tags

    def test_update_year_and_tags(self):
        self.setup_lib()
        ref = Ref("painting/x", "b.jpg")
        new = self.lib().update(ref, {"year": [1880, 1885], "tags": ["Sea", "Calm Water", "sea", "!!"]})
        w = self.lib().get(new)
        self.assertEqual((w.year, w.tags), (self.Y(1880, 1885), ("calm-water", "sea")))
        self.assertEqual(new.file, "a_b_1880.jpg")  # a span names the file by its start year
        cleared = self.lib().update(new, {"year": None, "tags": []})
        w = self.lib().get(cleared)
        self.assertEqual((w.year, w.tags), (None, ()))

    def test_year_invalid_forms_reported(self):
        self.setup_lib()
        ref = Ref("painting/x", "b.jpg")
        for bad in ("1889", [1890, 1880], [1200, 1300], [1880], [1, 2, 3], [True, 1900], 1200, 2200, True):
            with self.assertRaises(ValueError, msg=bad):
                self.lib().update(ref, {"year": bad})
        for bad in ("sea", [1], [["a"]]):
            with self.assertRaises(ValueError, msg=bad):
                self.lib().update(ref, {"tags": bad})
