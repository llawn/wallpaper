"""Per-file git actions that touch only the index (spec 3.2): add, remove from git, delete plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..domain.licenses import is_shareable
from ..domain.models import Ref
from ..errors import NotFound, NotShareable, WallmanError
from ..ports import Catalogue, ImageStore, Vcs
from .unit_of_work import WRITE_LOCK, UnitOfWork

TRACKED_STATES = frozenset({"committed", "staged", "modified", "deleted", "conflict"})


@dataclass
class DeletePlan:
    tracked: list[Ref] = field(default_factory=list)
    untracked: list[Ref] = field(default_factory=list)
    external: list[Ref] = field(default_factory=list)


class GitActions:
    def __init__(self, catalogue: Catalogue, store: ImageStore, vcs: Vcs,
                 uow_factory: Callable[[], UnitOfWork]) -> None:
        self.catalogue, self.store, self.vcs, self.uow_factory = catalogue, store, vcs, uow_factory

    def _check_external(self, refs: list[Ref]) -> None:
        """Submodule files and main-repo files never go in one call; one submodule per call."""
        ext = {r.folder for r in refs if self.store.is_external(r.folder)}
        if ext and (len(ext) > 1 or any(not self.store.is_external(r.folder) for r in refs)):
            raise WallmanError("mixing submodule and main-repo files: run the action per collection")

    def _license(self, ref: Ref, cache: dict[str, dict[str, str]]) -> str:
        if ref.folder not in cache:
            cache[ref.folder] = {w.file: w.license for w in self.catalogue.load(ref.folder).wallpapers}
        try:
            return cache[ref.folder][ref.file]
        except KeyError:
            raise NotFound(f"{ref.folder}/{ref.file} is not listed") from None

    def add(self, refs: list[Ref]) -> list[Ref]:
        with WRITE_LOCK:  # `git add` takes .git/index.lock: never at the same time as another write
            return self._add(list(dict.fromkeys(refs)))

    def _add(self, refs: list[Ref]) -> list[Ref]:
        self._check_external(refs)
        cache: dict[str, dict[str, str]] = {}
        bad = [r for r in refs if not is_shareable(self._license(r, cache))]
        if bad:
            lines = ", ".join(f"{r.folder}/{r.file} ({self._license(r, cache)})" for r in bad)
            raise NotShareable(f"not shareable, stays download-only: {lines}")
        if refs:
            self.vcs.stage_add([self.store.path(r.folder, r.file) for r in refs])
        return refs

    def remove(self, refs: list[Ref]) -> list[Ref]:
        with WRITE_LOCK:
            return self._remove(list(dict.fromkeys(refs)))

    def _remove(self, refs: list[Ref]) -> list[Ref]:
        self._check_external(refs)
        paths = {r: self.store.path(r.folder, r.file) for r in refs}
        states = self.vcs.file_states(list(paths.values()))
        tracked = [r for r in refs if states[paths[r]] in TRACKED_STATES]
        if tracked:
            self.vcs.untrack([paths[r] for r in tracked])
        return tracked

    def delete_plan(self, refs: list[Ref]) -> DeletePlan:
        plan = DeletePlan()
        paths = {r: self.store.path(r.folder, r.file) for r in refs}
        states = self.vcs.file_states(list(paths.values()))
        for r in refs:
            if self.store.is_external(r.folder):
                plan.external.append(r)
            elif states[paths[r]] in TRACKED_STATES:
                plan.tracked.append(r)
            else:
                plan.untracked.append(r)
        return plan
