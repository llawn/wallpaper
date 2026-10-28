"""Cache key of an image's thumbnail: changes whenever the file's path, mtime or size does."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def thumb_key_for(path: Path, mtime_ns: int, size: int) -> str:
    return hashlib.sha1(f"{path}:{mtime_ns}:{size}".encode()).hexdigest()[:16]


def thumb_key(path: Path, stat_result: os.stat_result) -> str:
    return thumb_key_for(path, stat_result.st_mtime_ns, stat_result.st_size)
