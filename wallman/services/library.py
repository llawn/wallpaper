"""Browse and edit wallpaper metadata (spec section 5.3): rename, move, relicense."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Callable

from ..domain import naming, rules
from ..domain.licenses import LICENSES, is_shareable
from ..domain.models import Issue, Ref, Wallpaper
from ..domain.years import YearSpan, year_from_toml
from ..errors import LicenseNeedsDownload, NameConflict, NotFound
from ..ports import Catalogue, Fetcher, ImageStore, Vcs
from ._shared import check_folder, check_structure, needs_download, save_or_drop
from .library_index import LibraryIndex, Query
from .unit_of_work import TOML_NAME, UnitOfWork

FIELDS = frozenset({"title", "artist", "year", "tags", "license", "license_detail", "source", "download"})
NAME_FIELDS = ("title", "artist", "year")


OPTIONAL_TEXT = ("artist", "license_detail", "source", "download")


def check_values(changes: dict) -> dict:
    """Checked copy of `changes`: "" becomes None for optional text fields; a wrong type is a ValueError (it would
    corrupt the TOML: year is written unquoted). `year` (int or [start, end] in the API) becomes a YearSpan and
    `tags` a normalised sorted tuple."""
    changes = {k: (None if k in OPTIONAL_TEXT and v == "" else v) for k, v in changes.items()}
    for k, v in changes.items():
        if k == "year":
            if v is not None:
                span = v if isinstance(v, YearSpan) else year_from_toml(v)
                if span is None:
                    raise ValueError("year must be an integer, a [start, end] pair or null")
                if not span.is_valid:
                    raise ValueError("year must be between 1400 and 2100, with start <= end")
                changes[k] = span
        elif k == "tags":
            if not isinstance(v, (list, tuple)) or not all(isinstance(t, str) for t in v):
                raise ValueError("tags must be a list of strings")
            changes[k] = naming.normalise_tags(v)
        elif k in FIELDS or k == "folder":
            if v is not None and not isinstance(v, str):
                raise ValueError(f"{k} must be a string or null")
    for k in ("title", "license", "folder"):
        if k in changes and not changes[k]:
            raise ValueError(f"{k} must not be empty")
    if changes.get("download") is not None and not rules.is_web_url(changes["download"]):
        raise ValueError("download must be an http:// or https:// URL")
    return changes


class _Taken:
    """The names already used in a folder (listed in its toml or an image file there), minus `free`: what
    `listed | set(store.images(folder))` answers for `in`, without listing a 10,000-file folder."""

    def __init__(self, listed: set[str], folder: Path, free: str | None) -> None:
        self.listed, self.folder, self.free = listed, folder, free

    def __contains__(self, name: str) -> bool:
        if name == self.free:
            return False
        return name in self.listed or (name.lower().endswith(rules.IMAGE_EXTS) and os.path.isfile(self.folder / name))


class Library:
    def __init__(self, catalogue: Catalogue, store: ImageStore, vcs: Vcs, uow_factory: Callable[[], UnitOfWork],
                 fetcher: Fetcher, index: LibraryIndex | None = None) -> None:
        """`index`: the app's shared LibraryIndex; without one, every list() builds a throwaway index that trusts
        entry resolutions (v1 behaviour: no image reads)."""
        self.catalogue, self.store, self.vcs, self.uow_factory = catalogue, store, vcs, uow_factory
        self.fetcher, self.index = fetcher, index

    def list(self, folder: str | None = None, query: str = "", license: str | None = None,
             with_issues: bool = False, year: YearSpan | None = None, tags: tuple[str, ...] = (),
             ratio: str | None = None) -> list[tuple[Ref, Wallpaper, list[Issue]]]:
        index = self.index
        if index is None:
            index = LibraryIndex(self.catalogue, self.store, None, self.vcs)
            index.warm()
        found = index.select(Query(folder=folder, q=query, license=license, issues=with_issues, year=year,
                                   tags=tuple(tags), ratio=ratio))
        return [(e.ref, e.wallpaper, list(e.issues)) for e in found]

    def get(self, ref: Ref) -> Wallpaper:
        return self._find(self.catalogue.load(ref.folder), ref)[1]

    def _load_or_none(self, folder: str):
        try:
            return self.catalogue.load(folder)
        except NotFound:
            return None

    @staticmethod
    def _find(coll, ref: Ref) -> tuple[int, Wallpaper]:
        for i, w in enumerate(coll.wallpapers):
            if w.file == ref.file:
                return i, w
        raise NotFound(f"{ref.folder}/{ref.file} is not listed")

    def update(self, ref: Ref, changes: dict) -> Ref:
        unknown = set(changes) - FIELDS - {"folder"}
        if unknown:
            raise ValueError(f"cannot change {sorted(unknown)}")
        changes = check_values(changes)
        if "license" in changes and changes["license"] not in LICENSES:
            raise ValueError(f"license {changes['license']!r} is not in the allowed list")
        if "folder" in changes:
            changes = {**changes, "folder": check_folder(changes["folder"], self.store)}
        sha: str | None = None
        if changes.get("download"):  # network first, outside the lock: a failure changes nothing
            sha = self.fetcher.get(changes["download"])[1]

        with self.uow_factory() as uow:
            src = self.catalogue.load(ref.folder)
            idx, old = self._find(src, ref)
            target = changes.get("folder", ref.folder)
            moving = target != ref.folder
            if moving and (self.store.is_external(ref.folder) or self.store.is_external(target)):
                raise ValueError("wallpapers cannot be moved into or out of an external collection")
            check_structure(self.catalogue, [ref.folder, target])
            fields = {k: v for k, v in changes.items() if k != "folder"}
            if "artist" in fields and not fields["artist"]:
                fields["artist"] = "Unknown"
            if "download" in fields:
                fields["sha256"] = sha  # None when the download is cleared
            new = dataclasses.replace(old, **fields)
            old_share, new_share = is_shareable(old.license), is_shareable(new.license)
            if ("license" in fields or "download" in fields) and needs_download(new.license) and new.download is None:
                raise LicenseNeedsDownload(f"{new.license} files are not committed: give the download URL")

            dst = src if not moving else self._load_or_none(target)  # not collections(): one folder, not all
            taken = _Taken({w.file for w in dst.wallpapers} if dst else set(), self.store.path(target, ""),
                           None if moving else old.file)
            name = old.file
            if not self.store.is_external(ref.folder) and any(
                    k in fields and fields[k] != getattr(old, k) for k in NAME_FIELDS):
                name = naming.unique_name(naming.file_name(new.artist, new.title, new.year, Path(old.file).suffix), taken)
            if moving and name in taken:
                raise NameConflict(f"{target}/{name} already exists")
            src_path, dst_path = self.store.path(ref.folder, old.file), self.store.path(target, name)
            # what `old.file in store.images(ref.folder)` answers, without listing 10,000 files again
            on_disk = Path(old.file).suffix.lower() in rules.IMAGE_EXTS and os.path.isfile(src_path)
            file_moves = on_disk and (moving or name != old.file)

            uow.record_collection(ref.folder)
            if moving:
                uow.record_collection(target)
                if dst is None:
                    dst = self.catalogue.create(target)
            external = self.store.is_external(ref.folder)
            # publish model: a published entry stays published under its new name / in its new folder; one that
            # becomes personal is unpublished (personal wallpapers are never published)
            was_published = not external and old.file in uow.published(ref.folder)
            stays_published = was_published and new.license != "personal"
            if file_moves:
                uow.record_file(src_path)
                uow.record_file(dst_path)
                if self.vcs.is_tracked(src_path):
                    self.vcs.stage_mv(src_path, dst_path)
                else:
                    self.store.move(src_path, dst_path)
            # an unpublished wallpaper taking a name whose image is still in the index (a stale one) untracks it
            stale = file_moves and not external and not stays_published
            if (old_share and not new_share or was_published and not stays_published or stale) \
                    and self.vcs.is_tracked(dst_path):
                self.vcs.untrack([dst_path])
            new = dataclasses.replace(new, file=name)
            if moving:
                src.wallpapers.pop(idx)
                dst.wallpapers.append(new)
                save_or_drop(uow, self.catalogue, self.store, self.vcs, src)
            else:
                src.wallpapers[idx] = new
            self.catalogue.save(dst)
            if was_published:
                uow.set_published(ref.folder, uow.published(ref.folder) - {old.file})
                if stays_published:
                    uow.set_published(target, uow.published(target) | {name})
            uow.stage_toml(target)  # regenerated with the generated files, in one commit-time step
            if new_share and on_disk and not old_share and (stays_published or external):
                # relicensed to shareable: the image may still be in .gitignore (regenerated on commit), so force
                # the add -- deferred into on_commit's one batched `git add` (Ruling v2-17). A rename or move keeps
                # the tracked state instead (Ruling v2-18): git mv above for tracked files, nothing for the others.
                uow.stage_add([dst_path], force=True)
        return Ref(target, name)
