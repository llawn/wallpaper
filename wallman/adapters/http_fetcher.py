"""HTTP adapter: bounded, retrying download with checksum."""
from __future__ import annotations

import hashlib
import http.client
import time
import urllib.error
import urllib.request
from typing import Callable

from ..domain.rules import is_web_url
from ..errors import DownloadFailed

_CHUNK = 64 * 1024


class HttpFetcher:
    def __init__(self, user_agent: str, max_bytes: int = 200 * 2**20, retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.retries = retries
        self.sleep = sleep
        self.timeout = timeout

    def get(self, url: str) -> tuple[bytes, str]:
        if not is_web_url(url):
            raise DownloadFailed(f"{url}: only http:// and https:// URLs can be downloaded")
        attempt = 0
        while True:
            try:
                return self._once(url)
            except urllib.error.HTTPError as e:
                e.close()
                if (e.code == 429 or e.code >= 500) and attempt < self.retries:
                    attempt += 1
                    self.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise DownloadFailed(f"{url}: HTTP {e.code} {e.reason}") from e
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                reason = getattr(e, "reason", e)
                raise DownloadFailed(f"{url}: {reason}") from e

    def _once(self, url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            chunks, total = [], 0
            length = resp.headers.get("Content-Length")
            expected = int(length) if length and length.isdigit() else None
            while chunk := resp.read(_CHUNK):
                total += len(chunk)
                if total > self.max_bytes:
                    raise DownloadFailed(f"{url}: larger than {self.max_bytes} bytes")
                chunks.append(chunk)
        data = b"".join(chunks)
        if expected is not None and expected != len(data):
            raise DownloadFailed(f"{url}: truncated ({len(data)} of {expected} bytes)")
        return data, hashlib.sha256(data).hexdigest()
