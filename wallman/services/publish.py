"""Per-wallpaper git states of the publish model (owner brief 2026-10-28).

A wallpaper is published when its entry is in the wallpapers.toml of the git index (generated: the working collection
filtered to the published entries). Its state combines where its entry is (HEAD TOML, index TOML, equal to the
working entry or not) with its image's per-file git state:

    committed ✓        entry in HEAD and the index, equal to the working entry; image committed (shareable)
    metadata ◐         same, download-only: the image stays out of git by design
    staged +           published, not committed yet (new entry, new values or image staged)
    metadata-staged ◐+ same, download-only
    modified ~         published but the working entry (or the image) differs from the index
    untracked ?        not published
    deleted −          staged for removal (entry in HEAD, not in the index)
    conflict !         the image is in a merge conflict
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..domain.models import Wallpaper
from ..ports import Catalogue, Vcs

# where an entry stands, from the TOML blobs alone
NONE, DELETED, MODIFIED, STAGED, COMMITTED = "none", "deleted", "modified", "staged", "committed"
FEW = 64  # up to this many entries asked about: rendered one by one rather than reading a blob
_IMAGE_DIFFERS = frozenset({"modified", "untracked", "ignored", "deleted"})


def wallpaper_state(entry: str, image: str, shareable: bool) -> str:
    """The wallpaper's git state from its entry's standing (NONE ... COMMITTED) and its image's file state."""
    if entry in (NONE, DELETED):  # not published: an image conflict is not this wallpaper's
        return "untracked" if entry == NONE else "deleted"
    if image == "conflict":
        return "conflict"
    if entry == MODIFIED:
        return "modified"
    if not shareable:
        return "metadata" if entry == COMMITTED else "metadata-staged"
    if image in _IMAGE_DIFFERS:
        return "modified"  # a published shareable wallpaper whose image is not (or not as it is) in the index
    return "committed" if entry == COMMITTED and image == "committed" else "staged"


def blob_id(data: bytes) -> str:
    """git's blob id of `data` (what `git hash-object` prints)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class BlobCache:
    """blob id -> {file: table text}, shared by the units of work (published sets) and the index (states): a blob is
    read and split at most once, and the blob a unit writes is primed so nobody reads it back. Bounded (LRU)."""

    SIZE = 8

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def get(self, blob: str, load) -> dict[str, str]:
        with self._lock:
            hit = self._data.pop(blob, None)
            if hit is not None:
                self._data[blob] = hit
                return hit
        hit = load()
        self.put(blob, hit)
        return hit

    def put(self, blob: str, entries: dict[str, str]) -> None:
        with self._lock:
            self._data.pop(blob, None)
            self._data[blob] = entries
            while len(self._data) > self.SIZE:
                del self._data[next(iter(self._data))]


BLOBS = BlobCache()


class EntryStates:
    """Entry standings per folder from (index blob id, HEAD blob id) and the working TOML. Blobs come from BLOBS
    (read and split once per id); an answer is kept per folder while the three blob ids stay the same, so the
    periodic full refresh of a partly published 10,000-entry folder renders nothing again."""

    def __init__(self, catalogue: Catalogue, vcs: Vcs, blobs: BlobCache = BLOBS) -> None:
        self.catalogue, self.vcs, self.blobs = catalogue, vcs, blobs
        self._answers: dict[Path, tuple[tuple, dict[str, str]]] = {}  # toml -> ((ids, work id), file -> standing)
        self._lock = threading.Lock()  # git state refreshes run in several threads

    def _blob(self, blob: str) -> dict[str, str]:
        return self.blobs.get(blob, lambda: self.catalogue.blob_entries(self.vcs.read_blob(blob)))

    def folder(self, toml: Path, ids: tuple[str | None, str | None],
               working: list[tuple[str, Wallpaper]]) -> dict[str, str]:
        """file -> standing for the `working` (file, entry) pairs of one folder."""
        with self._lock:
            return self._folder(toml, ids, working)

    def _folder(self, toml: Path, ids: tuple[str | None, str | None],
                working: list[tuple[str, Wallpaper]]) -> dict[str, str]:
        index_id, head_id = ids
        if index_id is None and head_id is None:
            return {f: NONE for f, _ in working}
        try:
            work_id = blob_id(toml.read_bytes())
        except OSError:
            work_id = None
        key = (index_id, head_id, work_id)
        known = self._answers.get(toml)
        if known is None or known[0] != key:
            known = self._answers[toml] = (key, {})
        cache = known[1]
        todo = [(f, w) for f, w in working if f not in cache]
        if todo:
            cache.update(self._compute(index_id, head_id, work_id, todo))
        return {f: cache[f] for f, _ in working}

    def _compute(self, index_id, head_id, work_id, working) -> dict[str, str]:
        same_as_work = index_id is not None and index_id == work_id
        if same_as_work and head_id == index_id:
            return {f: COMMITTED for f, _ in working}
        if same_as_work and len(working) <= FEW:
            # the index holds the working file, so each entry's index text is its working text: no blob read
            index = {f: self.catalogue.entry_text(w) for f, w in working}
        else:
            index = self._blob(index_id) if index_id is not None else {}
        head = index if head_id == index_id else self._blob(head_id) if head_id is not None else {}
        out: dict[str, str] = {}
        for f, w in working:
            text = index.get(f)
            if text is None:
                out[f] = DELETED if f in head else NONE
            elif not same_as_work and text != self.catalogue.entry_text(w):
                out[f] = MODIFIED
            else:
                out[f] = COMMITTED if head.get(f) == text else STAGED
        return out
