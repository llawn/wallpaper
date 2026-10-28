"""`python3 -m wallman <command>`; exit codes: 0 ok, 1 validation/guard refusal, 2 usage, 3 error."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import bootstrap
from ..domain.models import CropBox, Ref
from ..domain.rules import MEDIA_DIR
from ..errors import WallmanError
from ..ports import Fetcher
from ..services.importer import NewWallpaper


def find_root(start: Path) -> Path:
    """Nearest ancestor of `start` holding both a `wallman/` package and `.git`; else `start`."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / "wallman").is_dir() and (d / ".git").exists():
            return d
    return start


def _crop(text: str) -> CropBox:
    try:
        x, y, w, h = (int(p) for p in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("expected x,y,w,h (four integers)") from None
    return CropBox(x, y, w, h)


def _rel(text: str) -> str:
    """Folder/ref text relative to images/; a leading `images/` is accepted and stripped."""
    text = text.strip("/")
    return text.removeprefix(f"{MEDIA_DIR}/") if text.startswith(f"{MEDIA_DIR}/") else text


def _ref(text: str) -> Ref:
    folder, _, file = _rel(text).rpartition("/")
    if not folder or not file:
        raise argparse.ArgumentTypeError(f"{text!r}: expected <folder>/<file>")
    return Ref(folder, file)


def _meta_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("dest", metavar="DEST", help="target folder under images/, e.g. painting/impressionism")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--year", type=int)
    p.add_argument("--license", required=True)
    p.add_argument("--license-detail")
    p.add_argument("--source")
    p.add_argument("--crop", type=_crop, metavar="x,y,w,h")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wallman", description="Manage the wallpaper collection.")
    ap.add_argument("--root", type=Path, help="repository root (default: found from the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check every collection against the rules")
    p = sub.add_parser("sync", help="bring wallpapers.toml in line with the images")
    p.add_argument("--stage", action="store_true", help="stage the changes in git")
    sub.add_parser("guard", help="refuse staged images that may not be redistributed")
    p = sub.add_parser("fetch", help="download the files that are not in git")
    p.add_argument("folders", nargs="*", help="folders relative to images/")
    p = sub.add_parser("add", help="add an image file")
    p.add_argument("file", metavar="FILE")
    _meta_args(p)
    p = sub.add_parser("add-url", help="download an image and add it")
    p.add_argument("url", metavar="URL")
    _meta_args(p)
    p = sub.add_parser("rm", help="delete wallpapers (metadata and git included)")
    p.add_argument("refs", nargs="+", type=_ref, metavar="REF")
    p = sub.add_parser("mv", help="move a wallpaper to another folder")
    p.add_argument("ref", type=_ref, metavar="REF")
    p.add_argument("dest", metavar="DEST")
    p = sub.add_parser("crop", help="crop a wallpaper (from its original download when it has one)")
    p.add_argument("ref", type=_ref, metavar="REF")
    p.add_argument("box", type=_crop, metavar="x,y,w,h")
    sub.add_parser("gitignore", help="rewrite the generated block of .gitignore")
    sub.add_parser("readme", help="regenerate README.md")
    sub.add_parser("theme-init", help="write a commented default web UI theme config (never overwrites)")
    p = sub.add_parser("serve", help="open the web UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true")
    return ap


def _meta(a: argparse.Namespace) -> NewWallpaper:
    return NewWallpaper(folder=_rel(a.dest), title=a.title, artist=a.artist, year=a.year, license=a.license,
                        license_detail=a.license_detail, source=a.source, crop=a.crop)


def _validate(app) -> int:
    issues = app.validator.run()
    errors = 0
    for i in issues:
        mark = "✗" if i.level == "error" else "!"
        errors += i.level == "error"
        where = f"{i.folder}/{i.file}" if i.file else i.folder
        print(f"{mark} {where}: {i.code} — {i.message}")
    print(f"{len(app.store.collection_folders())} folders checked, {errors} problem(s)")
    return 1 if errors else 0


def _run(a: argparse.Namespace, app) -> int:
    cmd = a.cmd
    if cmd == "validate":
        return _validate(app)
    if cmd == "sync":
        r = app.syncer.sync(a.stage)
        for ref in r.skipped:
            print(f"! {ref.folder}/{ref.file}: skipped, ImageMagick cannot read it")
        for folder, why in r.broken:
            print(f"! {folder}: skipped, {why}; fix that wallpapers.toml first")
        print(f"sync: {len(r.removed)} removed, {len(r.added)} added, {len(r.renamed)} renamed, "
              f"{len(r.skipped)} skipped")
    elif cmd == "guard":
        lines = app.gitignore.check_staged(app.vcs.staged())
        if lines:
            print("\n".join(lines))
            return 1
        print("guard: ok")
    elif cmd == "fetch":
        r = app.fetch_service.fetch([_rel(f) for f in a.folders])
        for ref, why in r.failed:
            print(f"✗ {ref.folder}/{ref.file}: {why}", file=sys.stderr)
        print(f"fetch: {r.downloaded} downloaded, {len(r.failed)} failed")
        return 1 if r.failed else 0
    elif cmd in ("add", "add-url"):
        upload = (app.uploads.from_bytes(Path(a.file).name, Path(a.file).read_bytes()) if cmd == "add"
                  else app.uploads.from_url(a.url))
        try:
            ref = app.importer.add(upload, _meta(a))
        except BaseException:
            app.uploads.discard(upload.id)
            raise
        print(f"{ref.folder}/{ref.file}")
    elif cmd == "rm":
        print(f"removed {app.remover.delete(a.refs)} wallpaper(s)")
    elif cmd == "mv":
        ref = app.library.update(a.ref, {"folder": _rel(a.dest)})
        print(f"{ref.folder}/{ref.file}")
    elif cmd == "crop":
        w = app.cropper.crop(a.ref, a.box)
        print(f"{a.ref.folder}/{a.ref.file}: {w.resolution.width}x{w.resolution.height}")
    elif cmd == "gitignore":
        print(f"gitignore: {app.gitignore.write()} restricted file(s) listed")
    elif cmd == "readme":
        app.readme.write()
        print("README.md written")
    elif cmd == "serve":
        try:
            from ..web.server import serve
        except ModuleNotFoundError as e:
            if e.name != "wallman.web.server":
                raise
            print("error: web UI not installed yet", file=sys.stderr)
            return 3
        serve(app, port=a.port, open_browser=not a.no_open)
    return 0


def _theme_init() -> int:
    from ..services.theme import config_paths, load_builtin, write_default_config
    path = config_paths()[0]
    try:
        write_default_config(path, load_builtin())
    except FileExistsError:
        print(f"error: {path} already exists; edit it or delete it first", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(f"theme config written: {path}")
    return 0


def main(argv: list[str], *, fetcher: Fetcher | None = None, cache_dir: Path | None = None) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if a.cmd == "theme-init":
        return _theme_init()
    try:
        app = bootstrap.build(a.root if a.root else find_root(Path.cwd()), cache_dir, fetcher)
        return _run(a, app)
    except (WallmanError, OSError) as e:
        print(f"error: {str(e).removeprefix('error: ')}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
