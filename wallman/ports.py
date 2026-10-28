"""Ports: the small interfaces services depend on. Folders are POSIX str relative to the repo root."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .domain.models import Change, Collection, CropBox, Issue, Resolution, Wallpaper


class Catalogue(Protocol):
    def collections(self) -> list[Collection]: ...
    def load(self, folder: str) -> Collection: ...
    def save(self, collection: Collection) -> None: ...  # atomic write
    def create(self, folder: str) -> Collection: ...
    def delete(self, folder: str) -> None: ...  # removes wallpapers.toml
    def structural_issues(self, folder: str) -> list[Issue]: ...
    def render(self, collection: Collection) -> bytes: ...  # what save() would write (the generated index TOML)
    def render_parts(self, collection: Collection) -> tuple[bytes, dict[str, str]]: ...  # + file -> table text
    def entry_text(self, w: Wallpaper) -> str: ...  # one [[wallpaper]] table as save() writes it
    def blob_entries(self, data: bytes) -> dict[str, str]: ...  # file -> table text, from a TOML's bytes


class ImageStore(Protocol):
    def images(self, folder: str) -> list[str]: ...
    def path(self, folder: str, file: str) -> Path: ...  # confined to the repo root
    def move(self, src: Path, dst: Path) -> None: ...
    def delete(self, path: Path) -> None: ...
    def remove_dir(self, folder: str) -> None: ...  # rmdir, only if empty
    def is_external(self, folder: str) -> bool: ...
    def collection_folders(self) -> list[str]: ...
    def image_folders(self) -> list[str]: ...  # MEDIA folders holding images, with or without a toml


class ImageTool(Protocol):
    def identify(self, path: Path | str) -> Resolution: ...  # raises InvalidImage; str: the index passes 10,000
    def crop(self, src: Path, box: CropBox, dst: Path) -> None: ...
    def convert(self, src: Path, dst: Path) -> None: ...
    def render_svg(self, src: Path, dst: Path) -> None: ...
    def thumbnail(self, src: Path, dst: Path, width: int, quality: int = 82) -> None: ...


class Vcs(Protocol):
    def stage_add(self, paths: list[Path], force: bool = False) -> None: ...  # force: add even if ignored
    def stage_rm(self, paths: list[Path]) -> None: ...
    def stage_mv(self, src: Path, dst: Path) -> None: ...
    def untrack(self, paths: list[Path]) -> None: ...  # git rm --cached
    def is_tracked(self, path: Path) -> bool: ...
    def staged(self) -> list[Path]: ...  # absolute paths staged in the main repo (not deletions)
    def file_states(self, paths: list[Path]) -> dict[Path, str]: ...  # spec git states, absolute keys
    def status(self) -> list[Change]: ...  # main repo + submodules
    # publish model: the index wallpapers.toml is a generated blob
    def index_blob(self, path: Path) -> bytes | None: ...  # content in the git index, None when absent
    def head_blob(self, path: Path) -> bytes | None: ...  # content in HEAD, None when absent
    def index_blob_id(self, path: Path) -> str | None: ...  # blob id in the git index, None when absent
    def stage_blob(self, path: Path, data: bytes) -> None: ...  # index content of path := data (file untouched)
    def unstage_path(self, path: Path) -> None: ...  # git rm --cached, nothing when absent
    def blob_ids(self, paths: list[Path]) -> dict[Path, tuple[str | None, str | None]] | None: ...  # (index, HEAD)
    def read_blob(self, blob_id: str) -> bytes: ...


class Fetcher(Protocol):
    def get(self, url: str) -> tuple[bytes, str]: ...  # (content, sha256)
