"""Catalogue-wide validation: structural problems plus the collection rules against what is on disk."""
from __future__ import annotations

import os
from pathlib import Path

from ..domain import rules
from ..domain.models import Issue, Resolution
from ..errors import InvalidImage, WallmanError
from ..ports import Catalogue, ImageStore, ImageTool


class Validator:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool) -> None:
        self.catalogue = catalogue
        self.store = store
        self.tool = tool

    def run(self) -> list[Issue]:
        issues: list[Issue] = []
        loaded = {c.folder: c for c in self.catalogue.collections()}
        for folder in self.store.collection_folders():
            if folder not in loaded:
                try:
                    loaded[folder] = self.catalogue.load(folder)
                except WallmanError:
                    issues += self.catalogue.structural_issues(folder)  # unparsable: nothing more to check
        for folder, collection in sorted(loaded.items()):
            issues += self.catalogue.structural_issues(folder)
            files = self.store.images(folder)
            sizes: dict[str, Resolution] = {}
            bad: list[Issue] = []
            for f in files:
                try:
                    sizes[f] = self.tool.identify(self.store.path(folder, f))
                except InvalidImage as e:
                    bad.append(Issue(folder, f, "invalid-image", f"cannot read image: {e}", "error"))
            on_disk = set(files) | self._originals(folder, collection)
            issues += rules.check_collection(collection, on_disk, sizes) + bad
        return issues

    def _originals(self, folder: str, collection) -> set[str]:
        """Originals that exist; their paths are relative to the entry's folder (they live in <repo>/_originals/)."""
        base = self.store.path(folder, "")
        return {w.original for w in collection.wallpapers
                if w.original and Path(os.path.normpath(base / w.original)).is_file()}
