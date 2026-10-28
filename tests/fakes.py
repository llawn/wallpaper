"""In-memory fakes of the ports, shared by the service tests."""
from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath

from wallman.domain.models import Change, Collection, CropBox, Issue, Resolution
from wallman.domain.rules import IMAGE_EXTS
from wallman.errors import DownloadFailed, InvalidImage, NotFound, WallmanError



class FakeCatalogue:
    def __init__(self, collections: list[Collection] | None = None) -> None:
        self.data: dict[str, Collection] = {c.folder: c for c in collections or []}
        self.raw: dict[str, bytes] = {f: b"" for f in self.data}
        self.issues: dict[str, list[Issue]] = {}  # structural_issues per folder; [] by default
        self.saved: list[str] = []
        self.root: Path | None = None  # set: save() also writes the rendered TOML under it (and delete() removes it)

    def collections(self) -> list[Collection]:
        return [copy.deepcopy(self.data[f]) for f in sorted(self.data)]

    def load(self, folder: str) -> Collection:
        """A copy, like the real catalogue: changes reach the catalogue only through save()."""
        if folder not in self.data:
            raise NotFound(f"{folder}/wallpapers.toml not found")
        return copy.deepcopy(self.data[folder])

    def _refuse_if_broken(self, folder: str) -> None:
        if self.issues.get(folder):  # same refusal as TomlCatalogue (Ruling 7, C1)
            raise WallmanError(f"{folder}/wallpapers.toml has structural problems; nothing was written")

    def save(self, collection: Collection) -> None:
        self._refuse_if_broken(collection.folder)
        self.data[collection.folder] = copy.deepcopy(collection)
        self.raw[collection.folder] = f"saved:{len(collection.wallpapers)}".encode()
        self.saved.append(collection.folder)
        if self.root is not None:
            path = self.root / collection.folder / "wallpapers.toml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.render(collection))

    def create(self, folder: str) -> Collection:
        parts = folder.split("/")
        c = Collection(folder=folder, medium=parts[0], movement=parts[-1])
        self.save(c)
        return c

    def delete(self, folder: str) -> None:
        if folder not in self.data:
            raise NotFound(f"{folder}/wallpapers.toml not found")
        self._refuse_if_broken(folder)
        del self.data[folder]
        self.raw.pop(folder, None)
        if self.root is not None:
            (self.root / folder / "wallpapers.toml").unlink(missing_ok=True)

    def structural_issues(self, folder: str) -> list[Issue]:
        return list(self.issues.get(folder, []))

    # the TOML text format is the real one: the generated index TOML is checked by content
    def render(self, collection: Collection) -> bytes:
        from wallman.adapters.toml_catalogue import render_text
        return render_text(collection).encode()

    def render_parts(self, collection: Collection):
        from wallman.adapters.toml_catalogue import entry_text
        return self.render(collection), {w.file: entry_text(w) for w in collection.wallpapers}

    def entry_text(self, w) -> str:
        from wallman.adapters.toml_catalogue import entry_text
        return entry_text(w)

    def blob_entries(self, data: bytes) -> dict[str, str]:
        from wallman.adapters.toml_catalogue import blob_entries
        return blob_entries(data)


class FakeStore:
    def __init__(self, files: dict[str, set[str]] | None = None, external: set[str] | None = None,
                 root: Path = Path("/fake")) -> None:
        self.files: dict[str, set[str]] = {f: set(v) for f, v in (files or {}).items()}
        self.external: set[str] = set(external or ())
        self.root = root
        self.calls: list[tuple] = []

    def images(self, folder: str) -> list[str]:
        return sorted(f for f in self.files.get(folder, set()) if f.lower().endswith(IMAGE_EXTS))

    def path(self, folder: str, file: str) -> Path:
        return self.root / folder / file

    def _split(self, p: Path) -> tuple[str, str]:
        rel = PurePosixPath(Path(p).relative_to(self.root).as_posix())
        return rel.parent.as_posix(), rel.name

    def move(self, src: Path, dst: Path) -> None:
        self.calls.append(("move", src, dst))
        sf, sn = self._split(src)
        self.files.get(sf, set()).discard(sn)
        df, dn = self._split(dst)
        self.files.setdefault(df, set()).add(dn)
        if Path(src).is_file():
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(src).replace(dst)

    def delete(self, path: Path) -> None:
        self.calls.append(("delete", path))
        f, n = self._split(path)
        self.files.get(f, set()).discard(n)
        if Path(path).is_file():  # roots that exist on disk (service tests) stay in step with the in-memory view
            Path(path).unlink()

    def remove_dir(self, folder: str) -> None:
        self.calls.append(("remove_dir", folder))
        if not self.files.get(folder):
            self.files.pop(folder, None)

    def is_external(self, folder: str) -> bool:
        return folder in self.external

    def collection_folders(self) -> list[str]:
        return sorted(self.files)

    def image_folders(self) -> list[str]:
        return sorted(f for f in self.files if self.images(f))


class FakeImageTool:
    def __init__(self, sizes: dict[Path, Resolution] | None = None, default: Resolution | None = None) -> None:
        self.sizes: dict[Path, Resolution] = dict(sizes or {})
        self.default = default  # answer for paths not in `sizes` (e.g. uploads with random names)
        self.calls: list[tuple] = []

    def identify(self, path: Path) -> Resolution:
        try:
            return self.sizes[Path(path)]
        except KeyError:
            if self.default is not None:
                return self.default
            raise InvalidImage(f"{path}: not an image") from None

    def crop(self, src: Path, box: CropBox, dst: Path) -> None:
        self.calls.append(("crop", src, box, dst))
        self.sizes[Path(dst)] = Resolution(box.w, box.h)
        if Path(dst).parent.is_dir():
            Path(dst).write_bytes(b"cropped:" + f"{box.x},{box.y},{box.w},{box.h}".encode())

    def convert(self, src: Path, dst: Path) -> None:
        self.calls.append(("convert", src, dst))
        self.sizes[Path(dst)] = self.identify(src)
        if Path(dst).parent.is_dir():
            Path(dst).write_bytes(b"converted")

    render_size = Resolution(3840, 2160)  # what render_svg produces (the real one fits 3840x2160)

    def render_svg(self, src: Path, dst: Path) -> None:
        self.calls.append(("render_svg", src, dst))
        self.sizes[Path(dst)] = self.render_size
        if Path(dst).parent.is_dir():
            Path(dst).write_bytes(b"rendered")

    def thumbnail(self, src: Path, dst: Path, width: int, quality: int = 82) -> None:
        self.calls.append(("thumbnail", src, dst, width))


class FakeVcs:
    def __init__(self, tracked: set[Path] | None = None) -> None:
        self.calls: list[tuple] = []
        self.tracked: set[Path] = set(tracked or ())
        self.changes: list[Change] = []
        self.staged_files: list[Path] = []
        self.states: dict[Path, str] = {}  # explicit git states; others: committed if tracked else untracked
        self.index: dict[Path, bytes] = {}  # index blobs (the generated wallpapers.toml)
        self.head: dict[Path, bytes] = {}  # HEAD blobs
        # blob_ids answers None (per-file states only, as outside a work tree) unless a test models the TOML blobs
        self.entry_states = False

    def stage_add(self, paths: list[Path], force: bool = False) -> None:
        self.calls.append(("stage_add", list(paths)) + (("force",) if force else ()))
        self.tracked.update(paths)
        for p in paths:
            if Path(p).name == "wallpapers.toml" and Path(p).is_file():
                self.index[Path(p)] = Path(p).read_bytes()

    def stage_rm(self, paths: list[Path]) -> None:
        self.calls.append(("stage_rm", list(paths)))
        self.tracked.difference_update(paths)

    def stage_mv(self, src: Path, dst: Path) -> None:
        self.calls.append(("stage_mv", src, dst))
        self.tracked.discard(src)
        self.tracked.add(dst)

    def untrack(self, paths: list[Path]) -> None:
        self.calls.append(("untrack", list(paths)))
        self.tracked.difference_update(paths)

    def is_tracked(self, path: Path) -> bool:
        return Path(path) in self.tracked

    def staged(self) -> list[Path]:
        return list(self.staged_files)

    def file_states(self, paths: list[Path]) -> dict[Path, str]:
        return {Path(p): self.states.get(Path(p), "committed" if Path(p) in self.tracked else "untracked")
                for p in paths}

    def status(self) -> list[Change]:
        self.calls.append(("status",))
        return list(self.changes)

    def index_blob(self, path: Path) -> bytes | None:
        return self.index.get(Path(path))

    def head_blob(self, path: Path) -> bytes | None:
        return self.head.get(Path(path))

    def index_blob_id(self, path: Path) -> str | None:
        return self._id(self.index.get(Path(path)))

    def stage_blob(self, path: Path, data: bytes) -> None:
        self.calls.append(("stage_blob", Path(path)))
        self.index[Path(path)] = bytes(data)

    def unstage_path(self, path: Path) -> None:
        self.calls.append(("unstage_path", Path(path)))
        self.index.pop(Path(path), None)

    @staticmethod
    def _id(data: bytes | None) -> str | None:
        import hashlib
        return None if data is None else hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def blob_ids(self, paths: list[Path]):
        if not self.entry_states:
            return None
        self.calls.append(("blob_ids", len(paths)))
        return {Path(p): (self._id(self.index.get(Path(p))), self._id(self.head.get(Path(p)))) for p in paths}

    def read_blob(self, blob_id: str) -> bytes:
        self.calls.append(("read_blob", blob_id))
        for data in [*self.index.values(), *self.head.values()]:
            if self._id(data) == blob_id:
                return data
        raise KeyError(blob_id)


class FakeFetcher:
    def __init__(self, responses: dict[str, bytes | Exception] | None = None) -> None:
        self.responses: dict[str, bytes | Exception] = dict(responses or {})
        self.calls: list[str] = []

    def get(self, url: str) -> tuple[bytes, str]:
        import hashlib
        self.calls.append(url)
        r = self.responses.get(url, DownloadFailed(f"{url}: no response programmed"))
        if isinstance(r, Exception):
            raise r
        return r, hashlib.sha256(r).hexdigest()
