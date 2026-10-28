"""API v2 (spec 3.2): paged list, tags, years, folders, git endpoints, delete plan, create/patch additions."""
import os
import unittest

from tests.cli.test_cli import git, make_image
from tests.services import fixture
from tests.web.test_api import FILE, FOLDER, REF, ApiBase
from wallman.web.thumb_key import thumb_key

GALLERY = {"folder", "file", "title", "artist", "year", "ratio", "git", "shareable", "issues", "thumb", "external"}


class ApiV2Test(ApiBase):
    def add_folder(self, folder, entries, medium="painting", movement="x"):
        fixture.write_collection(self.images, folder, medium, movement, [text for _, text in entries])
        for file, _ in entries:
            make_image(self.images / folder / file)
        self.app.index.refresh({folder})

    def entry(self, file, title, artist="A B", license="public-domain", year=1900):
        return file, fixture.entry(file, title, artist, license, year, "https://example.org/s")

    def toml(self):
        return (self.images / FOLDER / "wallpapers.toml").read_bytes()

    def many(self):
        self.add_folder("poster/pop", [self.entry("a.jpg", "Alpha", year=1950), self.entry("b.jpg", "Beta", year=1960),
                                       self.entry("c.jpg", "Gamma", license="all-rights-reserved", year=1970)], "poster", "pop")

    # -- list ------------------------------------------------------------------------------
    def test_list_item_shape_and_paging(self):
        self.many()
        status, page = self.api("GET", "/api/wallpapers?limit=2")
        self.assertEqual((status, page["total"], page["offset"], len(page["items"])), (200, 4, 0, 2))
        item = page["items"][0]
        self.assertEqual(set(item), GALLERY)
        self.assertEqual((item["title"], item["year"], item["ratio"], item["git"], item["external"], item["issues"]),
                         ("Alpha", 1950, "16:9", "untracked", False, 0))
        _, page2 = self.api("GET", "/api/wallpapers?limit=2&offset=2")
        self.assertEqual((page2["offset"], len(page2["items"])), (2, 2))
        self.assertEqual(self.api("GET", "/api/wallpapers?offset=99")[1]["items"], [])

    def test_list_filters_and_sort(self):
        self.many()
        _, p = self.api("GET", "/api/wallpapers?folder=poster")
        self.assertEqual([i["title"] for i in p["items"]], ["Alpha", "Beta", "Gamma"])
        _, p = self.api("GET", "/api/wallpapers?license=all-rights-reserved")
        self.assertEqual([i["title"] for i in p["items"]], ["Gamma"])
        self.assertFalse(p["items"][0]["shareable"])
        _, p = self.api("GET", "/api/wallpapers?ratio=16:9&sort=year")
        self.assertEqual([i["year"] for i in p["items"]], [1906, 1950, 1960, 1970])
        _, p = self.api("GET", "/api/wallpapers?ratio=square")
        self.assertEqual(p["total"], 0)
        _, p = self.api("GET", "/api/wallpapers?year=1950s")
        self.assertEqual([i["title"] for i in p["items"]], ["Alpha"])
        _, p = self.api("GET", "/api/wallpapers?q=beta")
        self.assertEqual(p["total"], 1)
        _, p = self.api("GET", "/api/wallpapers?issues=1")
        self.assertEqual([i["title"] for i in p["items"]], ["Gamma"])  # no download URL for a non-shareable license
        self.assertGreater(p["items"][0]["issues"], 0)

    def test_list_tag_repeatable_all_must_match(self):
        self.many()
        self.api("PATCH", "/api/wallpapers/poster/pop/a.jpg", {"tags": ["sea", "blue"]})
        self.api("PATCH", "/api/wallpapers/poster/pop/b.jpg", {"tags": ["sea"]})
        _, p = self.api("GET", "/api/wallpapers?tag=sea")
        self.assertEqual(p["total"], 2)
        _, p = self.api("GET", "/api/wallpapers?tag=sea&tag=blue")
        self.assertEqual([i["title"] for i in p["items"]], ["Alpha"])

    def test_list_tags_and_semantics_slugged(self):
        self.many()
        self.api("PATCH", "/api/wallpapers/poster/pop/a.jpg", {"tags": ["night-sky", "blue"]})
        self.api("PATCH", "/api/wallpapers/poster/pop/b.jpg", {"tags": ["night-sky"]})
        _, p = self.api("GET", "/api/wallpapers?tag=Night%20Sky&tag=BLUE")
        self.assertEqual([i["title"] for i in p["items"]], ["Alpha"])
        _, p = self.api("GET", "/api/wallpapers?tag=night-sky&tag=night-sky")
        self.assertEqual(p["total"], 2)
        _, p = self.api("GET", "/api/wallpapers?tag=night-sky&tag=unknown")
        self.assertEqual(p["total"], 0)

    def test_list_bad_params_400(self):
        for q in ("year=nonsense", "sort=bogus", "offset=-1", "offset=x", "limit=0", "limit=501", "limit=x",
                  "issues=2", "ratio=bogus"):
            status, body = self.api("GET", f"/api/wallpapers?{q}")
            self.assertEqual((status, body["error"]), (400, "BadRequest"), q)
        self.assertEqual(self.api("GET", "/api/wallpapers?limit=500")[0], 200)

    def test_thumb_key_matches_helper(self):
        _, p = self.api("GET", "/api/wallpapers")
        path = self.images / REF
        self.assertEqual(p["items"][0]["thumb"], thumb_key(path, path.stat()))
        self.assertEqual(len(p["items"][0]["thumb"]), 16)
        os.utime(path, ns=(5, 5))
        self.app.index.refresh({FOLDER})
        _, p2 = self.api("GET", "/api/wallpapers")
        self.assertNotEqual(p2["items"][0]["thumb"], p["items"][0]["thumb"])

    # -- full entry --------------------------------------------------------------------------
    def test_get_full_entry(self):
        _, patched = self.api("PATCH", f"/api/wallpapers/{REF}", {"tags": ["Sea"], "year": [1900, 1905]})
        _, one = self.api("GET", f"/api/wallpapers/{patched['folder']}/{patched['file']}")
        self.assertEqual((one["tags"], one["year"], one["git"]), (["sea"], [1900, 1905], "staged"))
        self.assertEqual(one["issues"], [])
        self.assertIn("license", one)
        self.assertIn("resolution", one)
        self.assertFalse(one["external"])

    def test_tag_filter_is_slugged_like_stored_tags(self):
        self.api("PATCH", f"/api/wallpapers/{REF}", {"tags": ["Night Sky"]})
        _, p = self.api("GET", "/api/wallpapers?tag=Night%20Sky")
        self.assertEqual(p["total"], 1)
        _, p = self.api("GET", "/api/wallpapers?tag=%20%21%20")  # nothing letter-like left: no filter
        self.assertEqual(p["total"], 1)

    # -- tags / years / folders -------------------------------------------------------------------
    def test_tags(self):
        self.many()
        self.api("PATCH", "/api/wallpapers/poster/pop/a.jpg", {"tags": ["sea", "blue"]})
        self.api("PATCH", "/api/wallpapers/poster/pop/b.jpg", {"tags": ["sea", "sky"]})
        _, tags = self.api("GET", "/api/tags")
        self.assertEqual(tags, [{"tag": "sea", "count": 2}, {"tag": "blue", "count": 1}, {"tag": "sky", "count": 1}])
        _, tags = self.api("GET", "/api/tags?prefix=s&limit=1")
        self.assertEqual(tags, [{"tag": "sea", "count": 2}])
        for q in ("limit=0", "limit=x", "limit=-3"):
            self.assertEqual(self.api("GET", f"/api/tags?{q}")[0], 400, q)

    def test_tags_without_prefix_lists_all_up_to_500(self):
        many = [(f"t{i:03d}", 1) for i in range(600)]
        self.app.index.tags = lambda prefix="": [tn for tn in many if tn[0].startswith(prefix)]
        _, tags = self.api("GET", "/api/tags")
        self.assertEqual(len(tags), 500)
        _, tags = self.api("GET", "/api/tags?prefix=t")
        self.assertEqual(len(tags), 50)  # suggestions as you type stay short
        _, tags = self.api("GET", "/api/tags?limit=3")
        self.assertEqual(len(tags), 3)

    def test_years_suggest(self):
        status, out = self.api("GET", "/api/years/suggest?q=188")
        self.assertEqual((status, out[0]), (200, "1880s"))
        self.assertEqual(self.api("GET", "/api/years/suggest")[1], [])
        self.assertEqual(self.api("GET", "/api/years/suggest?q=abc")[1], [])

    def test_folders(self):
        self.many()
        _, rows = self.api("GET", "/api/folders")
        self.assertEqual(rows, [{"folder": FOLDER, "medium": "painting", "movement": "impressionism", "external": False},
                                {"folder": "poster/pop", "medium": "poster", "movement": "pop", "external": False}])

    # -- patch -----------------------------------------------------------------------------------
    def test_patch_year_text_and_validation(self):
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"year": "1880s"})
        self.assertEqual((status, body["year"]), (200, [1880, 1889]))
        ref = f"{body['folder']}/{body['file']}"
        status, body = self.api("PATCH", f"/api/wallpapers/{ref}", {"year": "c. 1900"})
        self.assertEqual((status, body["year"]), (200, [1895, 1905]))
        ref = f"{body['folder']}/{body['file']}"
        before = self.toml()
        for bad in ("nonsense", "", "1880-1870", "3000", [1, 2, 3], [1900, "x"], 1399, True, {"a": 1}):
            status, body = self.api("PATCH", f"/api/wallpapers/{ref}", {"year": bad})
            self.assertEqual((status, body["error"]), (400, "BadRequest"), bad)
        for bad in ("sea", [1], [True], 5):
            status, body = self.api("PATCH", f"/api/wallpapers/{ref}", {"tags": bad})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.toml(), before)

    def test_patch_year_null_clears(self):
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"year": None})
        self.assertEqual((status, body["year"]), (200, None))

    # -- git endpoints ---------------------------------------------------------------------------
    def refs(self, *files, folder="poster/pop"):
        return [{"folder": folder, "file": f} for f in files]

    def test_git_add_and_remove(self):
        self.many()
        status, body = self.api("POST", "/api/git/add", {"refs": self.refs("a.jpg", "b.jpg")})
        self.assertEqual((status, body), (200, {"refs": self.refs("a.jpg", "b.jpg")}))
        _, p = self.api("GET", "/api/wallpapers?folder=poster")
        self.assertEqual([i["git"] for i in p["items"]], ["staged", "staged", "untracked"])
        status, body = self.api("POST", "/api/git/remove", {"refs": self.refs("a.jpg", "c.jpg")})
        self.assertEqual((status, body), (200, {"refs": self.refs("a.jpg")}))
        _, p = self.api("GET", "/api/wallpapers?folder=poster")
        self.assertEqual([i["git"] for i in p["items"]], ["untracked", "staged", "untracked"])
        self.assertTrue((self.images / "poster/pop/a.jpg").exists())

    def test_patch_response_and_next_list_show_the_git_state_after_the_operation(self):
        """The git status after an edit runs in the background; the response waits for it, so neither the response
        nor the list right after it shows the state from before the rename."""
        self.api("GET", "/api/wallpapers")  # warm (setUp committed everything)
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"title": "Nympheas"})
        self.assertEqual((status, body["file"], body["git"]), (200, "claude-monet_nympheas_1906.jpg", "staged"))
        _, page = self.api("GET", f"/api/wallpapers?folder={FOLDER}")
        self.assertEqual([(i["file"], i["git"]) for i in page["items"]], [("claude-monet_nympheas_1906.jpg", "staged")])

    def test_slow_git_after_an_operation_answers_pending_and_reads_never_wait(self):
        from unittest import mock
        self.api("GET", "/api/wallpapers")
        with mock.patch.object(self.app.index, "wait_for_operation_git", return_value=False), \
                mock.patch("sys.stderr"):
            status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"title": "Slow"})
        self.assertEqual((status, body["git"]), (200, "pending"))
        with mock.patch.object(self.app.index, "wait_for_operation_git", side_effect=AssertionError("waited")):
            status, body = self.api("GET", f"/api/wallpapers/{FOLDER}/{body['file']}")
        self.assertEqual(status, 200)
        self.assertNotEqual(body["git"], "pending")

    def test_status_counts_come_from_the_index_unless_full(self):
        from unittest import mock
        (self.images / FOLDER / FILE).unlink()
        self.add_folder("poster/pop", [self.entry("a.jpg", "Alpha"), self.entry("bad_name.JPG", "Bad")], "poster", "pop")
        _, full = self.api("GET", "/api/status?full=1")
        with mock.patch.object(self.app.validator, "run", side_effect=AssertionError("validator ran")):
            status, quick = self.api("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual((quick["errors"], quick["warnings"]), (full["errors"], full["warnings"]))
        self.assertGreaterEqual(quick["errors"], 2)
        (self.images / "poster/pop/a.jpg").unlink()  # outside wallman, just now: no freshness window for status
        _, after = self.api("GET", "/api/status")
        self.assertEqual(after["errors"], quick["errors"] + 1)
        status, body = self.api("GET", "/api/status?full=2")
        self.assertEqual((status, body["error"]), (400, "BadRequest"))

    def test_git_add_not_shareable_422(self):
        self.many()
        status, body = self.api("POST", "/api/git/add", {"refs": self.refs("a.jpg", "c.jpg")})
        self.assertEqual((status, body["error"]), (422, "NotShareable"))
        self.assertIn("poster/pop/c.jpg", body["message"])
        _, p = self.api("GET", "/api/wallpapers?folder=poster")
        self.assertEqual([i["git"] for i in p["items"]], ["untracked"] * 3)

    def test_git_add_ignored_path_422(self):
        self.many()
        (self.root / ".gitignore").write_text("images/poster/pop/a.jpg\n")
        status, body = self.api("POST", "/api/git/add", {"refs": self.refs("a.jpg")})
        self.assertEqual(status, 422, body)
        self.assertIn(".gitignore", body["message"])

    def test_git_bad_bodies_400(self):
        for path in ("/api/git/add", "/api/git/remove", "/api/wallpapers/delete-plan"):
            for bad in ({}, {"refs": "x"}, {"refs": [1]}, {"refs": [{"folder": "a"}]}, {"refs": [{"folder": 1, "file": "x"}]},
                        {"refs": None}):
                status, body = self.api("POST", path, bad)
                self.assertEqual((status, body["error"]), (400, "BadRequest"), (path, bad))
        status, body = self.api("POST", "/api/git/add", {"refs": [{"folder": "../..", "file": "x"}]})
        self.assertEqual((status, body["error"]), (400, "PathOutsideRepo"))
        status, body = self.api("POST", "/api/git/add", {"refs": self.refs("nope.jpg")})
        self.assertEqual(status, 404)

    # -- delete plan / delete ----------------------------------------------------------------------
    def test_delete_plan(self):
        self.many()
        refs = self.refs("a.jpg") + [{"folder": FOLDER, "file": FILE}]
        status, body = self.api("POST", "/api/wallpapers/delete-plan", {"refs": refs})
        self.assertEqual((status, body), (200, {"tracked": [refs[1]], "untracked": [refs[0]], "external": []}))

    def test_delete_git_keep_vs_remove(self):
        self.many()
        status, body = self.api("DELETE", "/api/wallpapers", {"refs": [{"folder": FOLDER, "file": FILE}], "git": "keep"})
        self.assertEqual((status, body), (200, {"deleted": 1}))
        self.assertEqual(git(self.root, "status", "--porcelain", "--", f"images/{REF}").strip()[:2].strip(), "D")
        self.assertEqual(git(self.root, "diff", "--cached", "--name-status", "--", f"images/{REF}"), "")
        self.add_folder("painting/cubism", [self.entry("d.jpg", "Delta")], movement="cubism")
        self.api("POST", "/api/git/add", {"refs": self.refs("d.jpg", folder="painting/cubism")})
        status, body = self.api("DELETE", "/api/wallpapers",
                                {"refs": self.refs("d.jpg", folder="painting/cubism"), "git": "remove"})
        self.assertEqual(status, 200)
        self.assertNotIn("d.jpg", git(self.root, "ls-files"))

    def test_delete_bad_git_400(self):
        for bad in ("rm", 1, None, True):
            status, body = self.api("DELETE", "/api/wallpapers", {"refs": [{"folder": FOLDER, "file": FILE}], "git": bad})
            self.assertEqual((status, body["error"]), (400, "BadRequest"), bad)
        self.assertTrue((self.images / REF).exists())

    # -- create ------------------------------------------------------------------------------------
    def test_create_git_suggestion(self):
        _, up = self.upload()
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "upload_id": up["upload_id"]})
        self.assertEqual((status, made["git_suggestion"], made["git"]), (201, None, "staged"))
        _, up = self.upload(color="red")
        meta = {**self.META, "title": "Red Dancers", "upload_id": up["upload_id"], "stage": False}
        status, made = self.api("POST", "/api/wallpapers", meta)
        self.assertEqual((status, made["git_suggestion"], made["git"]), (201, "add", "untracked"))

    def test_create_git_suggestion_survives_a_slow_git(self):
        """M4: the post-add git status timing out ("pending") must not skip the "Add to git?" prompt."""
        from unittest import mock
        _, up = self.upload(color="red")
        meta = {**self.META, "title": "Red Dancers", "upload_id": up["upload_id"], "stage": False}
        import contextlib
        import io
        with mock.patch.object(self.app.index, "wait_for_operation_git", return_value=False), \
                contextlib.redirect_stderr(io.StringIO()):
            status, made = self.api("POST", "/api/wallpapers", meta)
        self.assertEqual((status, made["git"], made["git_suggestion"]), (201, "pending", "add"))

    def test_create_with_tags(self):
        _, up = self.upload()
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "upload_id": up["upload_id"],
                                                            "tags": ["Night Sky", "blue", "blue"], "year": [1880, 1889]})
        self.assertEqual((status, made["tags"], made["year"]), (201, ["blue", "night-sky"], [1880, 1889]))
        _, up = self.upload(color="red")
        status, body = self.api("POST", "/api/wallpapers", {**self.META, "title": "Red", "upload_id": up["upload_id"],
                                                            "tags": "blue"})
        self.assertEqual(status, 400)

    def test_create_suggestion_needs_tracked_sibling_and_shareable(self):
        self.add_folder("poster/solo", [self.entry("s.jpg", "Solo")], "poster", "solo")
        _, up = self.upload()
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "folder": "poster/solo",
                                                            "upload_id": up["upload_id"], "stage": False})
        self.assertEqual((status, made["git_suggestion"]), (201, None))
        _, up = self.upload(color="red")
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "title": "Red", "upload_id": up["upload_id"],
                                                            "stage": "no"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()


class ThemeApiTest(ApiBase):
    def conf(self, text):
        p = self.app.theme.config_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_theme_css_follows_scheme_for_auto(self):
        status, h, data = self.call("GET", "/api/theme?scheme=light")
        self.assertEqual((status, h.get_content_type(), h["Cache-Control"]), (200, "text/css", "no-cache"))
        self.assertIn("color-scheme:light;", data.decode())
        self.assertIn("color-scheme:dark;", self.call("GET", "/api/theme?scheme=dark")[2].decode())
        self.conf('[theme]\npalette = "Nord"\nmode = "dark"\n')
        self.assertIn("color-scheme:dark;", self.call("GET", "/api/theme?scheme=light")[2].decode())

    def test_status_reports_theme_mtime_and_warnings(self):
        _, s = self.api("GET", "/api/status")
        # no config file: follow the live Noctalia palette; none in this isolated HOME, so the built-in + a warning
        self.assertEqual((s["theme_mtime"], len(s["theme_warnings"]), s["git_error"]), (0.0, 1, None))
        self.app.index.git_error = "GitError: index.lock exists"  # shown in the status bar (M3)
        self.assertEqual(self.api("GET", "/api/status")[1]["git_error"], "GitError: index.lock exists")
        self.assertEqual(self.api("GET", "/api/status?only=theme")[1]["git_error"], "GitError: index.lock exists")
        self.conf('[theme]\npalette = "Nope"\n')
        _, s = self.api("GET", "/api/status")
        self.assertGreater(s["theme_mtime"], 0)
        self.assertEqual(len(s["theme_warnings"]), 1)

    def test_status_theme_only_is_light(self):
        """The UI polls ?only=theme every 5 s: no validator run, no git status, just the theme fields."""
        self.conf('[theme]\npalette = "Nord"\n')
        _, s = self.api("GET", "/api/status?only=theme")
        self.assertEqual(set(s), {"theme_mtime", "theme_warnings", "git_error"})
        self.assertGreater(s["theme_mtime"], 0)
        status, body = self.api("GET", "/api/status?only=nope")
        self.assertEqual((status, body["error"]), (400, "BadRequest"))


class IndexInvariantTest(ApiBase):
    """Rulings v2-17 (a rolled-back operation leaves the git index unchanged) and v2-18 (rename and move keep the
    image's tracked state)."""
    ARR = "pixar_up_2009.jpg"

    def setUp(self):
        super().setUp()
        fixture.write_collection(self.images, "poster/pop", "poster", "pop", [
            fixture.entry(self.ARR, "Up", "Pixar", "all-rights-reserved", 2009, "https://example.org/up",
                          'download = "https://example.org/up.jpg"')])
        make_image(self.images / "poster/pop" / self.ARR)
        self.app.index.refresh({"poster/pop"})

    def index(self):
        return (self.root / ".git" / "index").read_bytes()

    def ls(self, *paths):
        return git(self.root, "ls-files", "--", *paths)

    def update(self, folder, file, changes):
        from wallman.domain.models import Ref
        return self.app.library.update(Ref(folder, file), changes)

    def test_failed_relicense_to_shareable_stages_nothing(self):
        from unittest import mock
        before, toml = self.index(), (self.images / "poster/pop/wallpapers.toml").read_bytes()
        with mock.patch.object(self.app.readme, "write", side_effect=OSError("read-only README")):
            with self.assertRaises(OSError):
                self.update("poster/pop", self.ARR, {"license": "CC-BY-4.0"})
        self.assertEqual(self.ls(f"images/poster/pop/{self.ARR}"), "")
        self.assertEqual(self.index(), before)
        self.assertEqual((self.images / "poster/pop/wallpapers.toml").read_bytes(), toml)

    def test_failure_after_the_batched_add_restores_the_index(self):
        from unittest import mock
        before = self.index()
        with mock.patch.object(self.app.index, "refresh", side_effect=RuntimeError("late failure")):
            with self.assertRaises(RuntimeError):
                self.update("poster/pop", self.ARR, {"license": "CC-BY-4.0"})
        self.assertEqual(self.index(), before)
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "")

    def test_failed_rename_of_tracked_image_restores_the_index(self):
        from unittest import mock
        before = self.index()
        with mock.patch.object(self.app.readme, "write", side_effect=OSError("read-only README")):
            with self.assertRaises(OSError):
                self.update(FOLDER, FILE, {"title": "Nympheas"})
        self.assertEqual(self.index(), before)
        self.assertTrue((self.images / REF).is_file())

    def test_rename_after_remove_from_git_stays_untracked(self):
        from wallman.domain.models import Ref
        self.app.git_actions.remove([Ref(FOLDER, FILE)])
        new = self.update(FOLDER, FILE, {"title": "Nympheas"})
        self.assertEqual(self.ls(f"images/{FOLDER}/{new.file}"), "")
        self.assertEqual(self.ls(f"images/{REF}"), "")
        self.assertTrue((self.images / FOLDER / new.file).is_file())

    def test_move_of_tracked_image_is_staged_at_the_new_path(self):
        new = self.update(FOLDER, FILE, {"folder": "painting/symbolism"})
        self.assertEqual(self.ls(f"images/painting/symbolism/{new.file}").strip(), f"images/painting/symbolism/{new.file}")
        self.assertIn(f"images/painting/symbolism/{new.file}", git(self.root, "diff", "--cached", "--name-only"))
        self.assertEqual(self.ls(f"images/{REF}"), "")

    def test_rename_of_ignored_image_stays_ignored(self):
        new = self.update("poster/pop", self.ARR, {"title": "Up Again"})
        self.assertEqual(self.ls(f"images/poster/pop/{new.file}"), "")
