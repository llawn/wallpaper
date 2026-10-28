"""Git adapter: shells out to `git`, routing each path to the (sub)module repo that owns it."""
from __future__ import annotations

import os
import posixpath
import re
import subprocess
import warnings
from pathlib import Path, PurePosixPath
from typing import Callable

from ..domain.models import Change
from ..errors import GitError, PathOutsideRepo

FILE_PATHSPECS = 64  # file_states: up to this many paths per repo are passed to git one by one, more by folder
_PATH_LINE = re.compile(r"^\s*path\s*=\s*(.*?)\s*$", re.M)


class GitCli:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._warned = False
        self._work_tree: bool | None = None
        self._subs: tuple[tuple, list[str]] | None = None  # (stamp, submodule paths): see _submodules
        # called with a repo's index file before each command that writes it (the unit of work snapshots it)
        self.on_index_write: Callable[[Path], None] | None = None
        self._index_files: dict[Path, Path] = {}

    # -- plumbing ---------------------------------------------------------
    def _run(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        try:
            # read-only commands (status) must not take .git/index.lock: a concurrent `git add` would fail
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        except OSError as e:
            raise GitError(f"cannot run git: {e}") from e
        if check and r.returncode != 0:
            raise GitError(r.stderr.strip())
        return r

    def _enabled(self, mutating: bool = True) -> bool:
        if self._work_tree is None:
            try:
                r = self._run(self.root, "rev-parse", "--is-inside-work-tree", check=False)
                self._work_tree = r.returncode == 0 and r.stdout.strip() == "true"
            except GitError:
                self._work_tree = False
        if not self._work_tree and mutating and not self._warned:
            self._warned = True
            warnings.warn(f"{self.root} is not a git work tree; git operations are skipped",
                          UserWarning, stacklevel=3)
        return self._work_tree

    def _submodule_stamp(self) -> tuple:
        """Changes whenever the answer of `git submodule status --recursive` can: a .gitmodules file changes, or a
        submodule it lists gets or loses its .git (init, deinit), recursively. A few stats, no git process."""
        stamp: list[tuple] = []
        todo = [self.root]
        while todo:
            repo = todo.pop()
            gm = repo / ".gitmodules"
            try:
                st = os.stat(gm)
                text = gm.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stamp.append((str(repo),))
                continue
            stamp.append((str(repo), st.st_mtime_ns, st.st_size, st.st_ctime_ns))
            for m in _PATH_LINE.finditer(text):
                sub = repo / m.group(1).strip('"')
                initialised = os.path.exists(sub / ".git")
                stamp.append((str(sub), initialised))
                if initialised:
                    todo.append(sub)
        return tuple(stamp)

    def _submodules(self) -> list[str]:
        """Initialised submodule paths relative to root (POSIX), including nested ones.

        Cached while _submodule_stamp() is unchanged: the command takes ~50 ms, and every routed git call needs it
        (an edit makes four or five)."""
        stamp = self._submodule_stamp()
        if self._subs is not None and self._subs[0] == stamp:
            return list(self._subs[1])
        out = self._list_submodules()
        self._subs = (stamp, out)
        return list(out)

    def _list_submodules(self) -> list[str]:
        r = self._run(self.root, "submodule", "status", "--recursive", check=False)
        out = []
        for line in r.stdout.splitlines():
            if not line or line[0] == "-":  # not initialised: no work tree
                continue
            rest = line[1:].split(" ", 1)
            if len(rest) < 2:
                continue
            path = rest[1]
            if path.endswith(")") and " (" in path:
                path = path[: path.rindex(" (")]
            out.append(path)
        return out

    def _route(self, path: Path, subs: list[str]) -> tuple[str, str]:
        """Return (repo dir relative to root POSIX ('.' for main), path relative to that repo)."""
        p = Path(path)
        p = (p.parent.resolve() / p.name) if p.name else p.resolve()
        try:
            rel = PurePosixPath(p.relative_to(self.root).as_posix())
        except ValueError:
            raise PathOutsideRepo(f"{path} is outside {self.root}") from None
        best = "."
        for s in subs:
            sp = PurePosixPath(s)
            if (rel == sp or sp in rel.parents) and (best == "." or len(s) > len(best)):
                best = s
        inner = rel if best == "." else rel.relative_to(best)
        return best, str(inner)

    def _group(self, paths: list[Path]) -> dict[str, list[str]]:
        subs = self._submodules()
        groups: dict[str, list[str]] = {}
        for p in paths:
            repo, rel = self._route(p, subs)
            groups.setdefault(repo, []).append(rel)
        return groups

    def _repo_dir(self, repo: str) -> Path:
        return self.root if repo == "." else self.root / repo

    def _before_write(self, repo_dir: Path) -> None:
        if self.on_index_write is None:
            return
        index = self._index_files.get(repo_dir)
        if index is None:
            out = self._run(repo_dir, "rev-parse", "--git-path", "index").stdout.strip()
            index = self._index_files[repo_dir] = (repo_dir / out).resolve()
        self.on_index_write(index)

    def _each(self, paths: list[Path], *cmd: str) -> None:
        if not paths or not self._enabled():
            return
        for repo, rels in self._group(paths).items():
            self._before_write(self._repo_dir(repo))
            self._run(self._repo_dir(repo), *cmd, "--", *rels)

    # -- Vcs --------------------------------------------------------------
    def stage_add(self, paths: list[Path], force: bool = False) -> None:
        self._each(paths, "add", *(["-f"] if force else []))

    def stage_rm(self, paths: list[Path]) -> None:
        self._each(paths, "rm", "-q", "-f")  # -f: also files added or edited since the last commit

    def untrack(self, paths: list[Path]) -> None:
        self._each(paths, "rm", "-q", "--cached")

    def stage_mv(self, src: Path, dst: Path) -> None:
        if not self._enabled():
            return
        subs = self._submodules()
        (r1, s), (r2, d) = self._route(src, subs), self._route(dst, subs)
        if r1 != r2:
            raise GitError(f"cannot move across repositories: {r1} -> {r2}")
        self._before_write(self._repo_dir(r1))
        self._run(self._repo_dir(r1), "mv", "--", s, d)

    def is_tracked(self, path: Path) -> bool:
        if not self._enabled(mutating=False):
            return False
        repo, rel = self._route(path, self._submodules())
        r = self._run(self._repo_dir(repo), "ls-files", "--error-unmatch", "--", rel, check=False)
        return r.returncode == 0

    def staged(self) -> list[Path]:
        """Absolute paths staged in the main repo (added, copied, modified, renamed; not deleted)."""
        if not self._enabled(mutating=False):
            return []
        r = self._run(self.root, "-c", "core.quotepath=off", "diff", "--cached", "--name-only", "-z",
                      "--diff-filter=ACMR")
        return [self.root / p for p in r.stdout.split("\0") if p]

    def status(self) -> list[Change]:
        if not self._enabled(mutating=False):
            return []
        subs = self._submodules()
        changes: list[Change] = []
        for repo in [".", *subs]:
            r = self._run(self._repo_dir(repo), "-c", "core.quotepath=off",
                          "status", "--porcelain=v1", "-z")
            entries = r.stdout.split("\0")
            i = 0
            while i < len(entries):
                e = entries[i]
                i += 1
                if len(e) < 4:
                    continue
                code, path = e[:2], e[3:]
                if code[0] in "RC":
                    i += 1  # skip the original path
                changes.append(Change(repo, code.strip(), path))
        return changes

    def file_states(self, paths: list[Path]) -> dict[Path, str]:
        """Spec git state per absolute path: committed, staged, modified, untracked, ignored, deleted, conflict."""
        if not paths:
            return {}
        if not self._enabled(mutating=False):
            return {Path(p): "untracked" for p in paths}
        subs = self._submodules()
        out: dict[Path, str] = {}
        by_repo: dict[str, list[tuple[Path, str]]] = {}
        routed: dict[str, tuple[str, str]] = {}  # each folder resolved and routed once, not each file
        for p in paths:  # strings, not Path operations: 10,000 paths per folder
            parent, _, name = os.fspath(p).rpartition(os.sep)
            r = routed.get(parent)
            if r is None:
                r = routed[parent] = self._route(Path(parent).resolve(), subs)
            repo, rel_dir = r
            by_repo.setdefault(repo, []).append((p, name if rel_dir == "." else f"{rel_dir}/{name}"))
        for repo, items in by_repo.items():
            # their folders as pathspecs, not one pathspec per file: git matches every pathspec against every
            # path, so 10,000 file pathspecs take seconds where the ~20 folders holding them take milliseconds; a
            # few files (after an operation) are asked about directly, so git does not scan their whole folders
            if len(items) <= FILE_PATHSPECS:
                specs = sorted({rel for _, rel in items})
            else:
                specs = sorted({posixpath.dirname(rel) or "." for _, rel in items})
            r = self._run(self._repo_dir(repo), "--literal-pathspecs", "-c", "core.quotepath=off", "status",
                          "--porcelain=v1", "-z", "--ignored=matching", "--", *specs)
            codes: dict[str, str] = {}
            entries = r.stdout.split("\0")
            i = 0
            while i < len(entries):
                e = entries[i]
                i += 1
                if len(e) < 4:
                    continue
                if e[0] in "RC":
                    i += 1  # skip the original path
                codes[e[3:]] = e[:2]
            tracked = set(self._run(self._repo_dir(repo), "--literal-pathspecs", "-c", "core.quotepath=off",
                                    "ls-files", "-z", "--",
                                    *specs).stdout.split("\0"))
            collapsed = [(d, c) for d, c in codes.items() if d.endswith("/")]
            for path, rel in items:
                code = codes.get(rel)
                if code is None and collapsed:  # a collapsed directory entry ("ign/") covers everything below it
                    code = next((c for d, c in collapsed if rel.startswith(d)), None)
                out[path] = _state(code, rel in tracked)
        return out


_CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _state(code: str | None, tracked: bool) -> str:
    if code is None:
        return "committed" if tracked else "untracked"
    if code == "!!":
        return "ignored"
    if code == "??":
        return "untracked"
    if code in _CONFLICTS:
        return "conflict"
    x, y = code
    if "D" in code:
        return "deleted"
    if y == "M":
        return "modified"
    if x in "AMRC" and y == " ":
        return "staged"
    return "modified"
