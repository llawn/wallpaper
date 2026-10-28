"""Download the wallpapers that are listed with a `download` URL but not on disk."""
from __future__ import annotations

import time
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..domain.models import Ref, Wallpaper
from ..errors import ChecksumMismatch, WallmanError
from ..ports import Catalogue, Fetcher, ImageStore, ImageTool


@dataclass
class FetchReport:
    downloaded: int = 0
    failed: list[tuple[Ref, str]] = field(default_factory=list)


def _matches(folder: str, prefixes: list[str]) -> bool:
    return not prefixes or any(folder == p.strip("/") or folder.startswith(p.strip("/") + "/") for p in prefixes)


DELAY = 1.0  # seconds between two downloads: politeness towards the hosts


class FetchService:
    def __init__(self, catalogue: Catalogue, store: ImageStore, tool: ImageTool, fetcher: Fetcher,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.catalogue, self.store, self.tool, self.fetcher = catalogue, store, tool, fetcher
        self.sleep = sleep

    def fetch(self, folders: list[str] | None = None) -> FetchReport:
        report = FetchReport()
        first = True
        for coll in self.catalogue.collections():
            if self.store.is_external(coll.folder) or not _matches(coll.folder, folders or []):
                continue
            present = set(self.store.images(coll.folder))
            for w in coll.wallpapers:
                if not w.download or w.file in present:
                    continue
                ref = Ref(coll.folder, w.file)
                if not first:
                    self.sleep(DELAY)
                first = False
                try:
                    self._fetch_one(coll.folder, w)
                except WallmanError as e:
                    report.failed.append((ref, str(e) or type(e).__name__))
                else:
                    report.downloaded += 1
        return report

    def _fetch_one(self, folder: str, w: Wallpaper) -> None:
        data, sha = self.fetcher.get(w.download)
        if sha != w.sha256:
            raise ChecksumMismatch("checksum mismatch")
        dst = self.store.path(folder, w.file)
        ext = Path(urllib.parse.urlparse(w.download).path).suffix or ".bin"
        tmp = self.store.path(folder, f".fetch-{Path(w.file).stem}{ext}")
        moved = False
        try:
            tmp.write_bytes(data)
            if "render" in w.processing:
                self.tool.render_svg(tmp, dst)
            elif "convert" in w.processing:
                self.tool.convert(tmp, dst)
            else:
                self.store.move(tmp, dst)
                moved = True
            if w.crop is not None:
                self.tool.crop(dst, w.crop, dst)
        except BaseException:
            if w.file in self.store.images(folder):
                with suppress(OSError):
                    self.store.delete(dst)
            raise
        finally:
            if not moved:
                with suppress(OSError):
                    self.store.delete(tmp)

