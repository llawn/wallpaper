"""Plain data types shared by every layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .years import YearSpan


@dataclass(frozen=True)
class Resolution:
    width: int
    height: int

    @property
    def ratio(self) -> float:
        return self.width / self.height


@dataclass(frozen=True)
class CropBox:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Ref:
    folder: str  # relative to the repo root, POSIX
    file: str


@dataclass(frozen=True)
class Wallpaper:
    file: str
    title: str
    artist: str
    license: str
    resolution: Resolution
    year: YearSpan | None = None
    license_detail: str | None = None
    source: str | None = None
    download: str | None = None
    sha256: str | None = None
    processing: tuple[str, ...] = ()
    original: str | None = None
    crop: CropBox | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.year, int) and not isinstance(self.year, bool):  # a plain year is a one-year span
            object.__setattr__(self, "year", YearSpan(self.year, self.year))
        if not isinstance(self.tags, tuple):
            object.__setattr__(self, "tags", tuple(self.tags))


@dataclass
class Collection:
    folder: str
    medium: str
    movement: str
    wallpapers: list[Wallpaper] = field(default_factory=list)
    header: str = ""
    external: bool = False


@dataclass(frozen=True)
class Issue:
    folder: str
    file: str | None
    code: str
    message: str
    level: Literal["error", "warning"]


@dataclass(frozen=True)
class Change:
    repo: str
    status: str
    path: str
