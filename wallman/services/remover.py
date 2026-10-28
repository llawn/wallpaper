"""Delete wallpapers (spec section 5.2)."""
from __future__ import annotations

from typing import Callable, Literal

from ..domain.models import Ref
from ..errors import NotFound
from ..ports import Catalogue, ImageStore, Vcs
from ._shared import check_structure, save_or_drop
from .unit_of_work import UnitOfWork


class Remover:
    def __init__(self, catalogue: Catalogue, store: ImageStore, vcs: Vcs, uow_factory: Callable[[], UnitOfWork]) -> None:
        self.catalogue, self.store, self.vcs, self.uow_factory = catalogue, store, vcs, uow_factory

    def delete(self, refs: list[Ref], git: Literal["remove", "keep"] = "remove") -> int:
        """git="keep": delete the files but never touch the index for the images (their deletion stays unstaged)."""
        if git not in ("remove", "keep"):
            raise ValueError(f"git must be 'remove' or 'keep', not {git!r}")
        by_folder: dict[str, set[str]] = {}
        for ref in refs:
            by_folder.setdefault(ref.folder, set()).add(ref.file)
        count = 0
        with self.uow_factory() as uow:
            loaded = {}
            for folder, files in by_folder.items():  # check everything first: git calls cannot be undone
                coll = self.catalogue.load(folder)
                missing = files - {w.file for w in coll.wallpapers}
                if missing:
                    raise NotFound(f"{folder}/{sorted(missing)[0]} is not listed")
                loaded[folder] = coll
            check_structure(self.catalogue, loaded)
            for folder, coll in loaded.items():
                on_disk = set(self.store.images(folder))
                for file in sorted(by_folder[folder]):
                    path = self.store.path(folder, file)
                    if file in on_disk:
                        uow.record_file(path)
                    if git == "remove" and self.vcs.is_tracked(path):
                        self.vcs.stage_rm([path])
                    elif file in on_disk:
                        self.store.delete(path)
                    count += 1
                coll.wallpapers = [w for w in coll.wallpapers if w.file not in by_folder[folder]]
                save_or_drop(uow, self.catalogue, self.store, self.vcs, coll)
        return count
