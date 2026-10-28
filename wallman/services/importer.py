"""Add a new wallpaper from an upload (spec section 5.1)."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..domain import naming
from ..domain.licenses import is_shareable
from ..domain.rules import IMAGE_EXTS
from ..domain.models import CropBox, Ref, Resolution, Wallpaper
from ..domain.years import YearSpan, year_to_toml
from ..errors import LicenseNeedsDownload
from ..ports import Catalogue, ImageStore, ImageTool, Vcs
from ._shared import check_box, check_folder, check_ratio, check_size, needs_download, size_rules_apply, stage_entry
from .unit_of_work import UnitOfWork
from .uploads import Upload, Uploads


@dataclass(frozen=True)
class NewWallpaper:
    folder: str
    title: str
    artist: str | None
    year: "int | YearSpan | None"
    license: str
    license_detail: str | None
    source: str | None
    crop: CropBox | None
    tags: tuple[str, ...] = ()


class Importer:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool, vcs: Vcs,
                 uow_factory: Callable[[], UnitOfWork], uploads: Uploads) -> None:
        self.catalogue, self.store, self.tool, self.vcs = catalogue, store, tool, vcs
        self.uow_factory, self.uploads = uow_factory, uploads

    def add(self, upload: Upload, meta: NewWallpaper, stage: bool = True) -> Ref:
        """stage=False: the image is left untracked (the wallpapers.toml is still staged)."""
        shareable = is_shareable(meta.license)
        if needs_download(meta.license) and upload.download is None:
            raise LicenseNeedsDownload(f"{meta.license} files are not committed: add the image from a URL")
        folder = check_folder(meta.folder, self.store)
        with self.uow_factory() as uow:
            external = self.store.is_external(folder)
            known = {c.folder: c for c in self.catalogue.collections()}
            medium = known[folder].medium if folder in known else folder.split("/")[0]
            enforce = size_rules_apply(medium, external)
            res = upload.resolution
            if meta.crop is not None:
                check_box(meta.crop, res)
                res = Resolution(meta.crop.w, meta.crop.h)
            elif enforce:
                check_ratio(res)
            if enforce:
                check_size(res)
            existing = {w.file for w in known[folder].wallpapers} if folder in known else set()
            taken = existing | set(self.store.images(folder))
            ext = upload.path.suffix.lower()
            if external:
                name = Path(upload.original_name).name or f"image{ext}"
                if Path(name).suffix.lower() not in IMAGE_EXTS or upload.processing:
                    name = Path(name).stem + ext  # e.g. foo.webp converted to foo.jpg
            else:
                name = naming.file_name(meta.artist, meta.title, meta.year, ext)
            name = naming.unique_name(name, taken)
            dst = self.store.path(folder, name)

            uow.record_collection(folder)
            coll = known[folder] if folder in known else self.catalogue.create(folder)
            uow.record_file(dst)
            if meta.crop is not None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                self.tool.crop(upload.path, meta.crop, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(upload.path, dst)  # the upload lives outside the repo, so not store.move
            coll.wallpapers.append(Wallpaper(
                file=name, title=meta.title, artist=meta.artist or "Unknown", license=meta.license,
                resolution=res, year=meta.year, license_detail=meta.license_detail, source=meta.source, tags=meta.tags,
                download=upload.download, sha256=upload.sha256 if upload.download else None,
                processing=upload.processing + (("crop",) if meta.crop is not None else ()),
                crop=meta.crop if meta.crop is not None and upload.download else None))
            self.catalogue.save(coll)
            stage_entry(uow, self.store, folder, name, shareable and stage)
        self.uploads.discard(upload.id)
        return Ref(folder, name)
