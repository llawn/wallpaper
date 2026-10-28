"""In-memory index of every wallpaper, so listing stays fast at 10,000+ images (spec v2 3.1).

Built once from every wallpapers.toml + file stats + header sizes + one git status. Afterwards only touched or
externally changed folders are reloaded:

- operations: external changes are picked up when the unit of work starts (check_external); UnitOfWork.on_commit
  -> refresh(touched folders, written files) reloads those folders incrementally; the git states of the written
  files and changed entries are refreshed in background threads (generation-guarded, outside every lock), and the
  operation's response waits for them with wait_for_operation_git() so the client never reads pre-operation states;
- external edits: ensure_fresh() stats each collection folder (its wallpapers.toml and the directory itself) at
  most once per FRESH_SECONDS and reloads the folders whose stat changed, appeared or vanished; git states are
  refreshed at most once per GIT_SECONDS.

Validation issues are computed per folder on reload, never per request (and only for the entries that changed);
/api/status counts them instead of running the validator. One RLock guards the index; entries are immutable and
every query returns new lists, so callers never see a half-updated index.
"""
from __future__ import annotations

import dataclasses
import gc
import itertools
import math
import operator
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal

from ..domain import rules
from ..domain.buckets import ratio_bucket
from ..domain.models import Issue, Ref, Resolution, Wallpaper
from ..domain.rules import TOML_NAME
from ..domain.years import YearSpan
from ..errors import InvalidImage, NotFound
from ..domain.licenses import is_shareable
from ..ports import Catalogue, ImageStore, ImageTool, Vcs
from .publish import EntryStates, wallpaper_state

FRESH_SECONDS = 2.0  # stat check of the collection folders at most this often
GIT_SECONDS = 1.0  # git status at most this often (always right after an operation)
DEEP_SECONDS = 10.0  # re-stat of the image files of a folder being listed at most this often (in-place overwrites)
GIT_WAIT = 2.0  # an operation's response waits at most this long for its git states
UNKNOWN_GIT = "untracked"  # until the first git status answers (or when git fails)

Sort = Literal["name", "year", "added"]


@dataclass(frozen=True)
class IndexedEntry:
    ref: Ref
    wallpaper: Wallpaper
    ratio_bucket: str  # of the size measured on disk (the entry's resolution when the file is missing)
    search_text: str  # lowercased title, artist, file and tags, one per line
    year_span: YearSpan | None
    git_state: str
    issues: tuple[Issue, ...]
    mtime_ns: int  # 0 when the file is missing
    size: int = 0  # bytes on disk; 0 when the file is missing
    path: str = ""  # absolute path, resolved like ImageStore.path (a list page builds items without touching disk)
    external: bool = False  # the entry's folder is an external collection


@dataclass(frozen=True)
class Query:
    folder: str | None = None  # path prefix: "painting" matches painting/*
    q: str = ""  # every whitespace-separated term must appear in search_text
    tags: tuple[str, ...] = ()  # all of them
    license: str | None = None
    ratio: str | None = None
    year: YearSpan | None = None  # overlap
    issues: bool = False  # only entries with issues
    sort: Sort = "name"
    offset: int = 0
    limit: int = 200


@dataclass(frozen=True)
class Page:
    items: list[IndexedEntry]
    total: int
    offset: int


# (toml mtime_ns, toml size, toml ctime_ns, folder dir mtime_ns); None when there is no (stat-able) wallpapers.toml
_Key = tuple[int, int, int, int] | None


@dataclass(frozen=True)
class _Folder:
    key: _Key
    entries: tuple[IndexedEntry, ...]  # wallpapers.toml order
    paths: tuple[Path, ...]  # absolute path of each entry, for git status
    issues: tuple[Issue, ...]  # not attached to an entry: folder-level, unlisted images, structural
    medium: str = ""
    movement: str = ""
    external: bool = False
    by_file: dict[str, IndexedEntry] = dataclasses.field(default_factory=dict)
    errors: int = 0  # every issue of the folder (entries and folder-level), what the validator would count
    warnings: int = 0
    base: str = ""  # the folder's resolved path when it was loaded
    links: bool = False  # some image is a symlink

    @staticmethod
    def make(key: _Key, entries: tuple[IndexedEntry, ...], paths: tuple[Path, ...], issues: tuple[Issue, ...],
             medium: str = "", movement: str = "", external: bool = False, base: str = "",
             links: bool = False) -> "_Folder":
        errors = warnings = 0
        for i in itertools.chain(issues, *(e.issues for e in entries if e.issues)):
            if i.level == "error":
                errors += 1
            else:
                warnings += 1
        return _Folder(key, entries, paths, issues, medium, movement, external, {e.ref.file: e for e in entries},
                       errors, warnings, base, links)


def _search_text(w: Wallpaper) -> str:
    return f"{w.title}\n{w.artist}\n{w.file}\n{' '.join(w.tags)}".lower()


def _in_folder(folder: str, prefix: str) -> bool:
    return not prefix or folder == prefix or folder.startswith(prefix + "/")


def _name_key(e: IndexedEntry) -> tuple:
    return (e.wallpaper.title.casefold(), e.ref.file)


def _year_key(e: IndexedEntry) -> tuple:
    return (e.year_span.start if e.year_span else math.inf, e.wallpaper.title.casefold(), e.ref.file)


class LibraryIndex:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool | None, vcs: Vcs,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """`tool` measures image sizes from their headers; None trusts each entry's resolution (no file reads)."""
        self.catalogue, self.store, self.tool, self.vcs, self.clock = catalogue, store, tool, vcs, clock
        self._lock = threading.RLock()
        self._folders: dict[str, _Folder] = {}
        self._all: tuple[IndexedEntry, ...] = ()  # every entry, folders sorted, toml order inside
        self._tag_counts: dict[str, int] = {}
        self._tag_members: dict[str, set[int]] = {}
        self._orders: dict[str, tuple[IndexedEntry, ...]] = {}  # sort -> all entries in that order (lazy)
        # folder -> file -> (mtime_ns, size, measured size or None): unchanged files are not identified again
        self._sizes: dict[str, dict[str, tuple[int, int, Resolution | None]]] = {}
        self._git: dict[Ref, str] = {}  # last git status answer
        self._git_gen = 0  # bumped by every synchronous git refresh: an older background answer is dropped
        self._git_thread: threading.Thread | None = None  # periodic background status (single flight)
        self._op_threads: list[threading.Thread] = []  # post-operation statuses still running
        self._op_pending: dict[Ref, Path] = {}  # entries whose post-operation git states have not landed yet
        self._changed: set[Ref] = set()  # refs (re)built by the reloads since the last operation's git refresh
        self._op_floor = 0  # post-operation answers of an older generation than this are dropped
        self._op_outstanding = 0  # post-operation statuses of the current operation still running
        self._op_done = threading.Event()  # set when no post-operation git status is pending
        self._op_done.set()
        self._deep_at: dict[str, float] = {}  # folder -> last background re-stat of its images
        self._deep_thread: threading.Thread | None = None
        self._deep_queue: list[str] = []  # folders waiting for _restat_worker
        self._warmed = False
        self._stat_at = -math.inf
        self._git_at = -math.inf
        self.git_error: str | None = None  # last git status failure, if any (states then keep their old values)
        self._entry_states = EntryStates(catalogue, vcs)  # publish model: where each entry stands in the TOMLs

    # -- loading -----------------------------------------------------------
    @property
    def warmed(self) -> bool:
        return self._warmed

    def warm(self) -> None:
        """(Re)build everything: walk the collection folders, load them all, one git status."""
        with self._lock:
            now = self.clock()
            folders = set(self.store.collection_folders())
            self._folders = {}
            self._sizes = {}
            self._reload(folders)
            self._warmed = True
            self._stat_at = now
            self._refresh_git(now)
            for sort in ("name", "year", "added"):  # the first requests should not pay for sorting
                self._order(sort)
        if len(self._all) > 1000:  # the parsed TOMLs are garbage now: collect here, not in the first requests
            gc.collect()

    def refresh(self, folders: set[str] | None = None, paths: Iterable[Path] | None = None) -> None:
        """Reload exactly `folders` (None: everything), then refresh git states in the background. No-op before the
        first warm-up: the warm-up will read everything anyway, and CLI commands must not pay for it.

        `paths`: the files the operation wrote (UnitOfWork.touched_paths). Their folders are reloaded lightly (see
        _read_folder), and git is asked about those files (started before the reload, so it runs meanwhile) and
        about the entries the reload rebuilt (new, renamed or edited entries), not about the whole folder; the
        periodic background status still covers the rest. Without `paths`, every entry of `folders`.

        Called from UnitOfWork.on_commit, i.e. under the write lock: the git status runs in threads outside every
        lock, so the next write does not wait for it. wait_for_operation_git() waits for their answers."""
        with self._lock:
            if not self._warmed:
                return
            if folders is None:
                self.warm()
                return
            folders = set(folders)
            self._changed = set()
            if paths is None:
                self._reload(folders)
                self._git_after_operation(self._pairs(
                    e.ref for f in folders if f in self._folders for e in self._folders[f].entries))
                return
            root = self.store.path("", "")
            written: dict[str, set[str]] = {}
            early: dict[Ref, Path] = {}
            for p in map(Path, paths):
                if p.parent.is_relative_to(root):
                    folder = p.parent.relative_to(root).as_posix()
                    written.setdefault(folder, set()).add(p.name)
                    if p.suffix.lower() in rules.IMAGE_EXTS:
                        early[Ref(folder, p.name)] = p
            self._git_after_operation(early)  # applied after the reload (it needs this lock): unindexed refs dropped
            self._reload(folders, written)
            extra = self._pairs(r for r in self._changed if r not in early)
            if extra:
                self._git_after_operation(extra, same_operation=True)
            self._restat(f for f in folders if f in self._folders)  # files the operation did not write, after it

    def refresh_git(self, refs: Iterable[Ref]) -> None:
        """Refresh the git states of `refs` only (after an index-only git action: no file or TOML changed), in the
        background like refresh()."""
        with self._lock:
            if self._warmed:
                self._git_after_operation(self._pairs(refs))

    def _pairs(self, refs: Iterable[Ref]) -> dict[Ref, Path]:
        """ref -> path for the indexed ones among `refs`."""
        out: dict[Ref, Path] = {}
        root = self.store.path("", "")
        for r in refs:
            data = self._folders.get(r.folder)
            if data is not None and r.file in data.by_file:
                out[r] = root / r.folder / r.file  # as in _Folder.paths: collection folders are never symlinks
        return out

    def _git_after_operation(self, pairs: dict[Ref, Path], same_operation: bool = False) -> None:
        """Caller holds the lock. Start a git status of `pairs` in a thread.

        A new operation raises the floor: answers of earlier operations still in flight are dropped (they could be
        older than this operation's writes), so its status also covers the entries those were for. With
        `same_operation`, an extra status of the current operation: both answers apply."""
        self._git_gen += 1
        gen = self._git_gen
        if same_operation:
            self._op_pending.update(pairs)
        else:
            self._op_floor = gen
            self._op_outstanding = 0
            pairs = {**self._op_pending, **pairs}
            self._op_pending = dict(pairs)
        self._op_outstanding += 1
        self._op_done.clear()
        refs, paths = list(pairs), list(pairs.values())

        def run() -> None:
            git = None
            try:
                git = self._fetch_states(refs, paths) if refs else {}
            except Exception as e:  # noqa: BLE001 -- _fetch_states already guards git; this is a bug, say so
                self._thread_failed(e)
            finally:
                with self._lock:
                    try:
                        if gen >= self._op_floor and git is not None:
                            self._apply_git(git)
                    except Exception as e:  # noqa: BLE001
                        self._thread_failed(e)
                    finally:  # whatever happened, waiters are released as soon as this status is over
                        if gen >= self._op_floor:
                            self._op_outstanding -= 1
                            if self._op_outstanding <= 0:
                                self._op_pending.clear()
                                self._op_done.set()

        t = threading.Thread(target=run, name="wallman-git-after-op", daemon=True)
        self._op_threads = [x for x in self._op_threads if x.is_alive()] + [t]
        t.start()

    def _thread_failed(self, e: Exception) -> None:
        self.git_error = f"{type(e).__name__}: {e}"
        print(f"wallman: git state refresh failed: {self.git_error}", file=sys.stderr)

    def wait_for_operation_git(self, timeout: float = GIT_WAIT) -> bool:
        """Wait (bounded) until the git states of the last operations have landed; False on timeout."""
        return self._op_done.wait(timeout)

    def ensure_fresh(self, folder: str | None = None) -> None:
        """Warm on first use; then pick up external changes (stat-based, cached FRESH_SECONDS) and git states.

        The periodic git status runs in a background thread (a request never waits for git: tens to hundreds of
        ms at 10,000 files); its answer is applied when it arrives.

        `folder` (the path prefix being listed; None: every folder): its image files are re-stat-ed in a background
        thread at most every DEEP_SECONDS per folder, so an image overwritten in place outside wallman (which
        changes neither the TOML nor the directory) shows up without any operation.
        """
        with self._lock:
            if not self._warmed:
                self.warm()
                return
            now = self.clock()
            changed = False
            if now - self._stat_at >= FRESH_SECONDS:
                changed = self._reload_stale(now)
            if changed or now - self._git_at >= GIT_SECONDS:
                self._refresh_git_background(now)
            prefix = (folder or "").strip("/")
            due = [f for f in self._folders
                   if _in_folder(f, prefix) and now - self._deep_at.get(f, -math.inf) >= DEEP_SECONDS]
            if due:
                self._restat(due)

    def _restat(self, folders: Iterable[str]) -> None:
        """Caller holds the lock. Queue `folders` for a background re-stat of their images (one worker thread):
        a folder where a file's mtime or size differs from the last load is reloaded, rebuilding those entries.
        This is how an image overwritten in place outside wallman (same name: neither the TOML nor the directory
        changes) is seen: right after any operation on its folder, and at most DEEP_SECONDS after a listing."""
        now = self.clock()
        for f in folders:
            self._deep_at[f] = now
            if f not in self._deep_queue:
                self._deep_queue.append(f)
        if self._deep_thread is None or not self._deep_thread.is_alive():
            self._deep_thread = threading.Thread(target=self._restat_worker, name="wallman-restat", daemon=True)
            self._deep_thread.start()

    def _restat_worker(self) -> None:
        while True:
            with self._lock:
                if not self._deep_queue:
                    return
                folder = self._deep_queue.pop(0)
                known = self._sizes.get(folder)
                data = self._folders.get(folder)
            try:
                if known is None or data is None or not data.base:
                    continue
                current = {f: (st.st_mtime_ns, st.st_size) for f, st, _ in self._scan(Path(data.base))}  # no lock
                differ = {f for f, v in current.items() if f not in known or known[f][:2] != v}
                if not differ and current.keys() == known.keys():
                    continue
                with self._lock:
                    if folder in self._folders:
                        self._reload({folder}, {folder: differ})  # incremental: re-stats and rebuilds those only
                        self._refresh_git_background(self.clock())  # a rewritten tracked file is now modified
            except Exception as e:  # noqa: BLE001 -- a background thread has nobody to raise to
                print(f"wallman: background re-stat of {folder} failed: {type(e).__name__}: {e}", file=sys.stderr)

    def _reload_stale(self, now: float) -> set[str]:
        """Caller holds the lock. Reload the folders whose freshness key changed, appeared or vanished; returns
        them."""
        self._stat_at = now
        current = set(self.store.collection_folders())
        stale = (current ^ set(self._folders)) | {
            f for f in current & set(self._folders) if self._key(f) != self._folders[f].key}
        if stale:
            self._reload(stale)
        return stale

    def check_external(self) -> set[str]:
        """Pick up changes made outside wallman now (no FRESH_SECONDS window): called when an operation starts, so
        its refresh can trust the previous load for every file it did not write. No-op before the warm-up.
        Returns the folders reloaded (the operation regenerates their index wallpapers.toml)."""
        with self._lock:
            stale = self._reload_stale(self.clock()) if self._warmed else set()
            if stale:
                self._refresh_git_background(self.clock())  # their git states, as ensure_fresh would
            return stale

    def _key(self, folder: str) -> _Key:
        """Freshness key of a folder: (wallpapers.toml mtime_ns, size, ctime_ns, folder directory mtime_ns).

        ctime catches a permission fix (chmod) on an unreadable wallpapers.toml, which changes nothing else.

        An image overwritten in place (same name, no rename or new file) changes neither the TOML nor the directory
        mtime, so this key misses it: the next operation on the folder (every image is stat-ed) or the background
        re-stat of a folder being listed (ensure_fresh, at most every DEEP_SECONDS) picks it up.
        """
        base = self.store.path(folder, "")
        try:
            toml = os.stat(base / TOML_NAME)
            return (toml.st_mtime_ns, toml.st_size, toml.st_ctime_ns, os.stat(base).st_mtime_ns)
        except OSError:  # missing, or not even stat-able: the reload decides what the folder is
            return None

    def _reload(self, folders: Iterable[str], written: dict[str, set[str]] | None = None) -> None:
        """`written`: after an operation, folder -> names of the files it wrote there (see _read_folder)."""
        now = self.clock()
        for folder in folders:
            self._deep_at[folder] = now  # a full load stats every image; after an operation _restat follows
            data = self._load_folder(folder, None if written is None else written.get(folder, set()))
            if data is None:
                self._drop(folder)
            else:
                self._folders[folder] = data
        self._rebuild()

    def _drop(self, folder: str) -> None:
        self._folders.pop(folder, None)
        self._sizes.pop(folder, None)

    def _load_folder(self, folder: str, written: set[str] | None = None) -> _Folder | None:
        """Everything about one folder, or None when it is not a collection (any more).

        Never raises for a broken folder: an unparsable, undecodable or unreadable wallpapers.toml (or any other
        failure while reading the folder) becomes a folder with no entries and a folder-level issue, so one bad
        file can neither break every query nor stop the warm-up nor roll back an operation from on_commit.
        """
        key = self._key(folder)  # taken before reading: a change during the load shows up as stale next time
        try:
            return self._read_folder(folder, key, written)
        except NotFound:
            return None
        except Exception as e:  # noqa: BLE001 -- reported as an issue of that folder, see the docstring
            try:
                issues = tuple(self.catalogue.structural_issues(folder))
            except Exception:  # noqa: BLE001
                issues = ()
            if not issues:
                issues = (Issue(folder, None, "unreadable", f"cannot index {folder}: {e}", "error"),)
            return _Folder.make(key, (), (), issues, folder.split("/")[0], "", self.store.is_external(folder))

    def _read_folder(self, folder: str, key: _Key, written: set[str] | None = None) -> _Folder:
        """Load one folder. An entry of the previous load is reused as is (no rule check, no new object) when its
        Wallpaper, its file's stat and the folder fields the rules read are unchanged: after an edit of one entry in
        a 10,000-entry folder only that entry is checked again. The refs that were not reused are added to
        self._changed (what an operation's git refresh asks about).

        `written` (after an operation: the names of the files it wrote in this folder): the incremental path
        (_read_light) is tried first. Every image is stat-ed either way, so an image overwritten in place outside
        wallman is seen by the next operation on its folder."""
        coll = self.catalogue.load(folder)
        base = self.store.path(folder, "")
        sbase = str(base)
        prev = self._folders.get(folder)
        known = self._sizes.get(folder)
        if written is not None and known is not None and prev is not None and prev.base == sbase:
            light = self._read_light(folder, key, coll, base, prev, known, written)
            if light is not None:
                return light
        sizes: dict[str, Resolution] = {}
        stats: dict[str, tuple[int, int]] = {}  # file -> (mtime_ns, size)
        bad: dict[str, Issue] = {}
        declared = {w.file: w.resolution for w in coll.wallpapers}
        cache = known or {}
        fresh: dict[str, tuple[int, int, Resolution | None]] = {}  # only files still on disk: no resurrection
        links: dict[str, str] = {}  # symlinked images: their resolved path (ImageStore.path resolves)
        tool = self.tool
        escaped = False  # a symlink resolving outside the collection root: treated as absent (never read)
        root = os.path.realpath(self.store.path("", "")) + os.sep
        for f, st, link in self._scan(base):
            if link:
                real = os.path.realpath(os.path.join(sbase, f))
                if not real.startswith(root):  # ImageStore.path refuses it too
                    escaped = True
                    continue
                links[f] = real
            mt, nb = st.st_mtime_ns, st.st_size
            stats[f] = (mt, nb)
            if tool is None:
                size = declared.get(f)
                fresh[f] = (mt, nb, None)
            else:
                cached = cache.get(f)
                if cached is not None and cached[0] == mt and cached[1] == nb:
                    size = cached[2]
                    fresh[f] = cached
                else:
                    size = self._identify(os.path.join(sbase, f))  # a str: no Path built per file
                    fresh[f] = (mt, nb, size)
                if size is None:
                    bad[f] = Issue(folder, f, "invalid-image", "cannot read the image size", "error")
            if size is not None:
                sizes[f] = size
        self._sizes[folder] = fresh
        on_disk = set(stats) | {w.original for w in coll.wallpapers
                                if w.original and os.path.isfile(os.path.normpath(base / w.original))}
        structural = self.catalogue.structural_issues(folder)
        loose: list[Issue] = []
        by_file: dict[str, list[Issue]] = {}
        for i in structural:
            if i.file in declared:
                by_file.setdefault(i.file, []).append(i)
            else:
                loose.append(i)
        loose += rules.collection_issues(coll)
        same = prev is not None and (prev.medium, prev.external, prev.base) == (coll.medium, coll.external, sbase)
        reusable = prev.by_file if same and not links and not escaped and not prev.links else {}
        prev_entries, prev_paths = (prev.entries, prev.paths) if reusable else ((), ())
        n_prev = len(prev_entries)
        entries = []
        paths = []
        external = coll.external
        prefix = sbase + os.sep
        for k, w in enumerate(coll.wallpapers):
            f = w.file
            mt, nb = stats.get(f, (0, 0))
            e = reusable.get(f)
            # reuse: same Wallpaper, same file stat, nothing about the folder that the rules read changed (the
            # folder path, medium, external, no symlinks: then the entry's path is still prefix + f)
            if (e is not None and (e.wallpaper is w or e.wallpaper == w) and e.mtime_ns == mt and e.size == nb
                    and not w.original and f not in by_file):
                # an equal Wallpaper from a new parse replaces the old object: later reloads compare by identity
                entries.append(e if e.wallpaper is w else dataclasses.replace(e, wallpaper=w))
                paths.append(prev_paths[k] if k < n_prev and prev_entries[k] is e else base / f)
                continue
            ref = Ref(folder, f)
            issues = by_file.get(f, []) + rules.entry_issues(coll, w, on_disk, sizes)
            if f in bad:
                issues.append(bad[f])
            path = links.get(f) or (prefix + f if "/" not in f else self._path(folder, f))
            entries.append(IndexedEntry(
                ref=ref, wallpaper=w, ratio_bucket=ratio_bucket(sizes.get(f, w.resolution)),
                search_text=_search_text(w), year_span=w.year, git_state=self._git.get(ref, UNKNOWN_GIT),
                issues=tuple(issues), mtime_ns=mt, size=nb, path=path, external=external))
            paths.append(base / f)
            self._changed.add(ref)
        loose += rules.unlisted_issues(coll, on_disk)
        loose += [i for f, i in bad.items() if f not in declared]
        return _Folder.make(key, tuple(entries), tuple(paths), tuple(loose), coll.medium, coll.movement, external,
                            sbase, bool(links) or escaped)

    def _read_light(self, folder: str, key: _Key, coll, base: Path, prev: _Folder,
                    known: dict[str, tuple[int, int, Resolution | None]], written: set[str]) -> _Folder | None:
        """_read_folder after an operation, when the entries are the previous ones in the same order with a few
        replaced (an edit, a rename): the folder is listed, only `written` and new names are stat-ed, and only the
        replaced entries and those whose file was written, appeared or vanished are rebuilt; the rest of the work is
        set and list operations. (The other files are re-stat-ed right after, in the background: _restat.) None when that does not apply
        (entries added, removed or moved, duplicate names, symlinks, other folder fields): the caller does the
        general reload."""
        ws = coll.wallpapers
        pe = prev.entries
        sbase = str(base)
        if (prev.links or len(pe) != len(ws) or len(prev.paths) != len(pe)
                or (prev.medium, prev.external) != (coll.medium, coll.external)):
            return None
        listing = self._scan(base, written)
        if any(link for _, _, link in listing):
            return None
        names = {name for name, _, _ in listing}
        declared = {w.file: w.resolution for w in ws}
        if len(declared) != len(ws):
            return None
        new, vanished = names - known.keys(), known.keys() - names
        fresh = dict(known)
        for f in vanished:
            del fresh[f]
        tool = self.tool
        rewritten: set[str] = set()  # new names and files whose stat changed (written by the operation or not)
        for f, st, _ in listing:
            if st is None:
                if f not in new:
                    continue  # not written by the operation: the previous stat stands until _restat
                try:
                    st = os.stat(os.path.join(sbase, f))
                except FileNotFoundError:
                    fresh.pop(f, None)
                    names.discard(f)
                    continue
            mt, nb = st.st_mtime_ns, st.st_size
            cached = known.get(f)
            if cached is not None and cached[0] == mt and cached[1] == nb:
                continue
            rewritten.add(f)
            fresh[f] = (mt, nb, None if tool is None else self._identify(os.path.join(sbase, f)))
        structural = self.catalogue.structural_issues(folder)
        by_file: dict[str, list[Issue]] = {}
        loose: list[Issue] = []
        for i in structural:
            if i.file in declared:
                by_file.setdefault(i.file, []).append(i)
            else:
                loose.append(i)
        loose += rules.collection_issues(coll)
        touched = (rewritten | vanished | by_file.keys()) & declared.keys()
        rebuild = {k for k, same in enumerate(map(operator.is_, (e.wallpaper for e in pe), ws)) if not same}
        if touched:
            rebuild |= {k for k, w in enumerate(ws) if w.file in touched}
        rebuild |= {k for k, w in enumerate(ws) if w.original}
        on_disk = set(fresh) | {w.original for w in ws if w.original and os.path.isfile(os.path.normpath(base / w.original))}
        entries, paths = list(pe), list(prev.paths)
        prefix = sbase + os.sep
        for k in sorted(rebuild):
            w = ws[k]
            f = w.file
            ref = Ref(folder, f)
            mt, nb, measured = fresh.get(f, (0, 0, None))
            size = declared.get(f) if tool is None else measured
            sizes = {f: size} if f in fresh and size is not None else {}
            issues = by_file.get(f, []) + rules.entry_issues(coll, w, on_disk, sizes)
            if tool is not None and f in fresh and measured is None:
                issues.append(Issue(folder, f, "invalid-image", "cannot read the image size", "error"))
            entries[k] = IndexedEntry(
                ref=ref, wallpaper=w, ratio_bucket=ratio_bucket(sizes.get(f, w.resolution)),
                search_text=_search_text(w), year_span=w.year, git_state=self._git.get(ref, UNKNOWN_GIT),
                issues=tuple(issues), mtime_ns=mt, size=nb,
                path=prefix + f if "/" not in f else self._path(folder, f), external=coll.external)
            paths[k] = base / f
            self._changed.add(ref)
        loose += rules.unlisted_issues(coll, on_disk)
        if tool is not None:
            loose += [Issue(folder, f, "invalid-image", "cannot read the image size", "error")
                      for f in sorted(fresh) if fresh[f][2] is None and f not in declared]
        self._sizes[folder] = fresh
        return _Folder.make(key, tuple(entries), tuple(paths), tuple(loose), coll.medium, coll.movement,
                            coll.external, sbase, False)

    @staticmethod
    def _scan(base: Path, only: set[str] | None = None) -> list[tuple[str, os.stat_result | None, bool]]:
        """(name, stat, is a symlink) of each image file in `base`, sorted: ImageStore.images() plus one stat each,
        in one directory pass (DirEntry.stat is an fstatat relative to the open directory). With `only`, the names
        outside it get None instead of a stat, symlinks excepted (see _read_light)."""
        out = []
        exts = rules.IMAGE_EXTS
        try:
            fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        except (FileNotFoundError, NotADirectoryError):
            return out
        try:
            with os.scandir(fd) as it:  # scandir of a descriptor: each stat is relative, no path walk
                for e in it:
                    name = e.name
                    dot = name.rfind(".")
                    if (dot <= 0 or name[dot:].lower() not in exts
                            or (name[0] == "." and not name[:dot].lstrip("."))):  # os.path.splitext's rule
                        continue
                    try:
                        if e.is_file():
                            link = e.is_symlink()
                            out.append((name, e.stat() if only is None or link or name in only else None, link))
                    except FileNotFoundError:  # vanished since the listing: treated as missing
                        continue
        finally:
            os.close(fd)
        out.sort()  # names are unique: the stats are never compared
        return out

    def _path(self, folder: str, file: str) -> str:
        try:
            return str(self.store.path(folder, file))
        except Exception:  # noqa: BLE001 -- a file name escaping the folder: the request for it is refused anyway
            return ""

    def _identify(self, path: str) -> Resolution | None:
        try:
            return self.tool.identify(path)
        except (InvalidImage, OSError):
            return None

    def _rebuild(self) -> None:
        self._all = tuple(e for f in sorted(self._folders) for e in self._folders[f].entries)
        members: dict[str, set[int]] = {}
        for e in self._all:
            for t in e.wallpaper.tags:
                members.setdefault(t, set()).add(id(e))
        self._tag_members = members  # tag -> id() of the entries in self._all carrying it
        self._tag_counts = {t: len(m) for t, m in members.items()}
        self._orders = {}

    def _order(self, sort: Sort) -> tuple[IndexedEntry, ...]:
        """Every entry in `sort` order, computed once per change of the index (a query then only filters)."""
        order = self._orders.get(sort)
        if order is None:
            if sort == "year":
                order = tuple(sorted(self._all, key=_year_key))
            elif sort == "added":  # newest first, ties by name
                order = tuple(sorted(sorted(self._all, key=_name_key), key=lambda e: e.mtime_ns, reverse=True))
            else:
                order = tuple(sorted(self._all, key=_name_key))
            self._orders[sort] = order
        return order

    def _snapshot(self) -> tuple[list[Ref], list[Path]]:
        """Refs and paths of every indexed entry."""
        refs: list[Ref] = []
        paths: list[Path] = []
        for data in self._folders.values():
            refs.extend(e.ref for e in data.entries)
            paths.extend(data.paths)
        return refs, paths

    def _fetch_states(self, refs: list[Ref], paths: list[Path]) -> dict[Ref, str] | None:
        """One file_states call for all the paths; None when git failed (states then keep their last value)."""
        try:
            states = self.vcs.file_states(paths)
            out = self._wallpaper_states({r: states.get(p, UNKNOWN_GIT) for r, p in zip(refs, paths)})
        except Exception as e:  # noqa: BLE001 -- git states are informative: never break listing or an operation
            self.git_error = f"{type(e).__name__}: {e}"
            return None
        self.git_error = None
        return out

    def _wallpaper_states(self, files: dict[Ref, str]) -> dict[Ref, str]:
        """Per-file image states -> per-wallpaper states (publish model): two git commands for every TOML involved
        (blob ids), blobs read once per id. External collections keep per-file states; so does everything when
        the Vcs cannot tell blob ids (not a work tree)."""
        by_folder: dict[str, list[Ref]] = {}
        for r in files:
            by_folder.setdefault(r.folder, []).append(r)
        folders = {f: self._folders.get(f) for f in by_folder}
        main = [f for f, d in folders.items() if d is not None and not d.external]
        if not main:
            return files
        tomls = {f: self.store.path(f, TOML_NAME) for f in main}
        ids = self.vcs.blob_ids(list(tomls.values()))
        if ids is None:
            return files
        out = dict(files)
        for f in main:
            data = folders[f]
            known = [(r, data.by_file[r.file].wallpaper) for r in by_folder[f] if r.file in data.by_file]
            standing = self._entry_states.folder(tomls[f], ids.get(tomls[f], (None, None)),
                                                 [(r.file, w) for r, w in known])
            for r, w in known:
                out[r] = wallpaper_state(standing[r.file], files[r], is_shareable(w.license))
        return out

    def _apply_git(self, git: dict[Ref, str]) -> None:
        """Merge an answer into the known states and replace the entries whose state changed (entries loaded after
        the snapshot keep theirs); states of refs no longer indexed are forgotten.

        Every indexed entry's git_state equals self._git.get(ref, UNKNOWN_GIT), so only the answers that differ
        from the known states touch anything: the periodic status of 10,000 files usually changes nothing."""
        known = self._git
        changed = {r: st for r, st in git.items() if known.get(r, UNKNOWN_GIT) != st}
        if not changed:
            return
        known.update(changed)
        for folder in {r.folder for r in changed}:
            data = self._folders.get(folder)
            if data is None:
                continue
            entries = tuple(dataclasses.replace(e, git_state=changed[e.ref]) if e.ref in changed else e
                            for e in data.entries)
            self._folders[folder] = _Folder.make(data.key, entries, data.paths, data.issues, data.medium,
                                                 data.movement, data.external, data.base, data.links)
        self._git = {r: st for r, st in known.items()
                     if r.folder in self._folders and r.file in self._folders[r.folder].by_file}
        self._rebuild()

    def _start_git(self, now: float) -> tuple[int, list[Ref], list[Path]]:
        """Caller holds the lock: a new full generation (older answers in flight will be dropped)."""
        self._git_gen += 1
        self._git_at = now
        return (self._git_gen, *self._snapshot())

    def _refresh_git(self, now: float) -> None:
        """Synchronous, under the lock: only the warm-up, which holds the lock throughout anyway."""
        _, refs, paths = self._start_git(now)
        git = self._fetch_states(refs, paths)
        if git is not None:
            self._apply_git(git)
        self._op_floor = self._git_gen + 1  # in-flight post-operation answers are older than this full status
        self._op_outstanding = 0
        self._op_pending.clear()
        self._op_done.set()

    def _refresh_git_background(self, now: float) -> None:
        """Caller holds the lock. At most one background git status at a time."""
        if self._git_thread is not None and self._git_thread.is_alive():
            return
        self._git_at = now
        gen = self._git_gen
        refs, paths = self._snapshot()

        def run() -> None:
            try:
                git = self._fetch_states(refs, paths)  # outside the lock: queries go on meanwhile
                with self._lock:
                    if git is not None and gen == self._git_gen:
                        self._apply_git(git)
            except Exception as e:  # noqa: BLE001 -- a background thread has nobody to raise to
                self.git_error = f"{type(e).__name__}: {e}"

        self._git_thread = threading.Thread(target=run, name="wallman-git-status", daemon=True)
        self._git_thread.start()

    def wait_for_git(self, timeout: float | None = None) -> None:
        """Wait for every background git status in flight, if any (tests, shutdown)."""
        for t in [self._git_thread, self._deep_thread, *self._op_threads]:
            if t is not None:
                t.join(timeout)

    # -- reading -----------------------------------------------------------
    def _filter(self, source: Iterable[IndexedEntry], query: Query) -> list[IndexedEntry]:
        prefix = (query.folder or "").strip("/")
        folders = {f for f in self._folders if _in_folder(f, prefix)} if prefix else None
        terms = query.q.lower().split()
        license, ratio, year = query.license, query.ratio, query.year
        tagged = self._tagged(query.tags)
        if tagged is not None and not tagged:
            return []
        out = []
        for e in source:
            if ((tagged is None or id(e) in tagged)
                    and (folders is None or e.ref.folder in folders)
                    and (license is None or e.wallpaper.license == license)
                    and (ratio is None or e.ratio_bucket == ratio)
                    and (not query.issues or e.issues)
                    and (year is None or (e.year_span is not None and e.year_span.overlaps(year)))
                    and all(t in e.search_text for t in terms)):
                out.append(e)
        return out

    def _tagged(self, tags: Iterable[str]) -> set[int] | None:
        """id() of the entries carrying every tag in `tags` (AND), or None when no tag is asked. The member sets
        are intersected smallest first, so a rare tag keeps the work small."""
        sets = sorted((self._tag_members.get(t, set()) for t in set(tags)), key=len)
        if not sets:
            return None
        out = set(sets[0])
        for s in sets[1:]:
            out &= s
            if not out:
                break
        return out

    def select(self, query: Query) -> list[IndexedEntry]:
        """Every entry matching the filters of `query`, in catalogue order (folders sorted, toml order inside);
        sort, offset and limit are ignored."""
        self.ensure_fresh()
        with self._lock:
            return self._filter(self._all, query)

    def query(self, query: Query) -> Page:
        """One page of the matching entries in `query.sort` order, plus the total; `items` is a new list."""
        self.ensure_fresh(query.folder)
        with self._lock:
            rows = self._filter(self._order(query.sort), query)
            offset = max(0, query.offset)
            return Page(items=rows[offset:offset + max(0, query.limit)], total=len(rows), offset=offset)

    def entry(self, ref: Ref) -> IndexedEntry | None:
        self.ensure_fresh()
        with self._lock:
            data = self._folders.get(ref.folder)
            return data.by_file.get(ref.file) if data else None

    def issue_counts(self) -> tuple[int, int]:
        """(errors, warnings) over the whole collection: what the validator reports, from the per-folder
        validation done at (re)load. The collection folders are stat-checked first (no FRESH_SECONDS window), so
        an edit made outside wallman is counted at once."""
        with self._lock:
            self._stat_at = -math.inf
        self.ensure_fresh()
        with self._lock:
            return (sum(d.errors for d in self._folders.values()), sum(d.warnings for d in self._folders.values()))

    def tags(self, prefix: str = "") -> list[tuple[str, int]]:
        """Tags starting with `prefix`, most used first (then alphabetical)."""
        self.ensure_fresh()
        with self._lock:
            p = prefix.lower()
            return sorted(((t, n) for t, n in self._tag_counts.items() if t.startswith(p)),
                          key=lambda tn: (-tn[1], tn[0]))

    def folders(self) -> list[str]:
        self.ensure_fresh()
        with self._lock:
            return sorted(self._folders)

    def folder_infos(self) -> list[tuple[str, str, str, bool]]:
        """(folder, medium, movement, external) of every collection, sorted by folder."""
        self.ensure_fresh()
        with self._lock:
            return [(f, d.medium, d.movement, d.external) for f, d in sorted(self._folders.items())]

    def folder_issues(self, folder: str) -> list[Issue]:
        """Issues of `folder` not attached to a listed entry (collection fields, unlisted images, broken toml)."""
        self.ensure_fresh()
        with self._lock:
            data = self._folders.get(folder)
            return list(data.issues) if data else []
