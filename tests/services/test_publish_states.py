"""Per-wallpaper git states (publish model): entry in HEAD / index TOML, index == working, image state."""
import dataclasses
import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeStore, FakeVcs
from tests.services.support import col, wp
from wallman.services.publish import EntryStates, wallpaper_state

DL = dict(license="all-rights-reserved", download="https://example.org/d.jpg", sha256="0" * 64)


class CombineTest(unittest.TestCase):
    def test_states(self):
        cases = [
            (("none", "untracked", True), "untracked"),
            (("none", "ignored", False), "untracked"),
            (("deleted", "deleted", True), "deleted"),
            (("deleted", "ignored", False), "deleted"),
            (("modified", "committed", True), "modified"),
            (("committed", "committed", True), "committed"),
            (("committed", "staged", True), "staged"),
            (("staged", "committed", True), "staged"),
            (("committed", "untracked", True), "modified"),  # published shareable entry, image not in git
            (("committed", "modified", True), "modified"),
            (("committed", "ignored", False), "metadata"),
            (("staged", "ignored", False), "metadata-staged"),
            (("committed", "conflict", True), "conflict"),
            (("none", "conflict", True), "untracked"),  # not published: the image conflict is not its own
        ]
        for args, want in cases:
            self.assertEqual(wallpaper_state(*args), want, args)


class EntryStatesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = FakeStore({"p/x": {"a.jpg", "b.jpg", "c.jpg"}}, root=self.root)
        self.cat = FakeCatalogue()
        self.vcs = FakeVcs()
        self.vcs.entry_states = True
        self.coll = col("p/x", [wp("a.jpg"), wp("b.jpg"), wp("c.jpg", **DL)])
        self.toml = self.store.path("p/x", "wallpapers.toml")
        self.toml.parent.mkdir(parents=True)
        self.write_working(self.coll)
        self.states = EntryStates(self.cat, self.vcs)

    def write_working(self, coll):
        self.coll = coll
        self.toml.write_bytes(self.cat.render(coll))

    def subset(self, *files, coll=None):
        coll = coll or self.coll
        return self.cat.render(dataclasses.replace(coll, wallpapers=[w for w in coll.wallpapers if w.file in files]))

    def ask(self):
        ids = self.vcs.blob_ids([self.toml])[self.toml]
        return self.states.folder(self.toml, ids, [(w.file, w) for w in self.coll.wallpapers])

    def test_nothing_in_git(self):
        self.assertEqual(self.ask(), {"a.jpg": "none", "b.jpg": "none", "c.jpg": "none"})

    def test_whole_toml_committed_is_all_committed_without_reading_blobs(self):
        self.vcs.index[self.toml] = self.vcs.head[self.toml] = self.toml.read_bytes()
        self.assertEqual(self.ask(), {"a.jpg": "committed", "b.jpg": "committed", "c.jpg": "committed"})
        self.assertEqual([c for c in self.vcs.calls if c[0] == "read_blob"], [])

    def test_partially_published_and_staged(self):
        self.vcs.head[self.toml] = self.subset("a.jpg")
        self.vcs.index[self.toml] = self.subset("a.jpg", "c.jpg")
        self.assertEqual(self.ask(), {"a.jpg": "committed", "b.jpg": "none", "c.jpg": "staged"})

    def test_changed_values_staged_then_modified_by_hand(self):
        self.vcs.head[self.toml] = self.subset("a.jpg", "b.jpg")
        edited = col("p/x", [wp("a.jpg", title="New"), wp("b.jpg"), wp("c.jpg", **DL)])
        self.vcs.index[self.toml] = self.subset("a.jpg", "b.jpg", coll=edited)
        self.write_working(edited)
        self.assertEqual(self.ask(), {"a.jpg": "staged", "b.jpg": "committed", "c.jpg": "none"})
        self.write_working(col("p/x", [wp("a.jpg", title="New"), wp("b.jpg", title="Hand"), wp("c.jpg", **DL)]))
        self.assertEqual(self.ask()["b.jpg"], "modified")

    def test_unpublished_but_committed_is_deleted(self):
        self.vcs.head[self.toml] = self.subset("a.jpg", "b.jpg")
        self.vcs.index[self.toml] = self.subset("b.jpg")
        self.assertEqual(self.ask(), {"a.jpg": "deleted", "b.jpg": "committed", "c.jpg": "none"})

    def test_blobs_are_read_once_per_id(self):
        self.vcs.head[self.toml] = self.subset("a.jpg")
        self.vcs.index[self.toml] = self.subset("a.jpg", "c.jpg")
        self.ask()
        reads = len([c for c in self.vcs.calls if c[0] == "read_blob"])
        self.ask()
        self.assertEqual(len([c for c in self.vcs.calls if c[0] == "read_blob"]), reads)
