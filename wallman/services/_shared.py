"""Pieces the mutating services share: size rules, crop checks, staging and emptied-collection handling."""
from __future__ import annotations

from ..domain import rules
from ..domain.licenses import is_shareable
from ..domain.models import Collection, CropBox, Resolution
from ..errors import InvalidImage, NotFound, RatioNeedsCrop, TooSmall, WallmanError
from ..ports import Catalogue, ImageStore, Vcs
from .unit_of_work import TOML_NAME, UnitOfWork


def check_folder(folder: str, store: ImageStore) -> str:
    """Normalised destination folder (relative to images/), or ValueError.

    Slashes are stripped; empty, "." and ".." parts are refused; the first part must be a medium; a folder strictly
    inside an external collection (an ancestor holds .git) is refused: it would belong to the submodule's history.
    """
    if not isinstance(folder, str):
        raise ValueError("folder must be a string")
    parts = folder.strip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"folder {folder!r} is not a plain relative path like painting/impressionism")
    if parts[0] not in rules.MEDIA:
        raise ValueError(f"folder {folder!r} must start with a medium: {', '.join(sorted(rules.MEDIA))}")
    for i in range(1, len(parts)):
        ancestor = "/".join(parts[:i])
        if store.is_external(ancestor):
            raise ValueError(f"folder {folder!r} is inside the external collection {ancestor}/: "
                             "add it inside that repository instead")
    return "/".join(parts)


def needs_download(license: str) -> bool:
    """Licenses whose files are not committed (personal files are simply not shared, nothing to re-download)."""
    return not is_shareable(license) and license != "personal"


def size_rules_apply(coll_medium: str, external: bool) -> bool:
    return not external and coll_medium != "personal"


def check_box(box: CropBox, res: Resolution) -> None:
    if box.x < 0 or box.y < 0 or box.w <= 0 or box.h <= 0 or box.x + box.w > res.width or box.y + box.h > res.height:
        raise InvalidImage(f"crop box {box.x},{box.y},{box.w},{box.h} does not fit inside {res.width}x{res.height}")


def check_size(res: Resolution) -> None:
    if max(res.width, res.height) < rules.MIN_LONG or min(res.width, res.height) < rules.MIN_SHORT:
        raise TooSmall(f"{res.width}x{res.height} is below the {rules.MIN_LONG}x{rules.MIN_SHORT} wallpaper minimum")


def check_ratio(res: Resolution) -> None:
    if rules.needs_crop(res):
        raise RatioNeedsCrop(f"{res.width}x{res.height} has ratio {res.ratio:.2f}, outside "
                             f"{rules.RATIO[0]}-{rules.RATIO[1]}: choose a crop")


def structural_problem(catalogue: Catalogue, folder: str) -> str | None:
    """A one-line description of the folder's structural problems, or None (also None without a toml)."""
    try:
        problems = catalogue.structural_issues(folder)
    except NotFound:
        return None
    if not problems:
        return None
    return (f"{folder}/{TOML_NAME} has {len(problems)} structural problem(s), e.g. {problems[0].code}: "
            f"{problems[0].message}")


def check_structure(catalogue: Catalogue, folders) -> None:
    """Refuse up front (before any git index change, which cannot be undone) when a touched toml is broken."""
    for folder in dict.fromkeys(folders):
        problem = structural_problem(catalogue, folder)
        if problem:
            raise WallmanError(f"{problem}; fix that wallpapers.toml first (nothing was changed)")


def stage_entry(uow: UnitOfWork, store: ImageStore, folder: str, file: str, shareable: bool) -> None:
    """After the entry or the image changed: regenerate the folder's index TOML, and re-stage the image when the
    wallpaper is published and shareable (an unpublished one stays out of git). External collections: image (when
    shareable) and whole TOML, in their own repository, as before. Deferred to commit time."""
    if store.is_external(folder):
        toml = store.path(folder, TOML_NAME)
        uow.stage_add([store.path(folder, file), toml] if shareable else [toml])
        return
    uow.stage_toml(folder)
    if shareable and file in uow.published(folder) and store.path(folder, file).is_file():
        uow.stage_add([store.path(folder, file)])


def publish(uow: UnitOfWork, store: ImageStore, folder: str, file: str, license: str) -> None:
    """Add a wallpaper to git: its entry joins the published set; a shareable image is staged too (download-only
    images stay ignored: their entry is enough to fetch them)."""
    uow.set_published(folder, uow.published(folder) | {file})
    image = store.path(folder, file)
    if is_shareable(license) and image.is_file():
        uow.stage_add([image])


def save_or_drop(uow: UnitOfWork, catalogue: Catalogue, store: ImageStore, vcs: Vcs, coll: Collection) -> None:
    """Save the collection, or delete it (TOML and folder) when its last entry is gone; either way its index TOML
    is regenerated at commit (a dropped collection leaves the index).

    Callers ran check_structure first, so every [[wallpaper]] table of the toml was loaded: an empty
    collection here means the raw toml has none left. catalogue.delete() re-checks and refuses otherwise.
    """
    folder = coll.folder
    uow.record_collection(folder)
    if coll.wallpapers or store.is_external(folder):  # an external collection keeps its (empty) toml
        catalogue.save(coll)
        uow.stage_toml(folder)
        return
    catalogue.delete(folder)
    store.remove_dir(folder)
    uow.stage_toml(folder)
