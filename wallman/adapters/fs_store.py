"""Filesystem ImageStore confined to the collection root (<repo>/images)."""
from __future__ import annotations

import os
from pathlib import Path

from ..domain.rules import IMAGE_EXTS as _IMAGE_EXTS, MEDIA, TOML_NAME
from ..errors import PathOutsideRepo


class FsStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._dirs: dict[Path, tuple[int, tuple[bool, bool, list[str]]]] = {}  # see _dir_info

    def _confine(self, p: Path) -> Path:
        resolved = p.resolve()
        if not resolved.is_relative_to(self.root):
            raise PathOutsideRepo(f"{p} is outside the repository")
        return resolved

    def resolve(self, folder: str, file: str = "") -> Path:
        for part in (folder, file):
            if os.path.isabs(part) or ".." in Path(part).parts:
                raise PathOutsideRepo(f"{part!r} is not a path inside the repository")
        return self._confine(self.root / folder / file)

    def path(self, folder: str, file: str) -> Path:
        return self.resolve(folder, file)

    def images(self, folder: str) -> list[str]:
        d = self.resolve(folder)
        if not d.is_dir():
            return []
        with os.scandir(d) as it:  # DirEntry type checks avoid a stat per file (10,000-image folders)
            return sorted(e.name for e in it if os.path.splitext(e.name)[1].lower() in _IMAGE_EXTS and e.is_file())

    def move(self, src: Path, dst: Path) -> None:
        src, dst = self._confine(Path(src)), self._confine(Path(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    def delete(self, path: Path) -> None:
        # unlink the entry itself; confine its parent so a symlink is removed, not its target
        p = Path(path)
        self._confine(p.parent)
        self._confine(p)
        p.unlink()

    def remove_dir(self, folder: str) -> None:
        d = self.resolve(folder)
        try:
            d.rmdir()
        except OSError:
            pass  # not empty (or already gone): leave it alone

    def is_external(self, folder: str) -> bool:
        return (self.resolve(folder) / ".git").exists()

    def _dir_info(self, d: Path) -> tuple[bool, bool, list[str]]:
        """(holds a wallpapers.toml, holds .git, sorted subdirectory names) of `d`, cached while its mtime is
        unchanged: creating, deleting or renaming any entry of a directory changes its mtime, and listing a
        10,000-image folder to find its (no) subdirectories costs milliseconds on every call.

        Assumes nanosecond directory mtimes and that st_nlink counts subdirectories when it is 2 or more (ext4, xfs,
        tmpfs; btrfs reports 1 and is always listed). On a coarse-mtime filesystem (FAT, some FUSE or network
        mounts) a change made within the same timestamp tick as the previous one is seen at the next change."""
        st = os.stat(d)
        mtime = st.st_mtime_ns
        hit = self._dirs.get(d)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        if st.st_nlink == 2:  # "." and the parent's entry only: no subdirectory (filesystems that do not count
            children = []     # subdirectories, such as btrfs, report 1 and are listed)
        else:
            with os.scandir(d) as it:  # is_dir(follow_symlinks=False): no stat per image file
                children = sorted(e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False))
        info = ((d / TOML_NAME).is_file(), (d / ".git").exists(), children)
        self._dirs[d] = (mtime, info)
        return info

    def collection_folders(self) -> list[str]:
        found: list[str] = []

        def walk(d: Path) -> None:
            try:
                has_toml, has_git, children = self._dir_info(d)
            except (FileNotFoundError, NotADirectoryError):  # vanished during the walk
                return
            if has_toml:
                found.append(d.relative_to(self.root).as_posix())
            if has_git:
                return
            for name in children:
                walk(d / name)

        for name in sorted(MEDIA):
            top = self.root / name
            if top.is_dir() and not top.is_symlink():
                walk(top)
        return sorted(found)

    def image_folders(self) -> list[str]:
        """MEDIA folders that directly hold images; same skip rules as collection_folders."""
        found: list[str] = []

        def walk(d: Path) -> None:
            has_toml = (d / TOML_NAME).is_file()
            if (d / ".git").exists():
                if has_toml and any(p.is_file() and p.suffix.lower() in _IMAGE_EXTS for p in d.iterdir()):
                    found.append(d.relative_to(self.root).as_posix())
                return
            if any(p.is_file() and p.suffix.lower() in _IMAGE_EXTS for p in d.iterdir()):
                found.append(d.relative_to(self.root).as_posix())
            for child in sorted(d.iterdir()):
                if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                    continue
                walk(child)

        for name in sorted(MEDIA):
            top = self.root / name
            if top.is_dir() and not top.is_symlink():
                walk(top)
        return sorted(found)
