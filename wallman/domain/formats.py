"""Image format from the first bytes of a file (pure: callers pass the bytes)."""
from __future__ import annotations

HEAD_BYTES = 512  # enough for every signature below, including an SVG's XML prolog

_HEIF_BRANDS = (b"avif", b"avis", b"heic", b"heix", b"hevc", b"mif1", b"msf1")


def sniff(head: bytes) -> str | None:
    """'jpeg', 'png', 'svg' or another raster name ('webp', 'jxl', 'avif', 'gif', 'tiff', 'bmp'); None if unknown."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"\xff\x0a" or head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return "jxl"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return "avif"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"BM":
        return "bmp"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<?xml", b"<svg", b"<!doctype svg", b"<!--")) and b"<svg" in head.lower():
        return "svg"
    return None


def stored_ext(kind: str | None, fallback: str) -> str:
    """Extension of the file kept in the collection: JPEG and PNG as they are, SVG rendered to PNG, other rasters
    converted to JPEG; unknown bytes keep the name's extension (`fallback`)."""
    if kind == "png" or kind == "svg":
        return ".png"
    if kind is None:
        return fallback
    return ".jpg"
