"""Bring every wallpapers.toml in line with the images on disk (port of _tools/sync.py and prune.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..domain import naming
from ..domain.models import Collection, Ref, Wallpaper
from ..errors import InvalidImage
from ..ports import Catalogue, ImageStore, ImageTool, Vcs
from ._shared import structural_problem
from .unit_of_work import TOML_NAME, UnitOfWork

_YEAR = re.compile(r"(1[4-9]|20)\d\d")


@dataclass
class SyncReport:
    removed: list[Ref] = field(default_factory=list)
    added: list[Ref] = field(default_factory=list)
    renamed: list[tuple[Ref, Ref]] = field(default_factory=list)
    skipped: list[Ref] = field(default_factory=list)  # images ImageMagick could not read
    broken: list[tuple[str, str]] = field(default_factory=list)  # (folder, why): toml unparsable or invalid, left alone


def _parse(file: str) -> tuple[str | None, str, int | None]:
    """(artist, title, year) from an underscore-separated file name."""
    parts = [p for p in Path(file).stem.split("_") if p]
    if not parts:
        return None, "", None
    artist = parts[0] if len(parts) > 1 else None
    title = parts[1] if len(parts) > 1 else parts[0]
    year = next((int(p) for p in parts[2:] if _YEAR.fullmatch(p)), None)
    return artist, title, year


class Syncer:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool, vcs: Vcs,
                 uow_factory: Callable[[], UnitOfWork]) -> None:
        self.catalogue, self.store, self.tool, self.vcs = catalogue, store, tool, vcs
        self.uow_factory = uow_factory

    def sync(self, stage: bool) -> SyncReport:
        report = SyncReport()
        changed: list[Path] = []
        dropped: list[Path] = []
        before = len(report.removed)
        with self.uow_factory() as uow:
            uow.stage = stage
            # a broken toml is reported and skipped: saving or dropping it would lose the entries load() skipped
            for folder in self.store.collection_folders():
                problem = structural_problem(self.catalogue, folder)
                if problem:
                    report.broken.append((folder, problem))
            broken = {f for f, _ in report.broken}
            collections = [c for c in self.catalogue.collections() if c.folder not in broken]
            known = {c.folder for c in collections} | broken
            for coll in collections:
                if self._sync_collection(uow, coll, report):
                    toml = self.store.path(coll.folder, TOML_NAME)
                    (changed if coll.wallpapers or self.store.is_external(coll.folder) else dropped).append(toml)
            for folder in self.store.image_folders():
                if folder in known:
                    continue
                uow.record_collection(folder)
                coll = self.catalogue.create(folder)
                self._sync_collection(uow, coll, report)
                changed.append(self.store.path(folder, TOML_NAME))
            if stage:
                # images deleted by hand: stage their deletion too (git rm of a missing file only touches the index)
                missing = [self.store.path(r.folder, r.file) for r in report.removed[before:]]
                gone = [p for p in missing if self.vcs.is_tracked(p)]
                if gone:
                    self.vcs.stage_rm(gone)
                # new entries are never published: an image of theirs still in the index (a stale name) leaves it
                fresh = [self.store.path(r.folder, r.file) for r in report.added if not self.store.is_external(r.folder)]
                stale = [p for p in fresh if self.vcs.is_tracked(p)]
                if stale:
                    self.vcs.untrack(stale)
                # each changed TOML's index version is regenerated: removed entries leave it, new entries are not
                # published (never automatically); a dropped collection leaves the index
                for toml in changed + dropped:
                    uow.stage_toml(toml.parent.relative_to(self.store.path("", "")).as_posix())
        return report

    def _sync_collection(self, uow: UnitOfWork, coll: Collection, report: SyncReport) -> bool:
        folder = coll.folder
        external = self.store.is_external(folder)
        present = set(self.store.images(folder))
        keep: list[Wallpaper] = []
        removed: list[Ref] = []
        for w in coll.wallpapers:
            if w.file in present or (w.download and not external):
                keep.append(w)
            else:
                removed.append(Ref(folder, w.file))
        listed = {w.file for w in keep}
        added: list[Wallpaper] = []
        added_refs: list[Ref] = []
        renamed: list[tuple[Ref, Ref]] = []
        taken = present | listed
        for f in sorted(present - listed):
            src = self.store.path(folder, f)
            try:
                res = self.tool.identify(src)
            except InvalidImage:
                report.skipped.append(Ref(folder, f))
                continue
            artist, title, year = _parse(f)
            name = f
            if not naming.NAME.match(f) and not external:
                ext = ".png" if src.suffix.lower() == ".png" else ".jpg"
                try:
                    new = naming.file_name(artist, title, year, ext)
                except ValueError:
                    new = f  # nothing usable in the name: keep it, validate reports it
                if new != f:
                    name = naming.unique_name(new, taken)
                    dst = self.store.path(folder, name)
                    uow.record_file(src)
                    uow.record_file(dst)
                    if self.vcs.is_tracked(src):
                        self.vcs.stage_mv(src, dst)
                    else:
                        self.store.move(src, dst)
                    taken.add(name)
                    renamed.append((Ref(folder, f), Ref(folder, name)))
            added.append(Wallpaper(
                file=name,
                title=title.replace("-", " ")[:1].upper() + title.replace("-", " ")[1:],
                artist=artist.replace("-", " ").title() if artist else "Unknown",
                license="personal", resolution=res, year=year))
            added_refs.append(Ref(folder, name))
        if not removed and not added:
            return False
        uow.record_collection(folder)
        coll.wallpapers = keep + added
        if not coll.wallpapers and not external:  # emptied: drop the collection itself (external tomls stay)
            self.catalogue.delete(folder)
            self.store.remove_dir(folder)
        else:
            self.catalogue.save(coll)
        report.removed += removed
        report.added += added_refs
        report.renamed += renamed
        return True
