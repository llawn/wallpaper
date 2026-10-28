"""LibraryIndex: in-memory index, paged queries, incremental refresh, freshness, thread safety (spec v2 3.1)."""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

from tests.fakes import FakeFetcher, FakeVcs
from tests.services import fixture
from wallman.adapters.fs_store import FsStore
from wallman.adapters.image_header import read_size
from wallman.adapters.toml_catalogue import TomlCatalogue
from wallman.domain.models import Collection, Ref, Resolution, Wallpaper
from wallman.domain.years import YearSpan
from wallman.errors import InvalidImage
from wallman.services.library import Library
from wallman.services.library_index import IndexedEntry, LibraryIndex, Page, Query
from wallman.services.unit_of_work import UnitOfWork

BIG = Resolution(2560, 1440)


def png_bytes(w: int, h: int) -> bytes:
    """A real PNG of w x h (one grey row repeated); small for any size the tests use."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_header(w: int, h: int) -> bytes:
    """Just signature + IHDR: enough for the header reader, tiny on disk at any declared size."""
    data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + data
            + struct.pack(">I", zlib.crc32(b"IHDR" + data)))


class HeaderTool:
    """ImageTool.identify from file headers only (what Magick does for JPEG/PNG, without the binary)."""

    def __init__(self) -> None:
        self.calls = 0

    def identify(self, path: Path) -> Resolution:
        self.calls += 1
        size = read_size(path)
        if size is None:
            raise InvalidImage(f"{path}: not an image")
        return size


class CountingCatalogue(TomlCatalogue):
    def __init__(self, root, store) -> None:
        super().__init__(root, store)
        self.loads: list[str] = []

    def load(self, folder: str) -> Collection:
        self.loads.append(folder)
        return super().load(folder)


class CountingVcs(FakeVcs):
    def __init__(self) -> None:
        super().__init__()
        self.state_calls = 0

    def file_states(self, paths):
        self.state_calls += 1
        return super().file_states(paths)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wp(file, title="T", artist="A", license="public-domain", resolution=BIG, **kw):
    return Wallpaper(file=file, title=title, artist=artist, license=license, resolution=resolution, **kw)


class IndexCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.images = Path(tmp.name).resolve() / "images"
        self.images.mkdir()
        self.store = FsStore(self.images)
        self.cat = CountingCatalogue(self.images, self.store)
        self.tool = HeaderTool()
        self.vcs = CountingVcs()
        self.clock = Clock()

    def collection(self, folder, wallpapers, medium=None, write=True, sizes=None):
        """Write wallpapers.toml (through the real catalogue) and a PNG header per entry at its resolution."""
        medium = medium or folder.split("/")[0]
        c = Collection(folder=folder, medium=medium, movement=folder.split("/")[-1], wallpapers=list(wallpapers))
        (self.images / folder).mkdir(parents=True, exist_ok=True)
        self.cat.save(c)
        if write:
            for w in wallpapers:
                r = (sizes or {}).get(w.file, w.resolution)
                (self.images / folder / w.file).write_bytes(png_header(r.width, r.height))
        return c

    def index(self) -> LibraryIndex:
        idx = LibraryIndex(self.cat, self.store, self.tool, self.vcs, clock=self.clock)
        idx.warm()
        return idx

    @staticmethod
    def files(page: Page) -> list[str]:
        return [e.ref.file for e in page.items]


class QueryTest(IndexCase):
    def setUp(self):
        super().setUp()
        self.collection("painting/a", [
            wp("claude-monet_water-lilies.jpg", "Water Lilies", "Claude Monet", year=YearSpan(1906, 1906),
               tags=("flowers", "water")),
            wp("edouard-manet_bar.jpg", "Bar", "Edouard Manet", license="CC0-1.0", year=YearSpan(1880, 1889),
               tags=("water",)),
            wp("zed_tiny.jpg", "Tiny", "Zed", resolution=Resolution(800, 600)),
        ])
        self.collection("painting/ab", [wp("q_other.jpg", "other", "Q", resolution=Resolution(1920, 1200),
                                           tags=("water",))])
        self.collection("poster/p", [wp("studio_film_2001.jpg", "Film", "Studio", year=YearSpan(2001, 2001))])
        self.vcs.states[self.images / "painting/a/edouard-manet_bar.jpg"] = "staged"
        self.idx = self.index()

    def q(self, **kw) -> Page:
        return self.idx.query(Query(**kw))

    def test_defaults_list_everything_sorted_by_name(self):
        page = self.q()
        self.assertEqual(self.files(page), ["edouard-manet_bar.jpg", "studio_film_2001.jpg", "q_other.jpg",
                                            "zed_tiny.jpg", "claude-monet_water-lilies.jpg"])
        self.assertEqual((page.total, page.offset), (5, 0))
        self.assertIsInstance(page.items[0], IndexedEntry)

    def test_entry_fields_are_precomputed(self):
        e = {x.ref.file: x for x in self.q().items}
        bar = e["edouard-manet_bar.jpg"]
        self.assertEqual(bar.ref, Ref("painting/a", "edouard-manet_bar.jpg"))
        self.assertEqual(bar.ratio_bucket, "16:9")
        self.assertEqual(bar.year_span, YearSpan(1880, 1889))
        self.assertEqual(bar.git_state, "staged")
        self.assertEqual(e["q_other.jpg"].git_state, "untracked")
        self.assertIn("edouard manet", bar.search_text)
        self.assertIn("water", bar.search_text)
        self.assertEqual(bar.issues, ())
        self.assertIn("too-small", {i.code for i in e["zed_tiny.jpg"].issues})
        st = os.stat(self.images / "painting/a/edouard-manet_bar.jpg")
        self.assertEqual(bar.mtime_ns, st.st_mtime_ns)

    def test_folder_filter_is_a_path_prefix(self):
        self.assertEqual(sorted(self.files(self.q(folder="painting/a"))),
                         ["claude-monet_water-lilies.jpg", "edouard-manet_bar.jpg", "zed_tiny.jpg"])
        self.assertEqual(self.q(folder="painting").total, 4)
        self.assertEqual(self.q(folder="paint").total, 0)

    def test_search_every_term_must_match(self):
        self.assertEqual(self.files(self.q(q="MONET lilies")), ["claude-monet_water-lilies.jpg"])
        self.assertEqual(self.files(self.q(q="water")), ["edouard-manet_bar.jpg", "q_other.jpg",
                                                         "claude-monet_water-lilies.jpg"])  # tags count
        self.assertEqual(self.q(q="monet bar").total, 0)
        self.assertEqual(self.files(self.q(q="studio_film")), ["studio_film_2001.jpg"])

    def test_tag_license_ratio_year_issue_filters(self):
        self.assertEqual(self.files(self.q(tags=("water", "flowers"))), ["claude-monet_water-lilies.jpg"])
        self.assertEqual(self.files(self.q(license="CC0-1.0")), ["edouard-manet_bar.jpg"])
        self.assertEqual(self.files(self.q(ratio="16:10")), ["q_other.jpg"])
        self.assertEqual(self.files(self.q(year=YearSpan(1885, 1910))),
                         ["edouard-manet_bar.jpg", "claude-monet_water-lilies.jpg"])
        self.assertEqual(self.files(self.q(issues=True)), ["zed_tiny.jpg"])

    def test_several_tags_intersect_and_follow_edits(self):
        self.assertEqual(self.q(tags=("water", "nope")).total, 0)
        self.assertEqual(self.q(tags=("nope",)).total, 0)
        self.assertEqual(self.files(self.q(tags=("flowers", "water"), sort="year")), ["claude-monet_water-lilies.jpg"])
        self.collection("poster/p", [wp("studio_film_2001.jpg", "Film", "Studio", year=YearSpan(2001, 2001),
                                        tags=("flowers", "water"))])
        self.idx.refresh({"poster/p"})
        self.assertEqual(self.files(self.q(tags=("water", "flowers"))),
                         ["studio_film_2001.jpg", "claude-monet_water-lilies.jpg"])

    def test_sort_year_and_added(self):
        self.assertEqual(self.files(self.q(sort="year")),
                         ["edouard-manet_bar.jpg", "claude-monet_water-lilies.jpg", "studio_film_2001.jpg",
                          "q_other.jpg", "zed_tiny.jpg"])  # no year last, then by title
        os.utime(self.images / "painting/ab/q_other.jpg", ns=(5, 5 * 10**18))
        os.utime(self.images / "poster/p/studio_film_2001.jpg", ns=(5, 4 * 10**18))
        self.idx.refresh({"painting/ab", "poster/p"})
        self.assertEqual(self.files(self.q(sort="added"))[:2], ["q_other.jpg", "studio_film_2001.jpg"])

    def test_paging(self):
        all_files = self.files(self.q())
        page = self.q(offset=1, limit=2)
        self.assertEqual(self.files(page), all_files[1:3])
        self.assertEqual((page.total, page.offset), (5, 1))
        self.assertEqual(self.q(offset=10).items, [])
        self.assertEqual(self.q(offset=10).total, 5)

    def test_items_list_is_a_new_list(self):
        a = self.q()
        a.items.clear()
        self.assertEqual(self.q().total, 5)
        self.assertEqual(len(self.q().items), 5)

    def test_tags_most_used_first_and_folders(self):
        self.assertEqual(self.idx.tags(""), [("water", 3), ("flowers", 1)])
        self.assertEqual(self.idx.tags("fl"), [("flowers", 1)])
        self.assertEqual(self.idx.folders(), ["painting/a", "painting/ab", "poster/p"])

    def test_unparsable_toml_does_not_break_the_index(self):
        (self.images / "poster/p/wallpapers.toml").write_text("[[[ not toml")
        self.idx.refresh({"poster/p"})
        self.assertEqual(self.q().total, 4)
        self.assertIn("poster/p", self.idx.folders())
        self.assertIn("invalid-toml", {i.code for i in self.idx.folder_issues("poster/p")})


class FreshnessTest(IndexCase):
    def setUp(self):
        super().setUp()
        self.collection("painting/a", [wp("a_one.jpg", "One"), wp("a_two.jpg", "Two")])
        self.collection("painting/b", [wp("b_one.jpg", "Bee")])
        self.idx = self.index()

    def titles(self):
        return sorted(e.wallpaper.title for e in self.idx.query(Query()).items)

    def edit_title(self, folder, old, new):
        p = self.images / folder / "wallpapers.toml"
        p.write_text(p.read_text().replace(f'title = "{old}"', f'title = "{new}"'))

    def test_external_toml_edit_is_picked_up(self):
        self.edit_title("painting/a", "One", "Changed in an editor")
        self.clock.now += 1.0
        self.assertEqual(self.titles(), ["Bee", "One", "Two"])  # inside the 2 s window: cached
        self.clock.now += 1.0
        self.assertEqual(self.titles(), ["Bee", "Changed in an editor", "Two"])
        self.assertEqual(self.idx.query(Query(q="editor")).total, 1)

    def test_deleted_file_disappears(self):
        # a file manager removes one image: its entry stays listed (the toml still has it) but flagged missing
        (self.images / "painting/a/a_two.jpg").unlink()
        # and a whole collection folder
        for p in (self.images / "painting/b").iterdir():
            p.unlink()
        (self.images / "painting/b").rmdir()
        self.clock.now += 2.0
        entries = {e.ref.file: e for e in self.idx.query(Query()).items}
        self.assertEqual(sorted(entries), ["a_one.jpg", "a_two.jpg"])
        self.assertIn("missing", {i.code for i in entries["a_two.jpg"].issues})
        self.assertEqual(self.idx.folders(), ["painting/a"])
        self.assertEqual(self.idx.query(Query(folder="painting/b")).total, 0)
        # the owner then drops the entry from the toml by hand: gone for good, never resurrected
        p = self.images / "painting/a/wallpapers.toml"
        c = self.cat.load("painting/a")
        c.wallpapers = [w for w in c.wallpapers if w.file != "a_two.jpg"]
        self.cat.save(c)
        self.clock.now += 2.0
        self.assertEqual(self.titles(), ["One"])
        self.clock.now += 2.0
        self.idx.refresh(None)
        self.assertEqual(self.titles(), ["One"])
        self.assertTrue(p.exists())

    def test_new_folder_and_new_image_appear_after_window(self):
        self.collection("painting/c", [wp("c_one.jpg", "Sea")])
        (self.images / "painting/a/stray.jpg").write_bytes(png_header(2560, 1440))
        self.clock.now += 2.0
        self.assertEqual(self.titles(), ["Bee", "One", "Sea", "Two"])
        self.assertIn("unlisted", {i.code for i in self.idx.folder_issues("painting/a")})

    def test_unchanged_folders_are_not_reloaded(self):
        self.cat.loads.clear()
        self.clock.now += 5.0
        self.idx.ensure_fresh()
        self.assertEqual(self.cat.loads, [])
        self.edit_title("painting/b", "Bee", "Wasp")
        self.clock.now += 5.0
        self.idx.ensure_fresh()
        self.assertEqual(self.cat.loads, ["painting/b"])

    def test_refresh_only_touched_folders(self):
        self.cat.loads.clear()
        self.vcs.state_calls = 0
        self.edit_title("painting/a", "One", "Uno")
        self.idx.refresh({"painting/a"})
        self.assertEqual(self.cat.loads, ["painting/a"])
        self.assertTrue(self.idx.wait_for_operation_git())
        self.assertEqual(self.vcs.state_calls, 1)  # git states always refreshed right after an operation
        self.assertEqual(self.titles(), ["Bee", "Two", "Uno"])  # no stat window needed

    def test_refresh_of_unknown_or_vanished_folder(self):
        self.collection("painting/new", [wp("n_one.jpg", "New")])
        self.idx.refresh({"painting/new", "not/a/collection"})
        self.assertIn("painting/new", self.idx.folders())
        (self.images / "painting/new/wallpapers.toml").unlink()
        self.idx.refresh({"painting/new"})
        self.assertNotIn("painting/new", self.idx.folders())
        self.assertNotIn("New", self.titles())

    def test_git_status_at_most_once_per_second(self):
        self.vcs.state_calls = 0
        for _ in range(5):
            self.clock.now += 0.3
            self.idx.ensure_fresh()
            self.idx.wait_for_git()
        self.assertEqual(self.vcs.state_calls, 1)  # at t+0.3 .. t+1.5 -> one refresh when 1 s had passed
        self.vcs.states[self.images / "painting/a/a_one.jpg"] = "modified"
        self.clock.now += 1.0
        self.idx.ensure_fresh()  # starts git status in the background: the request does not wait for it
        self.idx.wait_for_git()
        self.assertEqual(self.idx.query(Query(q="one", folder="painting/a")).items[0].git_state, "modified")

    def test_background_git_answer_older_than_an_operation_is_dropped(self):
        gate, entered = threading.Event(), threading.Event()
        vcs = self.vcs
        real = vcs.file_states

        def slow(paths):
            entered.set()
            gate.wait(10)
            return {p: "untracked" for p in paths}  # what git said before the operation below

        a_one = self.images / "painting/a/a_one.jpg"
        vcs.file_states = slow
        self.clock.now += 1.0
        self.idx.ensure_fresh()
        self.assertTrue(entered.wait(10))
        vcs.file_states = real
        vcs.states[a_one] = "staged"
        self.idx.refresh({"painting/a"})  # the operation's own (newer) git status
        gate.set()
        self.idx.wait_for_git()
        e = self.idx.query(Query(q="a_one")).items[0]
        self.assertEqual(e.git_state, "staged")

    def test_unchanged_files_are_not_identified_again(self):
        self.tool.calls = 0
        self.idx.refresh({"painting/a"})
        self.assertEqual(self.tool.calls, 0)
        (self.images / "painting/a/a_one.jpg").write_bytes(png_header(1920, 1080))
        os.utime(self.images / "painting/a/a_one.jpg", ns=(1, 1))
        self.idx.refresh({"painting/a"})
        self.assertEqual(self.tool.calls, 1)
        e = self.idx.query(Query(q="a_one")).items[0]
        self.assertIn("resolution-mismatch", {i.code for i in e.issues})

    def test_query_warms_on_first_use(self):
        idx = LibraryIndex(self.cat, self.store, self.tool, self.vcs, clock=self.clock)
        self.cat.loads.clear()
        self.assertEqual(idx.query(Query()).total, 3)
        self.assertEqual(sorted(self.cat.loads), ["painting/a", "painting/b"])

    def test_refresh_before_warm_does_nothing(self):
        idx = LibraryIndex(self.cat, self.store, self.tool, self.vcs, clock=self.clock)
        self.cat.loads.clear()
        idx.refresh({"painting/a"})
        self.assertEqual(self.cat.loads, [])

    def test_concurrent_queries_stress(self):
        self.collection("painting/big", [wp(f"big_{i:04d}.jpg", f"Big {i}") for i in range(300)])
        self.idx.refresh({"painting/big"})
        errors: list[BaseException] = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    page = self.idx.query(Query(limit=50))
                    if page.total not in (303, 302) or len(page.items) != 50:
                        raise AssertionError(f"inconsistent page: total={page.total} items={len(page.items)}")
                    folder = self.idx.query(Query(folder="painting/big", limit=500))
                    if len(folder.items) != folder.total:
                        raise AssertionError("items and total disagree")
            except BaseException as e:  # reported by the main thread
                errors.append(e)

        def writer():
            try:
                c = self.cat.load("painting/a")
                for i in range(20):
                    c.wallpapers = c.wallpapers[:1] if i % 2 == 0 else [c.wallpapers[0], wp("a_two.jpg", "Two")]
                    self.cat.save(c)
                    self.idx.refresh({"painting/a", "painting/big"})
            except BaseException as e:
                errors.append(e)

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        w = threading.Thread(target=writer)
        w.start()
        w.join(60)
        stop.set()
        for t in readers:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.idx.query(Query()).total, 303)


class RobustnessTest(IndexCase):
    """One broken folder or a misbehaving git never breaks the index; git never runs under the index lock."""

    def setUp(self):
        super().setUp()
        self.collection("painting/a", [wp("a_one.jpg", "One"), wp("a_two.jpg", "Two")])
        self.collection("painting/b", [wp("b_one.jpg", "Bee")])

    def test_undecodable_toml_is_a_folder_issue(self):
        (self.images / "painting/b/wallpapers.toml").write_bytes(b'[collection]\nmedium = "\xff"\n')
        idx = self.index()  # the warm-up completes
        self.assertTrue(idx.warmed)
        self.assertEqual(sorted(e.ref.file for e in idx.query(Query()).items), ["a_one.jpg", "a_two.jpg"])
        self.assertIn("painting/b", idx.folders())
        self.assertEqual([i.code for i in idx.folder_issues("painting/b")], ["invalid-toml"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_unreadable_toml_is_a_folder_issue(self):
        toml = self.images / "painting/b/wallpapers.toml"
        toml.chmod(0)
        self.addCleanup(toml.chmod, 0o644)
        idx = self.index()
        self.assertEqual(idx.query(Query()).total, 2)
        self.assertEqual([i.code for i in idx.folder_issues("painting/b")], ["unreadable"])
        idx.refresh({"painting/b"})  # after an operation: no exception (on_commit would roll back)
        toml.chmod(0o644)
        self.clock.now += 2.0
        self.assertEqual(idx.query(Query()).total, 3)  # fixed by the owner: listed again

    def test_any_failure_reading_a_folder_becomes_an_issue(self):
        idx = self.index()

        def boom(folder):
            raise RuntimeError("disk on fire")

        self.cat.load = boom
        self.cat.structural_issues = boom
        idx.refresh({"painting/b"})
        self.assertEqual(idx.query(Query()).total, 2)
        self.assertEqual([i.code for i in idx.folder_issues("painting/b")], ["unreadable"])
        self.assertIn("disk on fire", idx.folder_issues("painting/b")[0].message)

    def test_git_status_after_an_operation_runs_outside_the_lock(self):
        idx = self.index()
        entered, gate = threading.Event(), threading.Event()
        real = self.vcs.file_states

        def slow(paths):
            entered.set()
            gate.wait(10)
            return real(paths)

        self.vcs.file_states = slow
        t = threading.Thread(target=idx.refresh, args=({"painting/a"},))
        t.start()
        try:
            self.assertTrue(entered.wait(10))
            done = threading.Event()
            threading.Thread(target=lambda: (idx.query(Query()), done.set())).start()
            self.assertTrue(done.wait(5), "query blocked while git status ran")
        finally:
            gate.set()
            t.join(10)

    def test_background_git_failure_is_recorded_not_raised(self):
        idx = self.index()

        def broken(paths):
            raise RuntimeError("git exploded")

        self.vcs.file_states = broken
        self.clock.now += 1.0
        idx.ensure_fresh()
        idx.wait_for_git()
        self.assertIn("git exploded", idx.git_error)
        self.assertEqual(idx.query(Query()).total, 3)

    def test_known_git_states_survive_a_snapshot_that_missed_a_folder(self):
        a_one = self.images / "painting/a/a_one.jpg"
        toml = self.images / "painting/a/wallpapers.toml"
        good = toml.read_text()
        self.vcs.states[a_one] = "staged"
        idx = self.index()
        real = self.vcs.file_states
        gate1, entered1, gate2 = threading.Event(), threading.Event(), threading.Event()

        def first(paths):  # snapshot taken while painting/a was broken: a's refs are not in it
            entered1.set()
            gate1.wait(10)
            return real(paths)

        toml.write_text("[[[")
        self.vcs.file_states = first
        self.clock.now += 2.0
        idx.ensure_fresh()
        self.assertTrue(entered1.wait(10))
        toml.write_text(good)
        self.clock.now += 2.0
        idx.ensure_fresh()  # a reloaded, keeps its known state; the first git status is still running
        gate1.set()
        idx.wait_for_git()

        def second(paths):
            gate2.wait(10)
            return real(paths)

        self.vcs.file_states = second
        toml.write_text(good.replace('title = "One"', 'title = "Uno"'))
        self.clock.now += 2.0
        try:
            e = idx.query(Query(q="a_one")).items[0]  # reloaded again while the next git status is pending
            self.assertEqual((e.wallpaper.title, e.git_state), ("Uno", "staged"))
        finally:
            gate2.set()
            idx.wait_for_git()

    def test_concurrent_query_during_refresh(self):
        """A query issued while a refresh is half-way (folder a reloaded, b not yet) waits for the whole batch."""
        idx = self.index()
        for folder, old, new in (("painting/a", "One", "Uno"), ("painting/b", "Bee", "Wasp")):
            p = self.images / folder / "wallpapers.toml"
            p.write_text(p.read_text().replace(f'title = "{old}"', f'title = "{new}"'))
        in_b, release = threading.Event(), threading.Event()
        real_load = self.cat.load

        def gated(folder):
            if folder == "painting/b":
                in_b.set()
                release.wait(10)
            return real_load(folder)

        self.cat.load = gated
        writer = threading.Thread(target=idx.refresh, args=(["painting/a", "painting/b"],))
        seen: list[list[str]] = []
        reader_done = threading.Event()

        def reader():
            seen.append(sorted(e.wallpaper.title for e in idx.query(Query()).items))
            reader_done.set()

        writer.start()
        try:
            self.assertTrue(in_b.wait(10))  # painting/a is reloaded, painting/b is in the middle of its load
            threading.Thread(target=reader).start()
            self.assertFalse(reader_done.wait(0.3), "a query saw the index half-way through a refresh")
        finally:
            release.set()
            writer.join(10)
        self.assertTrue(reader_done.wait(10))
        self.assertEqual(seen, [["Two", "Uno", "Wasp"]])  # both changes or none, never one of them
        self.assertEqual(idx.folders(), ["painting/a", "painting/b"])


class OperationGitTest(IndexCase):
    """refresh() after an operation: git runs in background threads; answers of an older operation are dropped
    but its entries are asked about again; two statuses of one operation both apply."""

    def setUp(self):
        super().setUp()
        self.collection("painting/a", [wp("a_one.jpg", "One"), wp("a_two.jpg", "Two")])
        self.collection("painting/b", [wp("b_one.jpg", "Bee")])
        self.idx = self.index()
        self.a_one, self.a_two = self.images / "painting/a/a_one.jpg", self.images / "painting/a/a_two.jpg"
        self.b_one = self.images / "painting/b/b_one.jpg"

    def state(self, folder, file):
        return self.idx.entry(Ref(folder, file)).git_state

    def test_refresh_returns_before_git_and_the_wait_is_bounded(self):
        gate, asked = threading.Event(), []
        real = self.vcs.file_states
        self.vcs.file_states = lambda paths: (asked.append(sorted(p.name for p in paths)), gate.wait(10), real(paths))[2]
        self.vcs.states[self.a_one] = "staged"
        self.idx.refresh({"painting/a"}, [self.a_one])
        self.assertFalse(self.idx.wait_for_operation_git(0.05))
        self.assertEqual(self.state("painting/a", "a_one.jpg"), "untracked")  # reads do not wait
        gate.set()
        self.assertTrue(self.idx.wait_for_operation_git())
        self.assertEqual(self.state("painting/a", "a_one.jpg"), "staged")
        self.assertEqual(asked, [["a_one.jpg"]])  # the written file only, not the whole folder

    def test_a_newer_operation_covers_the_entries_of_an_older_one_in_flight(self):
        gate = threading.Event()
        real = self.vcs.file_states
        calls = []

        def slow_first(paths):
            calls.append(sorted(p.name for p in paths))
            if len(calls) == 1:
                gate.wait(10)
                return {p: "modified" for p in paths}  # stale: from before the second operation
            return real(paths)

        self.vcs.file_states = slow_first
        self.vcs.states[self.a_one] = "staged"
        self.vcs.states[self.b_one] = "staged"
        self.idx.refresh({"painting/a"}, [self.a_one])
        self.idx.refresh({"painting/b"}, [self.b_one])
        self.assertTrue(self.idx.wait_for_operation_git())  # the second answer landed
        gate.set()
        self.idx.wait_for_git()
        self.assertEqual(calls, [["a_one.jpg"], ["a_one.jpg", "b_one.jpg"]])
        self.assertEqual((self.state("painting/a", "a_one.jpg"), self.state("painting/b", "b_one.jpg")),
                         ("staged", "staged"))

    def test_entries_changed_without_a_written_file_are_asked_about_too(self):
        """A license change stages or unstages the image without writing it: the entry changed, so it is asked."""
        c = self.cat.load("painting/a")
        c.wallpapers[1] = Wallpaper(file="a_two.jpg", title="Two", artist="A", license="CC0-1.0", resolution=BIG)
        self.cat.save(c)
        self.vcs.states[self.a_two] = "staged"
        asked = []
        real = self.vcs.file_states
        self.vcs.file_states = lambda paths: (asked.append(sorted(p.name for p in paths)), real(paths))[1]
        self.idx.refresh({"painting/a"}, [self.images / "painting/a/wallpapers.toml"])
        self.assertTrue(self.idx.wait_for_operation_git())
        self.idx.wait_for_git()
        self.assertEqual(asked, [["a_two.jpg"]])  # the toml is not an entry; an empty early status asks nothing
        self.assertEqual(self.state("painting/a", "a_two.jpg"), "staged")

    def test_a_failing_apply_releases_waiters_at_once_and_is_logged(self):
        import contextlib
        import io
        from unittest import mock
        err = io.StringIO()
        with mock.patch.object(self.idx, "_apply_git", side_effect=RuntimeError("apply exploded")), \
                contextlib.redirect_stderr(err):
            self.idx.refresh({"painting/a"}, [self.a_one])
            started = time.monotonic()
            self.assertTrue(self.idx.wait_for_operation_git(5))
            self.idx.wait_for_git()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("apply exploded", err.getvalue())
        self.assertIn("apply exploded", self.idx.git_error)
        self.assertTrue(self.idx.wait_for_operation_git(0))  # nothing left pending for the next response

    def test_symlinked_image_path_is_resolved_like_the_store(self):
        target = self.images / "painting/a/real_target.jpg"
        target.write_bytes(png_header(1920, 1080))
        self.a_one.unlink()
        self.a_one.symlink_to(target)
        self.idx.refresh(None)
        self.assertEqual(self.idx.entry(Ref("painting/a", "a_one.jpg")).path, str(self.store.path("painting/a", "a_one.jpg")))
        self.assertEqual(self.idx.entry(Ref("painting/a", "a_two.jpg")).path, str(self.a_two))


    def test_symlink_resolving_outside_the_collection_root_is_not_followed(self):
        outside = self.images.parent / "outside_target.jpg"
        outside.write_bytes(png_header(1920, 1080))
        self.a_one.unlink()
        self.a_one.symlink_to(outside)
        self.idx.refresh(None)
        e = self.idx.entry(Ref("painting/a", "a_one.jpg"))
        self.assertFalse(e.path.startswith(str(outside)), e.path)
        self.assertEqual(e.mtime_ns, 0)  # treated as absent: never stat-ed, identified or thumbnailed

class IncrementalReloadTest(IndexCase):
    """After an operation the touched folder is reloaded incrementally (unchanged entries reused, only written or
    new files stat-ed): the result must equal a full load from scratch, issues and counts included."""

    def setUp(self):
        super().setUp()
        from wallman.services.unit_of_work import in_unit
        self.collection("painting/a", [
            wp("a_one.jpg", "One"), wp("a_two.jpg", "Two", tags=("night",)),
            wp("a_small.jpg", "Small", resolution=Resolution(800, 600)),
            wp("a_gone.jpg", "Gone"),  # no file
            wp("a_bad.jpg", "Bad"),  # not an image
            wp("a_crop.jpg", "Crop", original="../../_originals/a_crop.jpg"),
            wp("a_studio.jpg", "Studio", license="all-rights-reserved", license_detail="© S",
               download="https://example.org/s.jpg", sha256="0" * 64),
        ])
        self.collection("painting/b", [wp("b_one.jpg", "Bee")])
        (self.images / "painting/a/a_gone.jpg").unlink()
        (self.images / "painting/a/a_bad.jpg").write_bytes(b"not an image")
        (self.images / "painting/a/stray.png").write_bytes(png_header(1920, 1080))
        (self.images / "painting/a/broken.png").write_bytes(b"junk")
        self.idx = self.index()

        def on_commit(uow):
            self.idx.refresh(uow.touched, uow.touched_paths)

        def uow_factory():
            if not in_unit():
                self.idx.check_external()
            return UnitOfWork(self.cat, self.store, self.vcs, on_commit)

        self.lib = Library(self.cat, self.store, self.vcs, uow_factory, FakeFetcher(), index=self.idx)

    @staticmethod
    def snapshot(idx):
        idx.wait_for_git()
        with idx._lock:
            return [(f, [(e.ref, e.wallpaper, e.ratio_bucket, e.search_text, e.year_span, e.git_state, e.issues,
                          e.mtime_ns, e.size, e.path, e.external) for e in d.entries], d.issues, d.errors, d.warnings,
                     [str(p) for p in d.paths])
                    for f, d in sorted(idx._folders.items())]

    def assert_like_full_load(self):
        fresh = LibraryIndex(TomlCatalogue(self.images, FsStore(self.images)), FsStore(self.images), HeaderTool(),
                             self.vcs, clock=self.clock)
        fresh.warm()
        self.assertEqual(self.snapshot(self.idx), self.snapshot(fresh))

    def rewrite_image(self):
        with self.lib.uow_factory() as uow:
            p = self.images / "painting/a/a_two.jpg"
            uow.record_file(p)
            p.write_bytes(png_header(1280, 1024) + b"longer")
            uow.touched.add("painting/a")

    def overwrite_in_place(self, rel, w, h):
        """What `cp new.jpg x.jpg` or `mogrify` does: same name, same inode, the directory is not touched."""
        path = self.images / rel
        dir_mtime = (self.images / rel).parent.stat().st_mtime_ns
        with open(path, "r+b") as f:
            f.truncate(0)
            f.write(png_header(w, h) + b"rewritten")
        self.assertEqual(path.parent.stat().st_mtime_ns, dir_mtime)

    def test_image_overwritten_in_place_is_seen_by_the_next_operation(self):
        """Reviewer probe (I1): a 16:9 image replaced in place by a 5:4 one, then any edit in the same folder."""
        a = "painting/a"
        self.assertEqual(self.idx.entry(Ref(a, "a_one.jpg")).ratio_bucket, "16:9")
        self.overwrite_in_place(f"{a}/a_one.jpg", 2000, 1600)
        self.lib.update(Ref(a, "a_two.jpg"), {"title": "Deux"})
        self.idx.wait_for_git()  # the re-stat of the files the edit did not write runs right after it
        e = self.idx.entry(Ref(a, "a_one.jpg"))
        self.assertEqual(e.ratio_bucket, "5:4")
        self.assertEqual(e.size, (self.images / a / "a_one.jpg").stat().st_size)
        self.assertIn("resolution-mismatch", {i.code for i in e.issues})
        self.assert_like_full_load()

    def test_image_overwritten_in_place_is_seen_by_listing_within_ten_seconds(self):
        from wallman.services.library_index import DEEP_SECONDS
        a = "painting/a"
        self.overwrite_in_place(f"{a}/a_one.jpg", 2000, 1600)
        self.clock.now += DEEP_SECONDS / 2
        self.idx.query(Query(folder=a))
        self.idx.wait_for_git()
        self.assertEqual(self.idx.entry(Ref(a, "a_one.jpg")).ratio_bucket, "16:9")  # not yet: at most every 10 s
        self.clock.now += DEEP_SECONDS / 2
        self.idx.query(Query(folder=a))  # the re-stat runs in the background: this request does not wait for it
        self.idx.wait_for_git()
        self.assertEqual(self.idx.entry(Ref(a, "a_one.jpg")).ratio_bucket, "5:4")
        self.assert_like_full_load()

    def test_operations_reload_like_a_full_load(self):
        from unittest import mock
        light: list[bool] = []
        real = LibraryIndex._read_light

        def counting(idx, *args):
            result = real(idx, *args)
            light.append(result is not None)
            return result

        patcher = mock.patch.object(LibraryIndex, "_read_light", counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        a = "painting/a"
        self.vcs.states[self.images / a / "a_one.jpg"] = "committed"
        steps = [
            lambda: self.lib.update(Ref(a, "a_one.jpg"), {"title": "Uno"}),  # rename
            lambda: self.lib.update(Ref(a, "a_two.jpg"), {"tags": ["blue", "night"]}),
            lambda: self.lib.update(Ref(a, "a_small.jpg"), {"license": "CC0-1.0"}),
            lambda: self.lib.update(Ref(a, "a_crop.jpg"), {"year": 1900}),
            self.rewrite_image,  # an image rewritten in place (same name, as a crop does), no toml change
            lambda: self.lib.update(Ref(a, "a_two.jpg"), {"folder": "painting/b"}),  # move: the general reload
        ]
        for k, step in enumerate(steps):
            with self.subTest(step=k):
                if k == 2:  # an image dropped in from outside just before: picked up when the operation starts
                    (self.images / a / "late.png").write_bytes(png_header(1920, 1080))
                step()
                self.assert_like_full_load()
        self.assertEqual(light, [True, True, True, True, True, False, False])
        self.assertIn("unlisted", {i.code for i in self.idx.folder_issues(a) if i.file == "late.png"})
        self.assertIsNotNone(self.idx.entry(Ref(a, "a_uno.jpg")))
        self.assertIsNotNone(self.idx.entry(Ref("painting/b", "a_two.jpg")))


class LibraryDelegatesTest(IndexCase):
    def test_list_uses_the_index_and_keeps_v1_shape(self):
        self.collection("painting/a", [wp("a_z.jpg", "Zeta"), wp("a_a.jpg", "Alpha", resolution=Resolution(800, 600))])
        self.collection("painting/b", [wp("b_b.jpg", "Beta")])
        idx = self.index()
        lib = Library(self.cat, self.store, self.vcs, lambda: None, FakeFetcher(), index=idx)
        self.cat.loads.clear()
        rows = lib.list(folder="painting")
        self.assertEqual([r.file for r, _, _ in rows], ["a_z.jpg", "a_a.jpg", "b_b.jpg"])  # catalogue order
        r, w, issues = rows[1]
        self.assertEqual((r, w.title), (Ref("painting/a", "a_a.jpg"), "Alpha"))
        self.assertIsInstance(issues, list)
        self.assertIn("too-small", {i.code for i in issues})
        self.assertEqual([r.file for r, _, _ in lib.list(with_issues=True)], ["a_a.jpg"])
        self.assertEqual(self.cat.loads, [])  # served from memory


class TouchedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.images = self.root / "images"
        self.images.mkdir()
        self.store = FsStore(self.images)
        self.cat = TomlCatalogue(self.images, self.store)
        self.seen: list[set[str]] = []

    def uow(self):
        return UnitOfWork(self.cat, self.store, FakeVcs(), lambda u: self.seen.append(set(u.touched)))

    def test_touched_folders_from_collections_and_files(self):
        with self.uow() as u:
            u.record_collection("painting/a")
            u.record_file(self.images / "poster/p/x.jpg")
            u.record_file(self.root / "README.md")  # outside the collection root
            with self.uow() as inner:  # nested units add to the outermost one
                inner.record_file(self.images / "painting/b/y.jpg")
        self.assertEqual(self.seen, [{"painting/a", "poster/p", "painting/b"}])
        with self.uow() as u:
            u.record_collection("linux")
        self.assertEqual(self.seen[-1], {"linux"})  # a new outermost unit starts empty


class WallpaperStateTest(IndexCase):
    """Publish model: git states per wallpaper, from the TOML blobs of the index and HEAD plus the image state."""

    def test_states_from_the_index_toml(self):
        import dataclasses
        c = self.collection("painting/a", [wp("a.jpg"), wp("b.jpg"), wp("c.jpg", license="all-rights-reserved",
                                                                      download="https://e/c.jpg", sha256="0" * 64)])
        self.collection("ml4w", [wp("e.jpg")], medium="ml4w")
        toml = self.images / "painting/a/wallpapers.toml"
        self.vcs.entry_states = True
        self.vcs.head[toml] = self.cat.render(dataclasses.replace(c, wallpapers=c.wallpapers[:1]))
        self.vcs.index[toml] = self.cat.render(dataclasses.replace(c, wallpapers=[c.wallpapers[0], c.wallpapers[2]]))
        self.vcs.tracked = {self.images / "painting/a/a.jpg", self.images / "ml4w/e.jpg"}
        self.vcs.states[self.images / "painting/a/c.jpg"] = "ignored"
        from unittest import mock
        with mock.patch.object(self.store, "is_external", side_effect=lambda f: f == "ml4w"):
            idx = self.index()
        states = {e.ref.file: e.git_state for e in idx.query(Query()).items}
        self.assertEqual(states, {"a.jpg": "committed", "b.jpg": "untracked", "c.jpg": "metadata-staged",
                                  "e.jpg": "committed"})  # external: per-file state as before
        self.vcs.head[toml] = self.vcs.index[toml]
        idx.refresh({"painting/a"})
        idx.wait_for_git()
        self.assertEqual(idx.entry(Ref("painting/a", "c.jpg")).git_state, "metadata")


class BootstrapIndexTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "repo"
        fixture.build(self.root)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        from wallman import bootstrap
        self.app = bootstrap.build(self.root, Path(tmp.name) / "cache", FakeFetcher())

    def test_index_is_lazy_and_refreshed_after_operations(self):
        from wallman.services.git_actions import GitActions
        self.assertIsInstance(self.app.git_actions, GitActions)
        self.assertFalse(self.app.index.warmed)  # CLI commands never pay for the warm-up
        before = self.app.index.query(Query(folder="poster")).items[0]
        self.assertEqual(before.wallpaper.title, "Poster")
        new = self.app.library.update(before.ref, {"source": "https://example.org/changed"})
        after = self.app.index.query(Query(folder="poster")).items[0]  # same instant: no stat window involved
        self.assertEqual((after.ref, after.wallpaper.source), (new, "https://example.org/changed"))


class WarmBenchmarkTest(unittest.TestCase):
    """Slow (a few seconds): 10,000 synthetic entries across 20 folders; spec target warm-up < 3 s."""

    N, FOLDERS = 10_000, 20

    def test_warm_10000_under_3s(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        images = Path(tmp.name).resolve() / "images"
        store = FsStore(images)
        cat = TomlCatalogue(images, store)
        tiny = png_bytes(1, 1)
        per = self.N // self.FOLDERS
        for f in range(self.FOLDERS):
            folder = f"painting/bench-{f:02d}"
            d = images / folder
            d.mkdir(parents=True)
            ws = []
            for i in range(per):
                name = f"artist-{i % 97}_title-{f}-{i}_{1400 + i % 700}.png"
                (d / name).write_bytes(tiny)
                ws.append(Wallpaper(file=name, title=f"Title {f} {i}", artist=f"Artist {i % 97}",
                                    license="public-domain", resolution=Resolution(1, 1),
                                    year=YearSpan(1400 + i % 700, 1400 + i % 700), tags=(f"tag-{i % 13}",)))
            cat.save(Collection(folder=folder, medium="painting", movement=f"bench-{f:02d}", wallpapers=ws))
        clock = Clock()
        idx = LibraryIndex(cat, store, HeaderTool(), FakeVcs(), clock=clock)
        t0 = time.perf_counter()
        idx.warm()
        warm = time.perf_counter() - t0
        t0 = time.perf_counter()
        page = idx.query(Query(q="title", sort="year", offset=5000, limit=200))
        query = time.perf_counter() - t0
        clock.now += 2.0  # this request also runs the stat freshness check (git status goes to the background)
        t0 = time.perf_counter()
        idx.query(Query(folder="painting/bench-03", sort="name", limit=200))
        checked = time.perf_counter() - t0
        idx.wait_for_git()
        timings = f"warm {warm:.2f} s, query {query * 1000:.1f} ms, with freshness check {checked * 1000:.1f} ms"
        self.assertEqual(page.total, self.N)
        self.assertEqual(len(page.items), 200)
        self.assertLess(warm, 3.0, timings)
        self.assertLess(query, 0.25, timings)  # target 50 ms (measured ~8 ms); loose to avoid flakes
        self.assertLess(checked, 0.25, timings)


if __name__ == "__main__":
    unittest.main()
