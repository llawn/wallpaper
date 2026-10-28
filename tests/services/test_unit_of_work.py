import tempfile
import threading
import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeStore, FakeVcs
from wallman.services.unit_of_work import UnitOfWork


class UnitOfWorkTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = FakeStore(root=self.root)
        self.commits = 0
        self.uow = UnitOfWork(FakeCatalogue(), self.store, FakeVcs(), self._commit)

    def _commit(self, uow):
        self.commits += 1

    def test_rollback_restores_file_and_toml(self):
        img = self.root / "a" / "x.jpg"
        toml = self.root / "a" / "wallpapers.toml"
        new = self.root / "a" / "new.jpg"
        img.parent.mkdir()
        img.write_bytes(b"old image")
        toml.write_bytes(b"old toml\n")
        with self.assertRaises(ValueError):
            with self.uow as u:
                u.record_file(img)
                u.record_collection("a")
                u.record_file(new)
                img.write_bytes(b"changed")
                toml.write_bytes(b"not valid toml at all [[[")
                new.write_bytes(b"created")
                raise ValueError("boom")
        self.assertEqual(img.read_bytes(), b"old image")
        self.assertEqual(toml.read_bytes(), b"old toml\n")
        self.assertFalse(new.exists())
        self.assertEqual(self.commits, 0)

    def test_rollback_deletes_toml_that_did_not_exist(self):
        toml = self.root / "b" / "wallpapers.toml"
        with self.assertRaises(ValueError):
            with self.uow as u:
                u.record_collection("b")
                toml.parent.mkdir()
                toml.write_text("x")
                raise ValueError
        self.assertFalse(toml.exists())

    def test_stage_defaults_true_and_nested_units_share_outermost(self):
        seen = []
        uow = UnitOfWork(FakeCatalogue(), self.store, FakeVcs(), lambda u: seen.append(u.stage))
        with uow as outer:
            self.assertTrue(outer.stage)
            with UnitOfWork(FakeCatalogue(), self.store, FakeVcs(), lambda u: None) as inner:
                inner.stage = False
                self.assertFalse(outer.stage)
        self.assertEqual(seen, [False])
        with uow:
            pass
        self.assertEqual(seen, [False, True])

    def test_on_commit_runs_once_per_batch(self):
        with self.uow as u:
            u.record_file(self.root / "f")
            with self.uow:
                pass
            with self.uow:
                pass
            self.assertEqual(self.commits, 0)
        self.assertEqual(self.commits, 1)

    def test_inner_failure_caught_by_outer_keeps_outer_work(self):
        a, b = self.root / "a.txt", self.root / "b.txt"
        a.write_text("a0")
        b.write_text("b0")
        with self.uow as u:
            u.record_file(a)
            a.write_text("a1")
            try:
                with self.uow as inner:
                    inner.record_file(b)
                    b.write_text("b1")
                    raise ValueError
            except ValueError:
                pass
        self.assertEqual((a.read_text(), b.read_text()), ("a1", "b0"))
        self.assertEqual(self.commits, 1)

    def test_nested_operations_share_one_lock(self):
        # the same thread re-enters (RLock); another thread waits until the outermost block exits
        entered = threading.Event()
        order = []

        def other():
            with UnitOfWork(FakeCatalogue(), self.store, FakeVcs(), lambda uow: None):
                order.append("other")

        with self.uow:
            with self.uow:
                t = threading.Thread(target=other)
                t.start()
                entered.wait(0.2)
                order.append("inner")
            order.append("outer")
        t.join(2)
        self.assertEqual(order, ["inner", "outer", "other"])


if __name__ == "__main__":
    unittest.main()


class DeferredStagingTest(unittest.TestCase):
    """stage_add() inside a unit is one `git add` at commit time (merged by on_commit), and none on a rollback."""

    def setUp(self):
        self.vcs = FakeVcs()
        self.store = FakeStore(root=Path(tempfile.mkdtemp()))

    def test_on_commit_takes_them_into_its_own_git_add(self):
        def on_commit(uow):
            self.vcs.stage_add(uow.take_staged() + [Path("/r/.gitignore")])

        with UnitOfWork(FakeCatalogue(), self.store, self.vcs, on_commit) as u:
            u.stage_add([Path("/r/a/wallpapers.toml")])
            with UnitOfWork(FakeCatalogue(), self.store, self.vcs, on_commit) as inner:  # nested: same list
                inner.stage_add([Path("/r/b/wallpapers.toml"), Path("/r/a/wallpapers.toml")])
            self.assertEqual(self.vcs.calls, [])  # nothing staged before the commit
        self.assertEqual(self.vcs.calls, [("stage_add", [Path("/r/a/wallpapers.toml"), Path("/r/b/wallpapers.toml"),
                                                         Path("/r/.gitignore")])])

    def test_left_over_paths_are_staged_after_on_commit_and_none_on_rollback(self):
        with UnitOfWork(FakeCatalogue(), self.store, self.vcs, lambda u: None) as u:
            u.stage_add([Path("/r/a/wallpapers.toml")])
        self.assertEqual(self.vcs.calls, [("stage_add", [Path("/r/a/wallpapers.toml")])])
        self.vcs.calls.clear()
        with self.assertRaises(ValueError):
            with UnitOfWork(FakeCatalogue(), self.store, self.vcs, lambda u: None) as u:
                u.stage_add([Path("/r/a/wallpapers.toml")])
                raise ValueError("boom")
        self.assertEqual(self.vcs.calls, [])

    def test_forced_paths_make_the_batch_forced(self):
        with UnitOfWork(FakeCatalogue(), self.store, self.vcs, lambda u: None) as u:
            u.stage_add([Path("/r/a/wallpapers.toml")])
            u.stage_add([Path("/r/a/x.jpg")], force=True)
        self.assertEqual(self.vcs.calls, [("stage_add", [Path("/r/a/wallpapers.toml"), Path("/r/a/x.jpg")], "force")])

    def test_rollback_restores_a_recorded_git_index(self):
        from wallman.services.unit_of_work import record_index
        index = self.store.root / "index"
        index.write_bytes(b"before")
        record_index(index)  # outside a unit: nothing recorded
        with self.assertRaises(ValueError):
            with UnitOfWork(FakeCatalogue(), self.store, self.vcs, lambda u: None):
                record_index(index)
                index.write_bytes(b"git add ran")
                record_index(index)  # once per unit: the first snapshot wins
                index.write_bytes(b"git mv ran")
                raise ValueError("boom")
        self.assertEqual(index.read_bytes(), b"before")
