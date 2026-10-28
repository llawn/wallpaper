import os
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path

from wallman.adapters.git_cli import GitCli
from wallman.domain.models import Change
from wallman.errors import GitError

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "protocol.file.allow=always", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout


class GitCliLockTest(unittest.TestCase):
    def test_commands_never_take_optional_locks(self):
        seen = []
        real = subprocess.run

        def spy(cmd, **kw):
            seen.append(kw.get("env", {}).get("GIT_OPTIONAL_LOCKS"))
            return real(cmd, **kw)

        with tempfile.TemporaryDirectory() as d:
            real(["git", "init", "-q", d], check=True)
            from unittest import mock
            (Path(d) / "a.txt").write_text("a")
            with mock.patch("wallman.adapters.git_cli.subprocess.run", spy):
                g = GitCli(Path(d))
                g.status()
                g.stage_add([Path(d) / "a.txt"])
        self.assertTrue(seen and all(v == "0" for v in seen), seen)


class GitCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        subsrc = base / "subsrc"
        subsrc.mkdir()
        git(subsrc, "init", "-q")
        (subsrc / "seed.txt").write_text("s")
        git(subsrc, "add", "seed.txt")
        git(subsrc, "commit", "-q", "-m", "seed")
        self.root = base / "main"
        self.root.mkdir()
        git(self.root, "init", "-q")
        (self.root / "a.jpg").write_text("a")
        git(self.root, "add", "a.jpg")
        git(self.root, "commit", "-q", "-m", "init")
        git(self.root, "submodule", "add", "-q", str(subsrc), "sub")
        git(self.root, "commit", "-q", "-m", "sub")
        self.git = GitCli(self.root)

    def staged(self, repo=None):
        return git(repo or self.root, "diff", "--cached", "--name-status")

    def test_staged_lists_added_modified_not_deleted(self):
        (self.root / "b é.jpg").write_text("b")
        (self.root / "a.jpg").write_text("changed")
        self.git.stage_add([self.root / "b é.jpg", self.root / "a.jpg"])
        self.assertEqual(self.git.staged(), [self.root / "a.jpg", self.root / "b é.jpg"])
        git(self.root, "commit", "-q", "-m", "x")
        self.git.stage_rm([self.root / "b é.jpg"])
        self.assertEqual(self.git.staged(), [])  # deletions are not reported

    def test_stage_rm_removes_file_staged_but_not_committed(self):
        f = self.root / "new.jpg"
        f.write_text("n")
        self.git.stage_add([f])
        self.git.stage_rm([f])
        self.assertFalse(f.exists())
        self.assertEqual(self.git.staged(), [])

    def test_staged_empty_outside_work_tree(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(GitCli(Path(t)).staged(), [])

    def test_stage_add_and_rm(self):
        f = self.root / "b.jpg"
        f.write_text("b")
        self.git.stage_add([f])
        self.assertIn("A\tb.jpg", self.staged())
        self.git.stage_rm([self.root / "a.jpg"])
        self.assertIn("D\ta.jpg", self.staged())
        self.assertFalse((self.root / "a.jpg").exists())

    def test_stage_add_force_adds_ignored_file(self):
        (self.root / ".gitignore").write_text("ign.jpg\n")
        f = self.root / "ign.jpg"
        f.write_text("i")
        with self.assertRaises(Exception):
            self.git.stage_add([f])
        self.assertNotIn("ign.jpg", self.staged())
        self.git.stage_add([f], force=True)
        self.assertIn("A\tign.jpg", self.staged())

    def test_stage_mv_tracked(self):
        self.git.stage_mv(self.root / "a.jpg", self.root / "c.jpg")
        self.assertIn("R100\ta.jpg\tc.jpg", self.staged())

    def test_stage_mv_untracked_raises(self):
        (self.root / "u.jpg").write_text("u")
        with self.assertRaises(GitError):
            self.git.stage_mv(self.root / "u.jpg", self.root / "v.jpg")

    def test_untrack_keeps_file(self):
        self.git.untrack([self.root / "a.jpg"])
        self.assertTrue((self.root / "a.jpg").exists())
        self.assertIn("D\ta.jpg", self.staged())
        self.assertFalse(self.git.is_tracked(self.root / "a.jpg"))

    def test_is_tracked(self):
        self.assertTrue(self.git.is_tracked(self.root / "a.jpg"))
        (self.root / "n.jpg").write_text("n")
        self.assertFalse(self.git.is_tracked(self.root / "n.jpg"))

    def test_paths_in_submodule_run_inside_it(self):
        f = self.root / "sub" / "x.jpg"
        f.write_text("x")
        self.git.stage_add([f])
        self.assertIn("A\tx.jpg", self.staged(self.root / "sub"))
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only").strip(), "")
        self.assertFalse(self.git.is_tracked(self.root / "sub" / "nope.jpg"))
        self.assertTrue(self.git.is_tracked(f))

    def test_submodule_list_is_cached_until_gitmodules_or_init_changes(self):
        from unittest import mock
        real = self.git._list_submodules
        with mock.patch.object(self.git, "_list_submodules", side_effect=real) as spy:
            self.assertEqual(self.git._submodules(), ["sub"])
            self.assertEqual(self.git._submodules(), ["sub"])
            self.assertEqual(spy.call_count, 1)
            git(self.root, "submodule", "deinit", "-q", "-f", "sub")  # sub/.git goes away
            self.assertEqual(self.git._submodules(), [])
            git(self.root, "submodule", "update", "-q", "--init")  # and comes back
            self.assertEqual(self.git._submodules(), ["sub"])
            other = self.root.parent / "subsrc"
            git(self.root, "submodule", "add", "-q", str(other), "sub2")  # .gitmodules changes
            self.assertEqual(sorted(self.git._submodules()), ["sub", "sub2"])
            self.assertEqual(spy.call_count, 4)

    def test_stage_mv_in_submodule(self):
        self.git.stage_mv(self.root / "sub" / "seed.txt", self.root / "sub" / "moved.txt")
        self.assertIn("R100\tseed.txt\tmoved.txt", self.staged(self.root / "sub"))

    def test_status_lists_repo_and_submodule(self):
        (self.root / "new.jpg").write_text("n")
        (self.root / "sub" / "é ü.jpg").write_text("x")
        changes = self.git.status()
        self.assertIn(Change(".", "??", "new.jpg"), changes)
        self.assertIn(Change("sub", "??", "é ü.jpg"), changes)

    def test_status_reports_submodule_pointer_and_inner_changes(self):
        sub = self.root / "sub"
        (sub / "seed.txt").write_text("changed")
        git(sub, "commit", "-q", "-am", "move head")
        (sub / "loose.jpg").write_text("x")
        changes = self.git.status()
        self.assertIn(Change(".", "M", "sub"), changes)
        self.assertIn(Change("sub", "??", "loose.jpg"), changes)

    def test_filename_starting_with_dash(self):
        f = self.root / "-x.jpg"
        f.write_text("x")
        self.git.stage_add([f])
        self.assertIn("A\t-x.jpg", self.staged())

    def test_file_states_each_kind(self):
        r = self.root
        (r / ".gitignore").write_text("ign.jpg\nign/\n")
        git(r, "add", ".gitignore")
        (r / "mod.jpg").write_text("1")
        (r / "del.jpg").write_text("1")
        git(r, "add", "mod.jpg", "del.jpg")
        git(r, "commit", "-q", "-m", "more")
        (r / "mod.jpg").write_text("2")
        (r / "del.jpg").unlink()
        (r / "stg é.jpg").write_text("s")
        git(r, "add", "stg é.jpg")
        (r / "unt.jpg").write_text("u")
        (r / "ign.jpg").write_text("i")
        (r / "ign").mkdir()
        (r / "ign" / "in.jpg").write_text("i")
        names = ["a.jpg", "mod.jpg", "del.jpg", "stg é.jpg", "unt.jpg", "ign.jpg", "ign/in.jpg", "nope.jpg"]
        for st in self.both_pathspec_modes([r / n for n in names]):
            self.assertEqual({k.relative_to(r).as_posix(): v for k, v in st.items()}, {
                "a.jpg": "committed", "mod.jpg": "modified", "del.jpg": "deleted", "stg é.jpg": "staged",
                "unt.jpg": "untracked", "ign.jpg": "ignored", "ign/in.jpg": "ignored", "nope.jpg": "untracked"})

    def both_pathspec_modes(self, paths):
        """file_states asks git per file for a few paths and per folder for many: both must agree."""
        from unittest import mock
        few = self.git.file_states(paths)
        with mock.patch("wallman.adapters.git_cli.FILE_PATHSPECS", 0):
            many = self.git.file_states(paths)
        return [few, many]

    def test_file_states_across_nested_folders_only_requested_paths(self):
        r = self.root
        for rel in ("p/a/t.jpg", "p/a/other.jpg", "p/a/new/n.jpg", "p/ab/u.jpg", "q/u.jpg"):
            (r / rel).parent.mkdir(parents=True, exist_ok=True)
            (r / rel).write_text(rel)
        git(r, "add", "p/a/t.jpg", "p/a/other.jpg")
        git(r, "commit", "-q", "-m", "p")
        asked = [r / "p/a/t.jpg", r / "p/a/new/n.jpg", r / "p/ab/u.jpg", r / "q/u.jpg", r / "p/a/gone.jpg"]
        st, by_folder = self.both_pathspec_modes(asked)  # other files of a folder must not leak into the result
        self.assertEqual(st, by_folder)
        self.assertEqual(st, {r / "p/a/t.jpg": "committed", r / "p/a/new/n.jpg": "untracked",
                              r / "p/ab/u.jpg": "untracked", r / "q/u.jpg": "untracked",
                              r / "p/a/gone.jpg": "untracked"})

    def test_file_states_literal_pathspecs(self):
        r = self.root
        (r / "a[1].jpg").write_text("x")
        (r / "a1.jpg").write_text("y")
        git(r, "add", "a1.jpg")
        st = self.git.file_states([r / "a[1].jpg", r / "a1.jpg"])
        self.assertEqual(st, {r / "a[1].jpg": "untracked", r / "a1.jpg": "staged"})

    def test_file_states_staged_then_modified_and_staged_delete(self):
        r = self.root
        (r / "a.jpg").write_text("x")
        git(r, "add", "a.jpg")
        (r / "a.jpg").write_text("y")
        self.assertEqual(self.git.file_states([r / "a.jpg"]), {r / "a.jpg": "modified"})
        git(r, "rm", "-q", "-f", "a.jpg")
        self.assertEqual(self.git.file_states([r / "a.jpg"]), {r / "a.jpg": "deleted"})

    def test_file_states_conflict(self):
        r = self.root
        git(r, "checkout", "-q", "-b", "side")
        (r / "a.jpg").write_text("side")
        git(r, "commit", "-q", "-am", "side")
        git(r, "checkout", "-q", "-")
        (r / "a.jpg").write_text("main")
        git(r, "commit", "-q", "-am", "main")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(r), "merge", "side"],
                       capture_output=True, env=ENV)
        self.assertEqual(self.git.file_states([r / "a.jpg"]), {r / "a.jpg": "conflict"})

    def test_file_states_routes_into_submodule(self):
        sub = self.root / "sub"
        (sub / "new.jpg").write_text("n")
        (sub / "tr.jpg").write_text("t")
        git(sub, "add", "tr.jpg")
        (sub / "seed.txt").write_text("changed")
        st = self.git.file_states([sub / "new.jpg", sub / "tr.jpg", sub / "seed.txt", self.root / "a.jpg"])
        self.assertEqual(st, {sub / "new.jpg": "untracked", sub / "tr.jpg": "staged",
                              sub / "seed.txt": "modified", self.root / "a.jpg": "committed"})

    def test_file_states_outside_work_tree_and_empty(self):
        self.assertEqual(self.git.file_states([]), {})
        with tempfile.TemporaryDirectory() as t:
            f = Path(t).resolve() / "a.jpg"
            f.write_text("a")
            self.assertEqual(GitCli(Path(t)).file_states([f]), {f: "untracked"})

    def test_not_a_work_tree_is_noop_with_one_warning(self):
        with tempfile.TemporaryDirectory() as d:
            g = GitCli(Path(d))
            f = Path(d) / "a.jpg"
            f.write_text("a")
            with self.assertWarns(UserWarning):
                g.stage_add([f])
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                g.stage_rm([f])
                g.untrack([f])
                g.stage_mv(f, Path(d) / "b.jpg")
            self.assertEqual(w, [])
            self.assertEqual(g.status(), [])
            self.assertTrue(f.exists())
            self.assertFalse(g.is_tracked(f))


class GitCliBlobTest(unittest.TestCase):
    """The index TOML is written as a blob: stage_blob / index_blob / head_blob / unstage_path / blob_ids."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        git(self.root, "init", "-q")
        (self.root / "d").mkdir()
        self.toml = self.root / "d" / "wallpapers.toml"
        self.toml.write_text("working\n")
        git(self.root, "add", "d/wallpapers.toml")
        git(self.root, "commit", "-q", "-m", "init")
        self.git = GitCli(self.root)

    def test_stage_blob_writes_the_index_only(self):
        self.git.stage_blob(self.toml, b"generated\n")
        self.assertEqual(git(self.root, "show", ":d/wallpapers.toml"), "generated\n")
        self.assertEqual(self.toml.read_text(), "working\n")
        self.assertEqual(self.git.index_blob(self.toml), b"generated\n")
        self.assertEqual(self.git.head_blob(self.toml), b"working\n")

    def test_stage_blob_adds_a_new_path_and_unstage_path_removes_it(self):
        new = self.root / "e [x]" / "wallpapers.toml"  # literal pathspecs: brackets are not a glob
        self.assertIsNone(self.git.index_blob(new))
        self.assertIsNone(self.git.head_blob(new))
        self.git.stage_blob(new, b"n\n")
        self.assertEqual(self.git.index_blob(new), b"n\n")
        self.assertFalse(new.exists())
        self.git.unstage_path(new)
        self.assertIsNone(self.git.index_blob(new))
        self.git.unstage_path(new)  # not in the index: no error

    def test_stage_blob_snapshots_the_index_first(self):
        seen = []
        self.git.on_index_write = seen.append
        self.git.stage_blob(self.toml, b"x")
        self.git.unstage_path(self.toml)
        self.assertEqual(len(seen), 2)
        self.assertTrue(str(seen[0]).endswith("index"))

    def test_blob_ids_and_read_blob(self):
        self.git.stage_blob(self.toml, b"generated\n")
        missing = self.root / "nope" / "wallpapers.toml"
        ids = self.git.blob_ids([self.toml, missing])
        index_id, head_id = ids[self.toml]
        self.assertEqual(ids[missing], (None, None))
        self.assertEqual(self.git.read_blob(index_id), b"generated\n")
        self.assertEqual(self.git.read_blob(head_id), b"working\n")
        self.assertEqual(self.git.index_blob_id(self.toml), index_id)
        self.assertIsNone(self.git.index_blob_id(missing))

    def test_outside_a_work_tree(self):
        with tempfile.TemporaryDirectory() as t:
            g = GitCli(Path(t))
            p = Path(t) / "wallpapers.toml"
            self.assertIsNone(g.index_blob(p))
            self.assertIsNone(g.blob_ids([p]))


if __name__ == "__main__":
    unittest.main()
