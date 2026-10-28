"""Temporary uploads: files (or downloads) waiting in the cache until an Importer uses them."""
from __future__ import annotations

import hashlib
import os
import urllib.parse
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..domain.formats import HEAD_BYTES, sniff, stored_ext
from ..domain.models import Resolution
from ..domain.rules import is_web_url
from ..errors import InvalidImage, NotFound, UploadTooLarge
from ..ports import Fetcher, ImageTool


@dataclass(frozen=True)
class Upload:
    id: str
    path: Path
    original_name: str
    resolution: Resolution
    download: str | None
    sha256: str  # of the bytes received (before convert/render)
    processing: tuple[str, ...] = ()  # ("convert",) or ("render",) when `path` is not the received file


def _ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext == ".jpeg":
        return ".jpg"
    return ext if ext in (".jpg", ".png") else ".jpg"


class Uploads:
    def __init__(self, cache_dir: Path, tool: ImageTool, fetcher: Fetcher, max_bytes: int = 200 * 2**20) -> None:
        self.cache_dir = Path(cache_dir)
        self.tool, self.fetcher, self.max_bytes = tool, fetcher, max_bytes
        self._items: dict[str, Upload] = {}

    def from_bytes(self, name: str, data: bytes) -> Upload:
        return self._store(name, data, None, hashlib.sha256(data).hexdigest())

    def from_url(self, url: str) -> Upload:
        if not is_web_url(url):
            raise ValueError(f"{url!r}: only http:// and https:// URLs can be downloaded")
        data, sha = self.fetcher.get(url)
        name = Path(urllib.parse.urlparse(url).path).name
        return self._store(name, data, url, sha)

    def get(self, id: str) -> Upload:
        try:
            return self._items[id]
        except KeyError:
            raise NotFound(f"upload {id} not found") from None

    def discard(self, id: str) -> None:
        up = self._items.pop(id, None)
        if up is not None:
            with suppress(FileNotFoundError):
                up.path.unlink()

    def purge(self) -> None:
        self._items.clear()
        if self.cache_dir.is_dir():
            for p in self.cache_dir.iterdir():
                if p.is_file() or p.is_symlink():
                    with suppress(OSError):
                        p.unlink()

    def from_stream(self, name: str, stream: BinaryIO, length: int) -> Upload:
        """Copy `length` bytes from `stream` to the cache in 1 MiB chunks (memory stays flat)."""
        self._check_size(name, length)
        id, path = self._target(name)
        sha = hashlib.sha256()
        try:
            with path.open("wb") as out:
                left = length
                while left:
                    chunk = stream.read(min(left, 2**20))
                    if not chunk:
                        raise InvalidImage(f"{name or 'upload'}: body ended after {length - left} of {length} bytes")
                    sha.update(chunk)
                    out.write(chunk)
                    left -= len(chunk)
            return self._register(id, path, name, None, sha.hexdigest())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _check_size(self, name: str, size: int) -> None:
        if size > self.max_bytes:
            raise UploadTooLarge(f"{name or 'upload'} is larger than {self.max_bytes // 2**20} MB")

    def _target(self, name: str) -> tuple[str, Path]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        id = uuid.uuid4().hex
        return id, self.cache_dir / f"{id}{_ext(name)}"

    def _normalise(self, path: Path) -> tuple[Path, tuple[str, ...]]:
        """Make `path` a JPEG/PNG named after its real format: rename, convert (WebP, JXL, AVIF, ...) or render (SVG).

        The received file is replaced; on failure both files are removed by the caller's cleanup of `path`."""
        with path.open("rb") as f:
            kind = sniff(f.read(HEAD_BYTES))
        target = path.with_suffix(stored_ext(kind, path.suffix))
        if kind in ("jpeg", "png", None):
            if target != path:
                os.replace(path, target)
            return target, ()
        if target == path:
            target = path.with_name(f"{path.stem}-converted{target.suffix}")
        try:
            if kind == "svg":
                self.tool.render_svg(path, target)
            else:
                self.tool.convert(path, target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        path.unlink()
        return target, ("render",) if kind == "svg" else ("convert",)

    def _register(self, id: str, path: Path, name: str, download: str | None, sha: str) -> Upload:
        received = path
        try:
            path, processing = self._normalise(path)
            res = self.tool.identify(path)  # InvalidImage: the file is removed below
        except BaseException:
            for p in {received, path}:
                p.unlink(missing_ok=True)
            raise
        up = Upload(id, path, name, res, download, sha, processing)
        self._items[id] = up
        return up

    def _store(self, name: str, data: bytes, download: str | None, sha: str) -> Upload:
        self._check_size(name, len(data))
        id, path = self._target(name)
        try:
            path.write_bytes(data)
            return self._register(id, path, name, download, sha)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
