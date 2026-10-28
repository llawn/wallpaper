"""ImageMagick / rsvg-convert adapter; subprocesses never use a shell."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..domain.models import CropBox, Resolution
from ..errors import InvalidImage
from .image_header import read_size


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise InvalidImage(f"{cmd[0]} is not installed") from e


def _check(cmd: list[str]) -> None:
    r = _run(cmd)
    if r.returncode != 0:
        raise InvalidImage(f"{cmd[0]} failed: {r.stderr.strip()}")


class Magick:
    def identify(self, path: Path | str) -> Resolution:
        # Fast path: JPEG/PNG header; spawn magick only when it cannot tell.
        try:
            size = read_size(path)
        except OSError:
            size = None
        if size is not None:
            return size
        r = _run(["magick", "identify", "-format", "%w %h\n", f"{path}[0]"])
        if r.returncode != 0:
            raise InvalidImage(f"cannot identify {path}: {r.stderr.strip()}")
        try:
            w, h = r.stdout.split()[:2]
            return Resolution(int(w), int(h))
        except (ValueError, IndexError) as e:
            raise InvalidImage(f"unparsable identify output for {path}: {r.stdout!r}") from e

    def crop(self, src: Path, box: CropBox, dst: Path) -> None:
        _check(["magick", f"{src}[0]", "-crop", f"{box.w}x{box.h}+{box.x}+{box.y}",
                "+repage", "-quality", "95", str(dst)])

    def convert(self, src: Path, dst: Path) -> None:
        _check(["magick", f"{src}[0]", "-quality", "95", str(dst)])

    def render_svg(self, src: Path, dst: Path) -> None:
        _check(["rsvg-convert", "-w", "3840", "-h", "2160", "--keep-aspect-ratio",
                "-o", str(dst), str(src)])

    def thumbnail(self, src: Path, dst: Path, width: int, quality: int = 82) -> None:
        _check(["magick", f"{src}[0]", "-thumbnail", f"{width}x", "-strip", "-quality", str(quality), f"JPEG:{dst}"])
