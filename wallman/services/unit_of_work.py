"""All-or-nothing batches of file and wallpapers.toml changes."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..domain.rules import TOML_NAME
from ..ports import Catalogue, ImageStore, Vcs

__all__ = ["TOML_NAME", "UnitOfWork"]


# One lock for the whole process (single user, one server). Re-entrant: nested units join the outer one.
_LOCK = threading.RLock()
WRITE_LOCK = _LOCK  # every repository write (units of work, index-only git actions) runs under this one lock
_depth = 0
# (path, bytes before) -- None means "did not exist"; guarded by _LOCK
_undo: list[tuple[Path, bytes | None]] = []
_outer: "UnitOfWork | None" = None  # the outermost open unit; guarded by _LOCK
_owner: int | None = None  # thread holding the open unit; guarded by _LOCK


def in_unit() -> bool:
    """Whether the calling thread is inside a unit of work."""
    return _depth > 0 and _owner == threading.get_ident()


def _restore(path: Path, data: bytes | None) -> None:
    if data is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".restore-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def record_index(path: Path) -> None:
    """The Vcs calls this before it writes a git index file (git add/mv/rm): inside a unit the index is snapshotted
    once, so a rollback restores it byte for byte with the recorded files (Ruling v2-17). Outside a unit: no-op."""
    if not in_unit() or _outer is None:
        return
    path = Path(path)
    if path in _outer._indexes:
        return
    _outer._indexes.add(path)
    try:
        before: bytes | None = path.read_bytes()
    except FileNotFoundError:
        before = None
    _undo.append((path, before))


class UnitOfWork:
    """`with UnitOfWork(...) as uow: uow.record_file(p); ...change p...`

    On an exception every recorded file and wallpapers.toml is restored byte for byte (reverse order) and the
    exception is re-raised. When the outermost block exits cleanly, `on_commit` runs once. Restoration writes the
    snapshotted bytes directly, so it is never blocked by the catalogue's structural refusal.
    """

    def __init__(self, catalogue: Catalogue, store: ImageStore, vcs: Vcs, on_commit: Callable[["UnitOfWork"], None]) -> None:
        self.catalogue = catalogue
        self.store = store
        self.vcs = vcs
        self.on_commit = on_commit
        self._mark = 0
        self._stage = True
        self._touched: set[str] = set()
        self._paths: set[Path] = set()
        self._deferred: list[Path] = []
        self._force = False
        self._indexes: set[Path] = set()

    @property
    def stage(self) -> bool:
        """Whether on_commit may stage generated files; nested units share the outermost unit's value."""
        return (_outer or self)._stage

    @stage.setter
    def stage(self, value: bool) -> None:
        (_outer or self)._stage = value

    @property
    def touched(self) -> set[str]:
        """Collection folders (relative to the collection root) of every recorded file or collection, nested
        units included: what on_commit hands to the library index."""
        return (_outer or self)._touched

    @property
    def touched_paths(self) -> set[Path]:
        """Every recorded file (nested units included): the only files whose git state an operation can change,
        so the index asks git about these alone after the commit."""
        return (_outer or self)._paths

    def __enter__(self) -> "UnitOfWork":
        global _depth, _outer, _owner
        _LOCK.acquire()
        _depth += 1
        if _depth == 1:
            _outer = self
            _owner = threading.get_ident()
            self._stage = True
            self._touched = set()
            self._paths = set()
            self._deferred = []
            self._force = False
            self._indexes = set()
        self._mark = len(_undo)
        return self

    def record_file(self, path: Path) -> None:
        path = Path(path)
        try:
            before: bytes | None = path.read_bytes()
        except FileNotFoundError:
            before = None
        _undo.append((path, before))
        self.touched_paths.add(path)
        self._touch(path)

    def stage_add(self, paths: list[Path], force: bool = False) -> None:
        """`git add` at commit time instead of now: on_commit can take them (take_staged) into the one `git add` it
        runs for the generated files, and whatever it leaves is added when the unit commits. Nothing is staged when
        the unit rolls back. `force`: the path may still be in .gitignore, so the whole batch is added with -f
        (harmless for the tomls and generated files, which are never ignored)."""
        outer = _outer or self
        outer._deferred.extend(Path(p) for p in paths)
        outer._force = outer._force or bool(force and paths)

    @property
    def stage_force(self) -> bool:
        """Whether the paths deferred so far must be added with -f (read it before take_staged)."""
        return (_outer or self)._force

    def take_staged(self) -> list[Path]:
        """The paths stage_add() deferred so far (they are then the caller's to stage, with -f when stage_force)."""
        outer = _outer or self
        paths, outer._deferred, outer._force = list(dict.fromkeys(outer._deferred)), [], False
        return paths

    def record_collection(self, folder: str) -> None:
        self.record_file(self.store.path(folder, TOML_NAME))

    def _touch(self, path: Path) -> None:
        root = self.store.path("", "")
        if path.parent != root and path.parent.is_relative_to(root):  # files outside images/ (README...) are not
            self.touched.add(path.parent.relative_to(root).as_posix())

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _depth, _outer, _owner
        try:
            if exc_type is None and _depth == 1:
                try:
                    self.on_commit(self)
                    force = self.stage_force
                    left = self.take_staged()
                    if left:
                        self.vcs.stage_add(left, force=force)
                except BaseException:
                    self._rollback()
                    raise
            elif exc_type is not None:
                self._rollback()
        finally:
            _depth -= 1
            if _depth == 0:
                _undo.clear()
                _outer = None
                _owner = None
            _LOCK.release()
        return False

    def _rollback(self) -> None:
        outer = _outer or self
        while len(_undo) > self._mark:
            path, data = _undo.pop()
            outer._indexes.discard(path)  # restored: a later write in the outer unit snapshots it again
            try:
                _restore(path, data)
            except OSError:
                pass  # best effort: keep restoring the rest, the original error is what the caller needs
