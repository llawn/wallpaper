"""Route table: (method, regex) -> handler(app, match, request) -> (status, payload). Services only."""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, BinaryIO, Callable

from ..bootstrap import App
from ..domain import licenses, rules
from ..domain.buckets import BUCKETS
from ..domain.naming import normalise_tags
from ..domain.models import CropBox, Ref, Wallpaper
from ..domain.years import parse_year, suggest_years, year_to_toml
from ..errors import GitError, InvalidImage, NotFound, NotShareable
from ..services.git_actions import TRACKED_STATES
from ..services.importer import NewWallpaper
from ..services.library import check_values
from ..services.library_index import IndexedEntry, Query
from ..services.uploads import Upload
from .thumb_key import thumb_key_for
from .thumbs import ThumbPool


@dataclasses.dataclass
class Binary:
    """A file response: the server streams `path` with `content_type`."""
    path: Any
    content_type: str
    etag: str | None = None
    cache: str = "no-cache"
    not_modified: bool = False
    body: bytes | None = None  # in-memory content instead of a file


class Request:
    def __init__(self, query: dict[str, str], body: bytes, headers: Any, thumbs: ThumbPool,
                 stream: BinaryIO | None = None, length: int = 0,
                 query_all: dict[str, list[str]] | None = None) -> None:
        self.query, self.body, self.headers, self.thumbs = query, body, headers, thumbs
        self.query_all = query_all if query_all is not None else {k: [v] for k, v in query.items()}
        self.stream, self.length = stream, length

    def json(self) -> dict:
        try:
            data = json.loads(self.body or b"{}")
        except ValueError as e:
            raise ValueError(f"invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data


def ref_from(raw: str) -> Ref:
    """`folder/…/file` (URL-encoded) -> Ref; confinement is the store's job."""
    folder, _, file = urllib.parse.unquote(raw).strip("/").rpartition("/")
    if not folder or not file:
        raise NotFound(f"{raw!r}: expected <folder>/<file>")
    return Ref(folder, file)


def _confined(app: App, ref: Ref):
    return app.store.path(ref.folder, ref.file)  # raises PathOutsideRepo


def _wallpaper(w: Wallpaper) -> dict:
    d = dataclasses.asdict(w)
    d["resolution"] = [w.resolution.width, w.resolution.height]
    d["crop"] = [w.crop.x, w.crop.y, w.crop.w, w.crop.h] if w.crop else None
    d["processing"] = list(w.processing)
    d["year"] = year_to_toml(w.year) if w.year else None  # 1889 or [1880, 1889]: the UI shows the label
    d["tags"] = list(w.tags)
    return d


def _after_operation(app: App) -> bool:
    """The operation's git states are refreshed in the background; its response waits (bounded) for them, so the
    client's next reads never show the states from before the operation. False when the wait timed out."""
    landed = app.index.wait_for_operation_git()
    if not landed:
        print("wallman: git status after the operation is slow; responding with git state 'pending'", file=sys.stderr)
    return landed


def _describe(app: App, ref: Ref, after_operation: bool = True) -> dict:
    """The entry; after an operation (mutation routes) once its git states landed, or with git "pending" when they
    did not within the bound. A plain read never waits."""
    _confined(app, ref)
    landed = _after_operation(app) if after_operation else True
    entry = app.index.entry(ref)
    if entry is None:
        raise NotFound(f"{ref.folder}/{ref.file} is not listed")
    described = _describe_entry(app, entry)
    if not landed:
        described["git"] = "pending"
    return described


def _describe_entry(app: App, e: IndexedEntry) -> dict:
    """Full entry: every Wallpaper field plus folder, file, tags, git state, issues."""
    w = e.wallpaper
    return {"folder": e.ref.folder, **_wallpaper(w), "shareable": licenses.is_shareable(w.license),
            "git": e.git_state, "external": app.store.is_external(e.ref.folder),
            "issues": [{"code": i.code, "message": i.message, "level": i.level} for i in e.issues]}


def _gallery_item(e: IndexedEntry) -> dict:
    """A list item from the index alone: no path resolution or stat per item (the index resolved them at load)."""
    w = e.wallpaper
    return {"folder": e.ref.folder, "file": e.ref.file, "title": w.title, "artist": w.artist,
            "year": year_to_toml(w.year) if w.year else None, "ratio": e.ratio_bucket, "git": e.git_state,
            "shareable": licenses.is_shareable(w.license), "issues": len(e.issues),
            "thumb": thumb_key_for(e.path, e.mtime_ns, e.size), "external": e.external}


def _box(d: Any) -> CropBox:
    if (not isinstance(d, dict) or set(d) != {"x", "y", "w", "h"}
            or not all(type(v) is int for v in d.values())
            or d["x"] < 0 or d["y"] < 0 or d["w"] <= 0 or d["h"] <= 0):
        raise ValueError("crop must be {x, y, w, h}: integers, x and y >= 0, w and h > 0")
    return CropBox(d["x"], d["y"], d["w"], d["h"])


def _upload_info(up: Upload) -> dict:
    return {"upload_id": up.id, "original_name": up.original_name, "width": up.resolution.width,
            "height": up.resolution.height, "ratio": up.resolution.ratio, "needs_crop": rules.needs_crop(up.resolution)}


def collections(app, m, req):
    rows = [{"folder": c.folder, "medium": c.medium, "movement": c.movement, "count": len(c.wallpapers),
             "external": c.external} for c in app.catalogue.collections()]
    return 200, sorted(rows, key=lambda r: r["folder"])


def _int_param(q: dict[str, str], name: str, default: int, lo: int, hi: int | None = None) -> int:
    raw = q.get(name)
    if raw is None:
        return default
    if not (raw.isascii() and raw.isdigit()):
        raise ValueError(f"{name} must be an integer")
    n = int(raw)
    if n < lo or (hi is not None and n > hi):
        raise ValueError(f"{name} must be between {lo} and {hi if hi is not None else 'infinity'}")
    return n


def list_wallpapers(app, m, req):
    q = req.query
    year = None
    if q.get("year"):
        year = parse_year(q["year"])
        if year is None:
            raise ValueError(f"year {q['year']!r} cannot be parsed")
    sort = q.get("sort", "name")
    if sort not in ("name", "year", "added"):
        raise ValueError("sort must be name, year or added")
    ratio = q.get("ratio") or None
    if ratio is not None and ratio not in BUCKETS:
        raise ValueError(f"ratio must be one of {', '.join(BUCKETS)}")
    issues = q.get("issues", "0")
    if issues not in ("", "0", "1"):
        raise ValueError("issues must be 1 or 0")
    query = Query(folder=q.get("folder") or None, q=q.get("q", ""),
                  tags=normalise_tags(req.query_all.get("tag", [])), license=q.get("license") or None,
                  ratio=ratio, year=year, issues=issues == "1", sort=sort,
                  offset=_int_param(q, "offset", 0, 0), limit=_int_param(q, "limit", 200, 1, 500))
    page = app.index.query(query)
    items = [_gallery_item(e) for e in page.items]
    req.thumbs.prefetch_keyed((it["thumb"], Path(e.path)) for it, e in zip(items, page.items) if e.mtime_ns and e.path)
    return 200, {"items": items, "total": page.total, "offset": page.offset}


def list_tags(app, m, req):
    prefix = req.query.get("prefix", "")
    limit = _int_param(req.query, "limit", 50 if prefix else 500, 1, 500)  # no prefix: the whole tag menu
    return 200, [{"tag": t, "count": n} for t, n in app.index.tags(prefix)[:limit]]


def years_suggest(app, m, req):
    return 200, suggest_years(req.query.get("q", ""))


def list_folders(app, m, req):
    return 200, [{"folder": f, "medium": med, "movement": mov, "external": ext}
                 for f, med, mov, ext in app.index.folder_infos()]


def _refs_body(app: App, body: dict) -> list[Ref]:
    refs = body.get("refs")
    if not isinstance(refs, list) or not all(isinstance(r, dict) and isinstance(r.get("folder"), str)
                                             and isinstance(r.get("file"), str) for r in refs):
        raise ValueError("body must be {refs: [{folder, file}]}")
    parsed = [Ref(r["folder"], r["file"]) for r in refs]
    for r in parsed:
        _confined(app, r)
    return parsed


def _ref_json(r: Ref) -> dict:
    return {"folder": r.folder, "file": r.file}


def git_add(app, m, req):
    refs = _refs_body(app, req.json())
    try:
        done = app.git_actions.add(refs)
    except GitError as e:
        if "ignore" in str(e).lower():
            raise NotShareable("file is listed in .gitignore (not shareable)") from None
        raise
    app.index.refresh_git(done)  # index-only: no file or TOML changed
    _after_operation(app)
    return 200, {"refs": [_ref_json(r) for r in done]}


def git_remove(app, m, req):
    refs = _refs_body(app, req.json())
    done = app.git_actions.remove(refs)
    app.index.refresh_git(done)
    _after_operation(app)
    return 200, {"refs": [_ref_json(r) for r in done]}


def delete_plan(app, m, req):
    plan = app.git_actions.delete_plan(_refs_body(app, req.json()))
    return 200, {k: [_ref_json(r) for r in getattr(plan, k)] for k in ("tracked", "untracked", "external")}


def get_wallpaper(app, m, req):
    return 200, _describe(app, ref_from(m["ref"]), after_operation=False)


def patch_wallpaper(app, m, req):
    ref = ref_from(m["ref"])
    _confined(app, ref)
    changes = req.json()
    if isinstance(changes.get("year"), str):  # what a person types: 1880s, c. 1900, 19th century ...
        span = parse_year(changes["year"])
        if span is None:
            raise ValueError(f"year {changes['year']!r} cannot be parsed")
        changes = {**changes, "year": year_to_toml(span)}
    new = app.library.update(ref, changes)
    return 200, _describe(app, new)


def delete_wallpapers(app, m, req):
    body = req.json()
    git = body.get("git", "remove")
    if git not in ("remove", "keep") or not isinstance(git, str):
        raise ValueError("git must be 'remove' or 'keep'")
    deleted = app.remover.delete(_refs_body(app, body), git=git)
    _after_operation(app)
    return 200, {"deleted": deleted}


def post_upload(app, m, req):
    raw = req.headers.get("X-Filename")
    if not raw:
        raise ValueError("missing X-Filename header")
    name = Path(urllib.parse.unquote(raw)).name
    return 200, _upload_info(app.uploads.from_stream(name, req.stream, req.length))


def post_upload_url(app, m, req):
    url = req.json().get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("body must be {url}")
    up = app.uploads.from_url(url)
    return 200, {**_upload_info(up), "download": up.download}


_NEW_KEYS = {"upload_id", "folder", "title", "artist", "year", "license", "license_detail", "source", "tags", "crop",
             "stage"}


def add_wallpaper(app, m, req):
    b = req.json()
    unknown = set(b) - _NEW_KEYS
    missing = {"upload_id", "folder", "title", "license"} - set(b)
    if unknown or missing:
        raise ValueError(f"unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    stage = b.get("stage", True)
    if not isinstance(stage, bool):
        raise ValueError("stage must be true or false")
    b = {**b, **check_values({k: v for k, v in b.items() if k not in ("crop", "upload_id", "stage")})}
    if not isinstance(b["upload_id"], str):
        raise ValueError("upload_id must be a string")
    upload = app.uploads.get(b["upload_id"])
    meta = NewWallpaper(folder=str(b["folder"]).strip("/"), title=b["title"], artist=b.get("artist"),
                        year=b.get("year"), license=b["license"], license_detail=b.get("license_detail"),
                        source=b.get("source"), crop=_box(b["crop"]) if b.get("crop") is not None else None,
                        tags=b.get("tags") or ())
    _confined(app, Ref(meta.folder, "x"))
    ref = app.importer.add(upload, meta, stage=stage)
    described = _describe(app, ref)
    return 201, {**described, "git_suggestion": _git_suggestion(app, ref, described)}


def _git_suggestion(app: App, ref: Ref, described: dict) -> str | None:
    """"add" when the image is shareable, not tracked yet, and a sibling in its folder is tracked. When the git
    status after the add is still pending (slow git), the image's own state is asked directly."""
    if not described["shareable"] or described["git"] in TRACKED_STATES:
        return None
    if described["git"] == "pending" and app.vcs.is_tracked(app.store.path(ref.folder, ref.file)):
        return None
    siblings = app.index.select(Query(folder=ref.folder))
    tracked = any(e.ref.folder == ref.folder and e.ref != ref and e.git_state in TRACKED_STATES for e in siblings)
    return "add" if tracked else None


def crop_wallpaper(app, m, req):
    ref = ref_from(m["ref"])
    _confined(app, ref)
    app.cropper.crop(ref, _box(req.json()))
    return 200, _describe(app, ref)


def status(app, m, req):
    """Default: rule counts from the library index (validated per folder when it (re)loads) + one git status.
    `full=1` runs the validator over every file instead (reads every image header: seconds at 10,000 images)."""
    only = req.query.get("only")
    if only == "theme":  # the UI's 5 s poll: cheap (no validator run, no git status)
        return 200, {"theme_mtime": app.theme.mtime(), "theme_warnings": app.theme.resolve().warnings,
                     "git_error": app.index.git_error}
    if only is not None:
        raise ValueError("only must be 'theme'")
    full = req.query.get("full", "0")
    if full not in ("", "0", "1"):
        raise ValueError("full must be 1 or 0")
    if full == "1":
        issues = app.validator.run()
        errors, warnings = sum(i.level == "error" for i in issues), sum(i.level == "warning" for i in issues)
    else:
        errors, warnings = app.index.issue_counts()
    return 200, {"changes": [{"repo": c.repo, "status": c.status, "path": c.path} for c in app.vcs.status()],
                 "errors": errors, "warnings": warnings,
                 "theme_mtime": app.theme.mtime(), "theme_warnings": app.theme.resolve().warnings,
                 "git_error": app.index.git_error}


def theme_css(app, m, req):
    css = app.theme.css(req.query.get("scheme", "dark"))
    return 200, Binary(None, "text/css; charset=utf-8", body=css.encode())


def list_licenses(app, m, req):
    return 200, [{"id": i, "label": licenses.LABELS.get(i, i), "shareable": licenses.is_shareable(i),
                  "doc": f"/docs/licenses/{i}"}
                 for i in sorted(licenses.LICENSES)]


def list_media(app, m, req):
    return 200, sorted(rules.MEDIA)


def thumb(app, m, req):
    ref = ref_from(m["ref"])
    image = _confined(app, ref)
    if image.suffix.lower() not in rules.IMAGE_EXTS:
        raise InvalidImage(f"{ref.file} is not an image")
    if not image.is_file():
        raise NotFound(f"{ref.folder}/{ref.file} not found")
    key = req.thumbs.key(image)
    etag = f'"{key}"'
    cache = "public, max-age=31536000, immutable" if req.query.get("v") == key else "no-cache"
    if req.headers.get("If-None-Match") == etag:
        return 304, Binary(None, "image/jpeg", etag, cache, not_modified=True)
    return 200, Binary(req.thumbs.get(image), "image/jpeg", etag, cache)


def image(app, m, req):
    ref = ref_from(m["ref"])
    path = _confined(app, ref)
    if not path.is_file():
        raise NotFound(f"{ref.folder}/{ref.file} not found")
    ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    if path.suffix.lower() not in rules.IMAGE_EXTS:
        raise InvalidImage(f"{ref.file} is not an image")
    return 200, Binary(path, ctype)


Handler = Callable[[App, "re.Match[str]", Request], tuple[int, Any]]
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = [
    (method, re.compile(pattern), fn) for method, pattern, fn in [
        ("GET", r"/api/collections", collections),
        ("GET", r"/api/wallpapers", list_wallpapers),
        ("DELETE", r"/api/wallpapers", delete_wallpapers),
        ("POST", r"/api/wallpapers", add_wallpaper),
        ("POST", r"/api/wallpapers/delete-plan", delete_plan),
        ("GET", r"/api/tags", list_tags),
        ("GET", r"/api/years/suggest", years_suggest),
        ("GET", r"/api/folders", list_folders),
        ("POST", r"/api/git/add", git_add),
        ("POST", r"/api/git/remove", git_remove),
        ("POST", r"/api/wallpapers/(?P<ref>.+)/crop", crop_wallpaper),
        ("GET", r"/api/wallpapers/(?P<ref>.+)", get_wallpaper),
        ("PATCH", r"/api/wallpapers/(?P<ref>.+)", patch_wallpaper),
        ("POST", r"/api/uploads", post_upload),
        ("POST", r"/api/uploads/url", post_upload_url),
        ("GET", r"/api/status", status),
        ("GET", r"/api/theme", theme_css),
        ("GET", r"/api/licenses", list_licenses),
        ("GET", r"/api/media", list_media),
        ("GET", r"/api/thumbs/(?P<ref>.+)", thumb),
        ("GET", r"/api/images/(?P<ref>.+)", image),
    ]
]
