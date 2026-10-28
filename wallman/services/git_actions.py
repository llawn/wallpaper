"""Per-wallpaper git actions (publish model): Add to git (publish), Remove from git (unpublish), delete plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..domain.models import Ref, Wallpaper
from ..errors import NotFound, NotShareable, WallmanError
from ..ports import Catalogue, ImageStore, Vcs
from ._shared import check_structure, publish
from .unit_of_work import TOML_NAME, UnitOfWork

# per-wallpaper git states (LibraryIndex) of a published wallpaper: its entry is in the index wallpapers.toml
PUBLISHED_STATES = frozenset({"committed", "staged", "modified", "metadata", "metadata-staged", "conflict"})


TRACKED_IMAGE = frozenset({"committed", "staged", "modified", "deleted", "conflict"})  # per-file states in the index


@dataclass
class DeletePlan:
    tracked: list[Ref] = field(default_factory=list)
    untracked: list[Ref] = field(default_factory=list)
    external: list[Ref] = field(default_factory=list)


class GitActions:
    def __init__(self, catalogue: Catalogue, store: ImageStore, vcs: Vcs,
                 uow_factory: Callable[[], UnitOfWork]) -> None:
        self.catalogue, self.store, self.vcs, self.uow_factory = catalogue, store, vcs, uow_factory

    def _refuse_external(self, refs: list[Ref], action: str) -> None:
        ext = sorted({r.folder for r in refs if self.store.is_external(r.folder)})
        if ext:
            raise WallmanError(f"{action} is not available in the external collection {ext[0]}/: it has its own "
                               "repository, run git there")

    def _entries(self, refs: list[Ref]) -> dict[Ref, Wallpaper]:
        loaded: dict[str, dict[str, Wallpaper]] = {}
        out: dict[Ref, Wallpaper] = {}
        for r in refs:
            if r.folder not in loaded:
                loaded[r.folder] = {w.file: w for w in self.catalogue.load(r.folder).wallpapers}
            try:
                out[r] = loaded[r.folder][r.file]
            except KeyError:
                raise NotFound(f"{r.folder}/{r.file} is not listed") from None
        return out

    def add(self, refs: list[Ref]) -> list[Ref]:
        """Publish: each wallpaper's entry joins the index TOML; shareable images are staged, download-only ones stay
        out of git. Personal wallpapers are never published; external collections are refused. All or nothing."""
        refs = list(dict.fromkeys(refs))
        self._refuse_external(refs, "Add to git")
        with self.uow_factory() as uow:
            entries = self._entries(refs)
            personal = [r for r in refs if entries[r].license == "personal"]
            if personal:
                lines = ", ".join(f"{r.folder}/{r.file}" for r in personal)
                raise NotShareable(f"personal wallpapers are never published: {lines}")
            check_structure(self.catalogue, [r.folder for r in refs])
            for r in refs:
                publish(uow, self.store, r.folder, r.file, entries[r].license)
        return refs

    def remove(self, refs: list[Ref]) -> list[Ref]:
        """Unpublish: the entries leave the index TOML and the images the index; files on disk untouched. Returns the
        wallpapers that were published."""
        refs = list(dict.fromkeys(refs))
        self._refuse_external(refs, "Remove from git")
        with self.uow_factory() as uow:
            self._entries(refs)
            check_structure(self.catalogue, [r.folder for r in refs])
            # one git status for every image, one `git rm --cached` for the tracked ones, one index TOML per folder
            paths = {r: self.store.path(r.folder, r.file) for r in refs}
            states = self.vcs.file_states(list(paths.values()))
            tracked = [r for r in refs if states.get(paths[r]) in TRACKED_IMAGE]
            if tracked:
                self.vcs.untrack([paths[r] for r in tracked])
            done = set(tracked)
            for folder in dict.fromkeys(r.folder for r in refs):
                published = uow.published(folder)
                gone = {r.file for r in refs if r.folder == folder} & published
                if gone:
                    uow.set_published(folder, published - gone)
                    done.update(r for r in refs if r.folder == folder and r.file in gone)
            return [r for r in refs if r in done]

    def published(self, refs: list[Ref]) -> set[Ref]:
        """The refs whose entry is in their folder's index TOML (one index read per folder)."""
        sets: dict[str, set[str]] = {}
        for r in refs:
            if r.folder not in sets:
                blob = self.vcs.index_blob(self.store.path(r.folder, TOML_NAME))
                sets[r.folder] = set(self.catalogue.blob_entries(blob)) if blob else set()
        return {r for r in refs if r.file in sets[r.folder]}

    def delete_plan(self, refs: list[Ref]) -> DeletePlan:
        """tracked: published wallpapers (deleting them stages a removal); external: per-file git state as before."""
        plan = DeletePlan()
        main = [r for r in refs if not self.store.is_external(r.folder)]
        published = self.published(main)
        ext = [r for r in refs if self.store.is_external(r.folder)]
        for r in refs:
            if r in ext:
                plan.external.append(r)
            elif r in published:
                plan.tracked.append(r)
            else:
                plan.untracked.append(r)
        return plan
