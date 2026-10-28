"""Re-crop a wallpaper (spec section 5.4).

Entries with a download are re-cropped from the original (fetched and converted again), so the box is in original
pixels. Rendered entries (processing has "render": SVG originals) are the exception: the browser only knows an SVG's
natural size, not the 3840x2160 render the box must refer to, so they are cropped from the current rendered file and
the stored box is composed with the previous one (still relative to the full render, so `just fetch` replays it).
"""
from __future__ import annotations

import dataclasses
import urllib.parse
from contextlib import suppress
from pathlib import Path
from typing import Callable

from ..domain.licenses import is_shareable
from ..domain.models import CropBox, Ref, Resolution, Wallpaper
from ..errors import ChecksumMismatch, NotFound, WallmanError
from ..ports import Catalogue, Fetcher, ImageStore, ImageTool, Vcs
from ._shared import check_box, check_size, size_rules_apply, stage_entry
from .unit_of_work import UnitOfWork


class Cropper:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool, vcs: Vcs,
                 uow_factory: Callable[[], UnitOfWork], fetcher: Fetcher) -> None:
        self.catalogue, self.store, self.tool, self.vcs = catalogue, store, tool, vcs
        self.uow_factory, self.fetcher = uow_factory, fetcher

    def crop(self, ref: Ref, box: CropBox) -> Wallpaper:
        image = self.store.path(ref.folder, ref.file)
        temps: list[Path] = []
        try:
            with self.uow_factory() as uow:
                coll = self.catalogue.load(ref.folder)
                idx = next((i for i, w in enumerate(coll.wallpapers) if w.file == ref.file), None)
                if idx is None:
                    raise NotFound(f"{ref.folder}/{ref.file} is not listed")
                old = coll.wallpapers[idx]
                result = Resolution(box.w, box.h)
                if size_rules_apply(coll.medium, self.store.is_external(ref.folder)):
                    check_size(result)  # before any download: the result size is known from the box
                rendered = "render" in old.processing
                stored = box if old.download else old.crop
                if rendered:
                    if ref.file not in self.store.images(ref.folder):
                        raise NotFound(f"{ref.folder}/{ref.file} is not on disk: run `just fetch` first")
                    source = image
                    if old.download and "crop" in old.processing:
                        if old.crop is None:
                            raise WallmanError(f"{ref.folder}/{ref.file}: cropped render without a crop box, "
                                               "cannot compose the new crop")
                        stored = CropBox(old.crop.x + box.x, old.crop.y + box.y, box.w, box.h)
                else:
                    # the original is downloaded inside the lock: which URL to fetch comes from the catalogue entry
                    source = self._original(ref.folder, old, temps) if old.download else image
                check_box(box, self.tool.identify(source))
                uow.record_collection(ref.folder)
                uow.record_file(image)
                self.tool.crop(source, box, image)
                new = dataclasses.replace(
                    old, resolution=result, crop=stored,
                    processing=old.processing if "crop" in old.processing else old.processing + ("crop",))
                coll.wallpapers[idx] = new
                self.catalogue.save(coll)
                stage_entry(uow, self.store, ref.folder, ref.file, is_shareable(old.license))
        finally:
            for t in temps:
                with suppress(OSError):
                    self.store.delete(t)
        return new

    def _original(self, folder: str, w: Wallpaper, temps: list[Path]) -> Path:
        """Download the original and apply convert, leaving it in temp files next to the image (never a render)."""
        data, sha = self.fetcher.get(w.download)
        if sha != w.sha256:
            raise ChecksumMismatch("checksum mismatch: the download changed since it was added")
        ext = Path(urllib.parse.urlparse(w.download).path).suffix or ".bin"
        stem = Path(w.file).stem
        raw = self.store.path(folder, f".crop-{stem}{ext}")
        temps.append(raw)
        raw.write_bytes(data)
        if "convert" in w.processing:
            out = self.store.path(folder, f".crop-{stem}-processed{Path(w.file).suffix}")
            temps.append(out)
            self.tool.convert(raw, out)
            return out
        return raw
