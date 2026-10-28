"""Catalogue adapter: one wallpapers.toml per folder, read with tomllib, written by hand to keep the format stable."""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import tomllib
from pathlib import Path

from ..domain.models import Collection, CropBox, Issue, Resolution, Wallpaper
from ..domain.naming import slug
from ..domain.rules import TOML_NAME
from ..domain.years import year_from_toml, year_to_toml
from ..errors import NameConflict, NotFound, WallmanError
from ..ports import ImageStore

FILENAME = TOML_NAME
SCHEMA_PATH = "wallman/schema/wallpapers.schema.json"
COLLECTION_MARKER = "[collection]"
PERSONAL_TODO = "  # TODO: set the real license (and source) to share it"

# key -> expected kind; required keys are listed separately (canonical write order = this order)
ENTRY_FIELDS = {
    "file": "str", "title": "str", "artist": "str", "year": "year", "tags": "strs", "license": "str", "license_detail": "str",
    "source": "str", "download": "str", "sha256": "str", "resolution": "size", "processing": "strs",
    "crop": "int4", "original": "str",
}
ENTRY_REQUIRED = ("file", "title", "artist", "license", "resolution")
COLLECTION_FIELDS = {"medium": "str", "movement": "str", "count": "int"}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _kind_ok(kind: str, v: object) -> bool:
    if kind == "str":
        return isinstance(v, str)
    if kind == "int":
        return _is_int(v)
    if kind in ("int2", "int4"):
        return isinstance(v, list) and len(v) == int(kind[3:]) and all(_is_int(x) for x in v)
    if kind == "size":  # [width, height], both > 0 (a zero would divide by zero in the ratio rules)
        return _kind_ok("int2", v) and all(x > 0 for x in v)
    if kind == "year":  # 1889 or [1880, 1889]; range validity belongs to the rules
        return _is_int(v) or _kind_ok("int2", v)
    if kind == "strs":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


def _problems(table: dict, fields: dict[str, str], required: tuple[str, ...]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key in required:
        if key not in table:
            out.append(("missing-field", f"missing required field {key!r}"))
    for key, value in table.items():
        if key not in fields:
            out.append(("unknown-field", f"unknown field {key!r}"))
        elif not _kind_ok(fields[key], value):
            kind = {"size": "[width, height] of positive integers",
                    "year": "integer or [start, end] of integers"}.get(fields[key], fields[key])
            out.append(("field-type", f"field {key!r} must be of type {kind}"))
    return out


def _quote(s: str) -> str:
    if s.isprintable() and '"' not in s and "\\" not in s:  # nothing to escape (the common case, 5x faster)
        return f'"{s}"'
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _ints(values) -> str:
    return "[" + ", ".join(str(int(v)) for v in values) + "]"


def _year(span) -> str:
    v = year_to_toml(span)
    return str(v) if isinstance(v, int) else _ints(v)


def _entry_lines(w: Wallpaper) -> list[str]:
    lic = f"license = {_quote(w.license)}" + (PERSONAL_TODO if w.license == "personal" else "")
    pairs: list[str | None] = [
        "[[wallpaper]]",
        f"file = {_quote(w.file)}",
        f"title = {_quote(w.title)}",
        f"artist = {_quote(w.artist)}",
        None if w.year is None else f"year = {_year(w.year)}",
        "tags = [" + ", ".join(_quote(t) for t in sorted(set(w.tags))) + "]" if w.tags else None,
        lic,
        None if w.license_detail is None else f"license_detail = {_quote(w.license_detail)}",
        None if w.source is None else f"source = {_quote(w.source)}",
        None if w.download is None else f"download = {_quote(w.download)}",
        None if w.sha256 is None else f"sha256 = {_quote(w.sha256)}",
        f"resolution = {_ints((w.resolution.width, w.resolution.height))}",
        "processing = [" + ", ".join(_quote(p) for p in w.processing) + "]" if w.processing else None,
        None if w.crop is None else f"crop = {_ints((w.crop.x, w.crop.y, w.crop.w, w.crop.h))}",
        None if w.original is None else f"original = {_quote(w.original)}",
    ]
    return [p for p in pairs if p is not None]


def _wallpaper(e: dict) -> Wallpaper:
    crop = e.get("crop")
    return Wallpaper(
        file=e["file"], title=e["title"], artist=e["artist"], license=e["license"],
        resolution=Resolution(*e["resolution"]), year=year_from_toml(e.get("year")), license_detail=e.get("license_detail"),
        source=e.get("source"), download=e.get("download"), sha256=e.get("sha256"),
        processing=tuple(e.get("processing", ())), original=e.get("original"),
        crop=CropBox(*crop) if crop is not None else None, tags=tuple(e.get("tags", ())),
    )


def _header(text: str) -> str:
    """The comment lines before [collection] (kept by save)."""
    idx = text.find(COLLECTION_MARKER)
    return text[:idx] if idx > 0 and (text[idx - 1] == "\n") else ""


class UnreadableToml(WallmanError):
    """wallpapers.toml exists but cannot be read (permissions, I/O) or is not UTF-8."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code  # issue code: "unreadable" or "invalid-toml"


@dataclasses.dataclass(frozen=True)
class _Parsed:
    """What one read of a wallpapers.toml gives, kept per folder while the file's identity is unchanged."""
    key: tuple  # (path, st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)
    # template: load() hands out copies with a new list (Wallpaper is immutable); None when the TOML does not parse
    collection: Collection | None
    issues: tuple[Issue, ...]  # structural issues
    error: str = ""  # the parse error when collection is None


def _file_key(path: Path, st: os.stat_result) -> tuple:
    return (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


@dataclasses.dataclass(frozen=True)
class _Block:
    """One entry as save() writes it."""
    wallpaper: Wallpaper
    text: str  # its [[wallpaper]] table
    written: Wallpaper  # what parsing `text` back gives: tags are written sorted and unique
    clean: bool  # structurally clean by construction (see of())

    @staticmethod
    def of(w: Wallpaper) -> "_Block":
        """`clean`: every value written comes from a typed Wallpaper, so only values that do not fit their declared
        types could make the table invalid; then save() can skip parsing the file back. Year and crop values are
        written through int() and assumed to be ints already (every producer validates them: API year parsing,
        _box, centered_crop); a non-int there would make the cached entry differ from a parse."""
        r = w.resolution
        clean = (type(w.file) is str and type(w.title) is str and type(w.artist) is str and type(w.license) is str
                 and type(r.width) is int and type(r.height) is int and r.width > 0 and r.height > 0)
        tags = tuple(sorted(set(w.tags)))
        return _Block(w, "\n".join(_entry_lines(w)), w if w.tags == tags else dataclasses.replace(w, tags=tags), clean)


class TomlCatalogue:
    def __init__(self, root: Path, store: ImageStore) -> None:
        self.root = Path(root)
        self.store = store
        # folder -> last parse, valid while the file's identity (inode, size, mtime, ctime) is unchanged: a 10,000-entry
        # wallpapers.toml takes ~0.5 s to parse, and an edit reads it several times (load, structural checks, index,
        # .gitignore, README). save() stores what it wrote, so the next read does not parse it back either.
        self._cache: dict[str, _Parsed] = {}
        self._blocks: dict[str, dict[int, _Block]] = {}  # folder -> id(Wallpaper) -> its text, from the last save

    def _toml(self, folder: str) -> Path:
        return self.root / folder / FILENAME

    def _parsed(self, folder: str) -> _Parsed:
        path = self._toml(folder)
        try:
            with open(path, encoding="utf-8") as f:  # text mode: same newline handling as read_text
                key = _file_key(path, os.fstat(f.fileno()))
                hit = self._cache.get(folder)
                if hit is not None and hit.key == key:
                    return hit
                text = f.read()
        except FileNotFoundError:
            self._cache.pop(folder, None)
            raise NotFound(f"{folder}/{FILENAME} not found") from None
        except UnicodeDecodeError as e:
            raise UnreadableToml(f"{folder}/{FILENAME} is not UTF-8: {e}", "invalid-toml") from e
        except OSError as e:
            raise UnreadableToml(f"{folder}/{FILENAME} cannot be read: {e.strerror or e}", "unreadable") from e
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            parsed = _Parsed(key, None, (Issue(folder, None, "invalid-toml", f"cannot parse {FILENAME}: {e}", "error"),),
                             str(e))
        else:
            parsed = _Parsed(key, self._collection(folder, text, data), tuple(self._structural(folder, data)))
        self._cache[folder] = parsed
        return parsed

    def collections(self) -> list[Collection]:
        out = []
        for folder in self.store.collection_folders():
            try:
                out.append(self.load(folder))
            except (NotFound, WallmanError):
                continue  # missing or unparsable: structural_issues reports it
        return out

    def load(self, folder: str) -> Collection:
        parsed = self._parsed(folder)
        if parsed.collection is None:
            raise WallmanError(f"{folder}/{FILENAME}: invalid TOML: {parsed.error}")
        return dataclasses.replace(parsed.collection, wallpapers=list(parsed.collection.wallpapers),
                                   external=self.store.is_external(folder))  # a submodule may appear later

    def _collection(self, folder: str, text: str, data: dict) -> Collection:
        col = data.get("collection") if isinstance(data.get("collection"), dict) else {}
        entries = data.get("wallpaper") if isinstance(data.get("wallpaper"), list) else []
        wallpapers = [
            _wallpaper(e) for e in entries
            if isinstance(e, dict) and not _problems(e, ENTRY_FIELDS, ENTRY_REQUIRED)
        ]
        return Collection(
            folder=folder, medium=str(col.get("medium", "")), movement=str(col.get("movement", "")),
            wallpapers=wallpapers, header=_header(text), external=self.store.is_external(folder),
        )

    def _refuse_if_broken(self, folder: str) -> None:
        """Ruling 7 / C1: entries load() skipped as invalid would be lost by a save or a delete."""
        problems = self.structural_issues(folder)
        if problems:
            raise WallmanError(
                f"{folder}/{FILENAME} has {len(problems)} structural problem(s), "
                f"e.g. {problems[0].code}: {problems[0].message}; fix that wallpapers.toml first (nothing was written)")

    def save(self, collection: Collection) -> None:
        folder = collection.folder
        if self._toml(folder).exists():
            self._refuse_if_broken(folder)
        header = collection.header
        if header and not header.endswith("\n"):
            header += "\n"
        parts = [COLLECTION_MARKER, f"medium = {_quote(collection.medium)}", f"movement = {_quote(collection.movement)}",
                 f"count = {len(collection.wallpapers)}"]
        # each entry's text is kept per Wallpaper object (immutable): saving a 10,000-entry folder after one edit
        # renders one entry, not 10,000
        prev = self._blocks.get(folder, {})
        blocks: dict[int, _Block] = {}
        written: list[Wallpaper] = []
        clean = True
        for w in collection.wallpapers:
            b = prev.get(id(w))
            if b is None or b.wallpaper is not w:
                b = blocks.get(id(w)) or _Block.of(w)
            blocks[id(w)] = b
            parts.append(b.text)
            written.append(b.written)
            clean = clean and b.clean
        text = header + "\n".join(parts) + "\n"
        path = self._toml(folder)
        self._cache.pop(folder, None)
        self._blocks[folder] = blocks
        self._atomic_write(path, text)
        if clean:
            try:
                key = _file_key(path, os.stat(path))
            except OSError:
                return
            self._cache[folder] = _Parsed(key, Collection(
                folder=folder, medium=collection.medium, movement=collection.movement, wallpapers=written,
                header=_header(text), external=self.store.is_external(folder)), ())

    def create(self, folder: str) -> Collection:
        if self._toml(folder).exists():
            raise NameConflict(f"{folder}/{FILENAME} already exists")
        parts = folder.split("/")
        schema = "../" * (len(parts) + 1) + SCHEMA_PATH  # SCHEMA_PATH is relative to the repo root, folders to images/
        c = Collection(folder=folder, medium=parts[0], movement=slug(parts[-1]),
                       header=f"#:schema {schema}\n\n", external=self.store.is_external(folder))
        self.save(c)
        return c

    def delete(self, folder: str) -> None:
        self._refuse_if_broken(folder)  # raises NotFound when there is no toml
        self._cache.pop(folder, None)
        self._blocks.pop(folder, None)
        try:
            self._toml(folder).unlink()
        except FileNotFoundError:
            raise NotFound(f"{folder}/{FILENAME} not found") from None

    def structural_issues(self, folder: str) -> list[Issue]:
        try:
            return list(self._parsed(folder).issues)
        except UnreadableToml as e:
            return [Issue(folder, None, e.code, str(e), "error")]

    @staticmethod
    def _structural(folder: str, data: dict) -> list[Issue]:
        def issue(file: str | None, code: str, message: str) -> Issue:
            return Issue(folder, file, code, message, "error")

        issues: list[Issue] = []
        for key in data:
            if key not in ("collection", "wallpaper"):
                issues.append(issue(None, "unknown-field", f"unknown top-level key {key!r}"))
        col = data.get("collection")
        if not isinstance(col, dict):
            issues.append(issue(None, "missing-field", "missing [collection] table"))
            col = {}
        else:
            issues += [issue(None, c, f"collection: {m}") for c, m in _problems(col, COLLECTION_FIELDS, tuple(COLLECTION_FIELDS))]
        entries = data.get("wallpaper", [])
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            issues.append(issue(None, "field-type", "wallpaper must be an array of tables"))
            return issues
        if _is_int(col.get("count")) and col["count"] != len(entries):
            issues.append(issue(None, "count", f"count = {col['count']} but {len(entries)} entries"))
        for e in entries:
            name = e.get("file") if isinstance(e.get("file"), str) else None
            issues += [issue(name, c, m) for c, m in _problems(e, ENTRY_FIELDS, ENTRY_REQUIRED)]
        return issues

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".wallpapers-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
