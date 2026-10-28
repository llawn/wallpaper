"""Image dimensions straight from file headers (JPEG SOF, PNG IHDR); no subprocess."""
from __future__ import annotations

import struct
from pathlib import Path

from ..domain.models import Resolution

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# SOF0-SOF15 except DHT (C4), JPG (C8) and DAC (CC)
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _resolution(w: int, h: int) -> Resolution | None:
    return Resolution(w, h) if w > 0 and h > 0 else None


def read_size(path: Path | str) -> Resolution | None:
    """Return the size, or None if the header is not a parseable JPEG/PNG.

    OSError from opening the file propagates; content problems never raise.
    """
    with open(path, "rb") as f:
        head = f.read(24)
        if head[:8] == _PNG_SIG:
            if len(head) < 24 or head[12:16] != b"IHDR":
                return None
            return _resolution(*struct.unpack(">II", head[16:24]))
        if head[:2] != b"\xff\xd8":
            return None
        f.seek(2)
        while True:
            marker = f.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                return None
            m = marker[1]
            if m == 0xFF:  # fill byte before a marker
                f.seek(-1, 1)
                continue
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                continue
            if m in (0xD9, 0xDA):  # EOI / start of scan: no SOF found
                return None
            raw = f.read(2)
            if len(raw) < 2:
                return None
            length = struct.unpack(">H", raw)[0]
            if length < 2:
                return None
            if m in _SOF:
                body = f.read(5)
                if len(body) < 5:
                    return None
                h, w = struct.unpack(">xHH", body)
                return _resolution(w, h)
            f.seek(length - 2, 1)
