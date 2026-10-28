"""ThumbPool (spec 3.3): single-flight, priorities, failure handling; thumbnail route caching."""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.web.test_api import REF, ApiBase
from wallman.adapters.magick import Magick
from wallman.errors import InvalidImage
from wallman.web.thumbs import ThumbPool


class CountingTool:
    def __init__(self, delay=0.0, fail=False, gate=None):
        self.delay, self.fail, self.gate = delay, fail, gate
        self.calls: list[Path] = []
        self.lock = threading.Lock()

    def thumbnail(self, src, dst, width, quality=82):
        if self.gate:
            self.gate.wait(5)
        with self.lock:
            self.calls.append(src)
        time.sleep(self.delay)
        if self.fail:
            dst.write_bytes(b"partial")
            raise InvalidImage("boom")
        dst.write_bytes(b"jpeg")


class PoolBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cache = self.dir / "cache"
        self.images = []
        for n in "abc":
            p = self.dir / f"{n}.jpg"
            p.write_bytes(n.encode() * 10)
            self.images.append(p)

    def pool(self, tool, **kw):
        pool = ThumbPool(self.cache, tool, **kw)
        self.addCleanup(pool.close)
        return pool


class ThumbPoolTest(PoolBase):
    def test_single_flight_one_magick_call(self):
        tool = CountingTool(delay=0.1)
        pool = self.pool(tool, workers=4)
        with ThreadPoolExecutor(10) as ex:
            outs = list(ex.map(lambda _: pool.get(self.images[0]), range(10)))
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(len(set(outs)), 1)
        self.assertEqual(outs[0], self.cache / f"{pool.key(self.images[0])}.jpg")
        pool.get(self.images[0])
        self.assertEqual(len(tool.calls), 1)  # cached

    def test_priority_ordering(self):
        gate = threading.Event()
        tool = CountingTool(gate=gate)
        pool = self.pool(tool, workers=1)
        pool.prefetch([self.images[0]])  # occupies the single worker (blocked on the gate)
        deadline = time.time() + 5
        while not pool._jobs[pool.key(self.images[0])].started and time.time() < deadline:
            time.sleep(0.01)
        pool.prefetch([self.images[1]])
        result = []
        t = threading.Thread(target=lambda: result.append(pool.get(self.images[2], priority=0)))
        t.start()
        time.sleep(0.1)
        gate.set()
        t.join(5)
        pool.get(self.images[1])
        self.assertEqual(tool.calls, [self.images[0], self.images[2], self.images[1]])

    def test_get_promotes_queued_prefetch(self):
        gate = threading.Event()
        tool = CountingTool(gate=gate)
        pool = self.pool(tool, workers=1)
        pool.prefetch(self.images)
        time.sleep(0.1)
        t = threading.Thread(target=lambda: pool.get(self.images[2]))
        t.start()
        time.sleep(0.1)
        gate.set()
        t.join(5)
        pool.get(self.images[1])
        self.assertEqual(tool.calls[1], self.images[2])
        self.assertEqual(len(tool.calls), 3)

    def test_failed_generation_not_cached(self):
        tool = CountingTool(fail=True)
        pool = self.pool(tool, workers=2)
        with self.assertRaises(InvalidImage):
            pool.get(self.images[0])
        self.assertEqual(list(self.cache.glob("*")), [])
        tool.fail = False
        out = pool.get(self.images[0])  # retried, not remembered as failed
        self.assertEqual(out.read_bytes(), b"jpeg")
        self.assertEqual(len(tool.calls), 2)

    def test_prefetch_skips_failed_keys(self):
        tool = CountingTool(fail=True)
        pool = self.pool(tool, workers=1)
        pool.prefetch([self.images[0]])
        deadline = time.time() + 5
        while not pool._failed and time.time() < deadline:
            time.sleep(0.01)
        pool.prefetch([self.images[0]])
        time.sleep(0.1)
        self.assertEqual(len(tool.calls), 1)
        os.utime(self.images[0], ns=(1, 1))  # a fixed file has a new key and is tried again
        pool.prefetch([self.images[0]])
        deadline = time.time() + 5
        while len(tool.calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(tool.calls), 2)

    def test_close_wakes_pending_get_and_is_quick(self):
        gate = threading.Event()
        tool = CountingTool(gate=gate)
        pool = ThumbPool(self.cache, tool, workers=1)
        self.addCleanup(gate.set)
        pool.prefetch([self.images[0]])
        time.sleep(0.1)  # the worker is now blocked inside the tool
        extra = []
        for i in range(500):
            p = self.dir / f"x{i}.jpg"
            p.write_bytes(b"x")
            extra.append(p)
        pool.prefetch(extra)
        errors = []

        def waiter():
            try:
                pool.get(extra[-1])
            except InvalidImage as e:
                errors.append(str(e))
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        gate.set()
        started = time.time()
        pool.close()
        t.join(5)
        self.assertLess(time.time() - started, 2)
        self.assertFalse(t.is_alive())
        self.assertIn("closed", errors[0])
        self.assertLess(len(tool.calls), 50)

    def test_get_times_out(self):
        gate = threading.Event()
        pool = self.pool(CountingTool(gate=gate), workers=1)
        self.addCleanup(gate.set)
        pool.timeout = 0.2
        with self.assertRaises(InvalidImage):
            pool.get(self.images[0])

    def test_sweeps_leftover_temp_files(self):
        self.cache.mkdir()
        (self.cache / "old.1.part").write_bytes(b"x")
        (self.cache / "old.part.jpg").write_bytes(b"x")
        keep = self.cache / "good.jpg"
        keep.write_bytes(b"x")
        self.pool(CountingTool())
        self.assertEqual([f.name for f in self.cache.iterdir()], ["good.jpg"])

    def test_key_changes_when_file_changes(self):
        pool = self.pool(CountingTool())
        a = pool.key(self.images[0])
        os.utime(self.images[0], ns=(1, 1))
        self.assertNotEqual(pool.key(self.images[0]), a)

    def test_close_stops_workers(self):
        pool = self.pool(CountingTool())
        pool.close()
        self.assertFalse(any(t.is_alive() for t in pool._threads))


class ThumbPoolRealMagickTest(PoolBase):
    def test_cached_file_is_jpeg(self):
        if not shutil.which("magick"):
            self.skipTest("ImageMagick not installed")
        src = self.dir / "src.png"
        subprocess.run(["magick", "-size", "800x450", "xc:blue", str(src)], check=True)
        pool = self.pool(Magick(), workers=2)
        out = pool.get(src)
        self.assertEqual(out.suffix, ".jpg")
        self.assertEqual(out.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertEqual(Magick().identify(out).width, 360)
        self.assertEqual([f.name for f in self.cache.iterdir()], [out.name])


class ThumbRouteTest(ApiBase):
    def test_304_on_matching_etag(self):
        _, headers, _ = self.call("GET", f"/api/thumbs/{REF}")
        etag = headers["ETag"]
        key = etag.strip('"')
        status, h, data = self.call("GET", f"/api/thumbs/{REF}?v={key}", headers={"If-None-Match": etag})
        self.assertEqual((status, data), (304, b""))
        self.assertEqual(h["ETag"], etag)
        status, h, data = self.call("GET", f"/api/thumbs/{REF}?v={key}")
        self.assertEqual(status, 200)
        self.assertEqual(h["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(h["ETag"], etag)
        self.assertTrue(data)

    def test_stale_version_not_immutable(self):
        status, h, _ = self.call("GET", f"/api/thumbs/{REF}?v=stale")
        self.assertEqual((status, h["Cache-Control"]), (200, "no-cache"))

    def test_key_changes_after_crop(self):
        _, page = self.api("GET", "/api/wallpapers")
        before = page["items"][0]["thumb"]
        self.call("GET", f"/api/thumbs/{REF}")
        status, body = self.api("POST", f"/api/wallpapers/{REF}/crop", {"x": 0, "y": 0, "w": 3200, "h": 1800})
        self.assertEqual(status, 200, body)
        _, page = self.api("GET", "/api/wallpapers")
        after = page["items"][0]["thumb"]
        self.assertNotEqual(before, after)
        _, h, _ = self.call("GET", f"/api/thumbs/{REF}?v={before}")
        self.assertEqual(h["ETag"], f'"{after}"')
        self.assertEqual(h["Cache-Control"], "no-cache")

    def test_list_prefetches_page(self):
        _, page = self.api("GET", "/api/wallpapers")
        out = self.app.thumbs_dir / f"{page['items'][0]['thumb']}.jpg"
        deadline = time.time() + 5
        while not out.is_file() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(out.is_file())
