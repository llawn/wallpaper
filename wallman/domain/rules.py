"""Pure collection rules. No I/O: callers pass what is on disk."""
from __future__ import annotations

import re
from typing import Literal

from .licenses import LICENSES, SHAREABLE
from .models import Collection, CropBox, Issue, Resolution
from .naming import NAME

MIN_LONG = 1800  # good enough for a 1920x1080 screen
MIN_SHORT = 900
RATIO = (1.6, 1.95)
MEDIA_DIR = "images"  # every media folder lives under <repo>/images
MEDIA: frozenset[str] = frozenset({
    "painting", "drawing", "illustration", "printmaking", "photography", "animation",
    "poster", "album-cover", "linux", "ml4w", "personal",
})
PROCESSING: frozenset[str] = frozenset({"crop", "fit-16x9", "convert", "render"})  # convert: JXL/WebP -> JPEG; render: SVG -> PNG
IMAGE_EXTS = (".jpg", ".jpeg", ".png")  # the only formats stored in the collection
TOML_NAME = "wallpapers.toml"  # one per collection folder
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA = re.compile(r"[0-9a-f]{64}")


def is_web_url(url: str) -> bool:
    """Downloads are fetched over http(s) only: never file://, ftp:// or other schemes."""
    return isinstance(url, str) and url.lower().startswith(("https://", "http://")) and len(url) > len("https://")


def needs_crop(r: Resolution) -> bool:
    return not RATIO[0] <= r.ratio <= RATIO[1]


def centered_crop(r: Resolution) -> CropBox:
    """Largest 16:9 box, centred, in integer pixels."""
    if r.width * 9 >= r.height * 16:
        h, w = r.height, r.height * 16 // 9
    else:
        w, h = r.width, r.width * 9 // 16
    return CropBox((r.width - w) // 2, (r.height - h) // 2, w, h)


def check_collection(c: Collection, files_on_disk: set[str], sizes: dict[str, Resolution]) -> list[Issue]:
    """files_on_disk: image paths relative to the folder (originals included); sizes: measured resolution per file."""
    issues = collection_issues(c)
    for w in c.wallpapers:
        issues += entry_issues(c, w, files_on_disk, sizes)
    return issues + unlisted_issues(c, files_on_disk)


def collection_issues(c: Collection) -> list[Issue]:
    """The [collection] fields."""
    issues: list[Issue] = []
    if c.medium not in MEDIA:
        issues.append(Issue(c.folder, None, "medium", f"collection.medium {c.medium!r} not in {sorted(MEDIA)}", "error"))
    if not _SLUG.match(c.movement):
        issues.append(Issue(c.folder, None, "movement", "collection.movement must be a lowercase-kebab string", "error"))
    return issues


def entry_issues(c: Collection, w, files_on_disk: set[str], sizes: dict[str, Resolution]) -> list[Issue]:
    """One entry. Depends only on the entry, c.folder, c.external, c.medium, whether its file and original are in
    files_on_disk, and its measured size (the library index reuses the result while those are unchanged)."""
    issues: list[Issue] = []
    f = w.file

    def add(code: str, message: str, level: Literal["error", "warning"] = "error") -> None:
        issues.append(Issue(c.folder, f, code, message, level))

    skip_size = c.external or c.medium == "personal"
    if not c.external and not NAME.match(f):
        add("name", "file name must be artist_title[_year].jpg in lowercase-kebab")
    if w.license not in LICENSES:
        add("license", f"license {w.license!r} not in the allowed list")
    if w.license == "all-rights-reserved" and not (w.license_detail or "").startswith("©"):
        add("license", 'all-rights-reserved needs license_detail naming the holder, e.g. "© 2010 DreamWorks Animation LLC"')
    if w.year is not None and not w.year.is_valid:
        add("year", f"year {w.year.label} must be 1400-2100 with start <= end")
    for t in w.tags:
        if not _SLUG.match(t):
            add("tag", f"tag {t!r} must be a lowercase-kebab string")
    if len(set(w.tags)) != len(w.tags):
        add("tag", "tags must be unique")
    if w.source is not None and not w.source.startswith("https://"):
        add("source", "source must be an https URL")
    if w.download is not None and not is_web_url(w.download):
        add("download", "download must be an http(s) URL")
    download_only = w.license not in SHAREABLE | {"personal"}
    if download_only and not (w.download and w.sha256):
        add("download-required", f"{w.license} files are not committed, so they need download + sha256")
    if w.sha256 is not None and not _SHA.fullmatch(w.sha256):
        add("sha256-format", "sha256 must be 64 lowercase hex characters")
    for p in w.processing:
        if p not in PROCESSING:
            add("processing", f"processing {p!r} not in {sorted(PROCESSING)}")
    if download_only and w.download and "crop" in w.processing and w.crop is None:
        add("crop-box-required", "download-only entry with processing 'crop' needs a crop box")
    if w.original is not None and w.original not in files_on_disk:
        add("original-missing", f"original {w.original} not found")
    if f not in files_on_disk:
        if w.download:
            add("missing", "not downloaded yet", "warning")
        else:
            add("missing", "file not found")
        return issues
    size = sizes.get(f)
    if size is None:
        return issues
    if size != w.resolution:
        add("resolution-mismatch",
            f"resolution says {w.resolution.width}x{w.resolution.height} but file is {size.width}x{size.height}")
    if skip_size:
        return issues
    if max(size.width, size.height) < MIN_LONG or min(size.width, size.height) < MIN_SHORT:
        add("too-small", f"{size.width}x{size.height} is below the {MIN_LONG}x{MIN_SHORT} wallpaper minimum")
    if needs_crop(size):
        add("ratio", f"ratio {size.ratio:.2f} is outside {RATIO[0]}-{RATIO[1]}")
    return issues


def unlisted_issues(c: Collection, files_on_disk: set[str]) -> list[Issue]:
    """Images in the folder that no entry lists (originals excluded)."""
    listed = {w.file for w in c.wallpapers}
    originals = {w.original for w in c.wallpapers if w.original}
    return [Issue(c.folder, f, "unlisted", "image present but not listed in wallpapers.toml", "error")
            for f in sorted(files_on_disk - listed - originals) if f.lower().endswith(IMAGE_EXTS) and "/" not in f]
