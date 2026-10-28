"""File-name rules: kebab-case artist_title[_year].jpg|png (a year span names the file by its start year)."""
from __future__ import annotations

import re
import unicodedata

from .years import YearSpan

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(_[a-z0-9]+(-[a-z0-9]+)*)*\.(jpg|png)$")


def slug(text: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        cut = s[:max_len]
        if s[max_len] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        s = cut.strip("-")
    return s


def slug_tag(text: str) -> str:
    """A tag is slugged like a name part; "" when nothing letter-like is left (the caller drops it)."""
    return slug(text)


def normalise_tags(tags) -> tuple[str, ...]:
    """Slug every tag, drop empties and duplicates, sort."""
    return tuple(sorted({t for t in (slug_tag(x) for x in tags) if t}))


def file_name(artist: str | None, title: str, year: "int | YearSpan | None", ext: str) -> str:
    """ValueError when neither the artist nor the title leaves any letter or digit (the name would be empty)."""
    parts = [slug(artist) if artist else "", slug(title), str(year.start if isinstance(year, YearSpan) else year) if year is not None else ""]
    if not parts[0] and not parts[1]:
        raise ValueError(f"cannot build a file name from title {title!r}: use letters or digits")
    ext = ext.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    return "_".join(p for p in parts if p) + ext


def unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    n = 2
    while f"{stem}-{n}.{ext}" in taken:
        n += 1
    return f"{stem}-{n}.{ext}"
