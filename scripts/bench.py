"""Benchmark the local web UI on a synthetic collection (spec v2 section 5): `just bench [--n 10000]`.

Builds a throwaway git repository under the system temp directory: one folder of N entries (solid 1920x1080 JPEGs
copied from a few base images made once by magick and cached in $XDG_CACHE_HOME/wallman-bench) plus a small
folder, with every wallpapers.toml written through TomlCatalogue, a generated .gitignore and one commit. Starts the
real server in-process and measures, over HTTP on 127.0.0.1 (so each number includes JSON encoding and the
loopback round trip, an upper bound of the server time):

- index warm-up (cold, first build in this process);
- list pages (folder at offsets 0 and N-200, search, tag, year filters);
- first paint (first page + 40 cached thumbnails), thumbnails warm (304 and cached 200) and cold (real magick);
- index refresh after an external edit of the big TOML, PATCH of a title (renames the file) and the list page right
  after it, default /api/status, POST /api/git/add.

Prints a table; exits 1 when a gated row misses its target (unless --no-gate). The user's repository is never
touched. Except for the cold-thumbnail row the thumbnail pool copies one real thumbnail instead of running magick,
so the prefetch backlog of list pages does not compete for the CPU (the prefetch bookkeeping still runs in the
request); background work is allowed to finish between rows and between edits (a person edits one wallpaper at
a time: back-to-back edits are not measured). PATCH is gated on the median of 20 edits after one excluded warm-up
edit; p95 and max are reported. The warm-up row is cold for the process only: the fixture was just written, so its
files are in the page cache (a real first start after boot also reads them from disk).

    python3 scripts/bench.py [--n N] [--no-gate] [--keep] [--profile warm|list|patch|status|refresh]
"""
from __future__ import annotations

import argparse
import cProfile
import gc
import http.client
import json
import os
import pstats
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wallman import bootstrap  # noqa: E402
from wallman.adapters.fs_store import FsStore  # noqa: E402
from wallman.adapters.magick import Magick  # noqa: E402
from wallman.adapters.toml_catalogue import TomlCatalogue  # noqa: E402
from wallman.domain.models import Collection, Ref, Resolution, Wallpaper  # noqa: E402
from wallman.domain.years import YearSpan  # noqa: E402
from wallman.services.gitignore import GitignoreService  # noqa: E402
from wallman.services.library_index import Query  # noqa: E402
from wallman.services.readme import ReadmeService  # noqa: E402
from wallman.web.server import make_server  # noqa: E402

BIG = "illustration/big"
SMALL = "painting/impressionism"
COLOURS = ["#336699", "#993366", "#669933", "#cc8844", "#4488cc", "#222222", "#dddddd", "#88cc44"]
PATCH_EDITS = 20  # gated: median over these, after one excluded warm-up edit
UNTRACKED_EVERY = 500  # every 500th shareable image of the big folder is left out of git (for the git add row)

# row -> (target ms or None when only reported, what the number is)
TARGETS: dict[str, float | None] = {
    "warm-up (process-cold)": 3000,
    "list folder offset 0": 50,
    "list folder offset end": 50,
    "list search q": 50,
    "list tag": 50,
    "list year": 50,
    "first paint (page + 40 thumbs)": 200,
    "thumb warm 304": 20,
    "thumb warm 200 (cached file)": 20,
    "thumb cold (magick)": None,
    "index refresh (external edit)": None,
    "PATCH title (warm-up edit)": None,
    "PATCH title": 300,
    "PATCH title p95": None,
    "list right after PATCH": None,
    "status (default)": 300,
    "git add (1 file)": None,
}


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "wallman-bench"


def base_images() -> list[Path]:
    """One solid 1920x1080 JPEG per colour, made once by magick and cached."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i, colour in enumerate(COLOURS):
        p = d / f"base-{i}.jpg"
        if not p.exists():
            tmp = d / f".base-{i}.part.jpg"
            subprocess.run(["magick", "-size", "1920x1080", f"xc:{colour}", "-quality", "50", str(tmp)], check=True)
            os.replace(tmp, p)
        out.append(p)
    return out


def _place(src: Path, dst: Path) -> None:
    # a copy, never a hard link: renaming one link changes the ctime of every name sharing the inode, and git then
    # re-reads all of them on each status, which a real collection never does
    shutil.copyfile(src, dst)


def big_entry(i: int) -> Wallpaper:
    year: YearSpan = YearSpan(1850 + i % 170, 1850 + i % 170 + (9 if i % 10 == 0 else 0))
    tags = ["synthetic"] + (["blue"] if i % 3 == 0 else []) + (["night"] if i % 7 == 0 else [])
    restricted = i % 5 == 0
    return Wallpaper(
        file=f"artist-{i % 37:02d}_work-{i:05d}_{year.start}.jpg", title=f"Work {i:05d}", artist=f"Artist {i % 37:02d}",
        license="all-rights-reserved" if restricted else "CC0-1.0",
        license_detail="© Bench Studio" if restricted else None,
        download=f"https://example.org/bench/{i}.jpg" if restricted else None,
        sha256="0" * 64 if restricted else None,
        resolution=Resolution(1920, 1080), year=year, tags=tuple(tags))


def build_repo(root: Path, n: int) -> list[Ref]:
    """The synthetic repository; returns the untracked shareable refs of the big folder."""
    bases = base_images()
    images = root / "images"
    images.mkdir(parents=True)
    catalogue = TomlCatalogue(images, FsStore(images))

    def folder(name: str, medium: str, movement: str, entries: list[Wallpaper]) -> None:
        d = images / name
        d.mkdir(parents=True, exist_ok=True)
        for k, w in enumerate(entries):
            _place(bases[k % len(bases)], d / w.file)
        catalogue.save(Collection(folder=name, medium=medium, movement=movement, wallpapers=entries))

    big = [big_entry(i) for i in range(n)]
    folder(BIG, "illustration", "bench", big)
    folder(SMALL, "painting", "impressionism", [dataclass_replace(big_entry(i), file=small_file(i),
                                                                  title=f"Pond {i:02d}", artist="Claude Monet")
                                                for i in range(1, 41)])
    GitignoreService(root, catalogue).write()
    ReadmeService(root, catalogue).write()
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    shareable = [w for k, w in enumerate(big) if w.license == "CC0-1.0"]
    untracked = [Ref(BIG, w.file) for k, w in enumerate(shareable) if k % UNTRACKED_EVERY == 1]
    if untracked:
        subprocess.run([*git, "rm", "-q", "--cached", "--", *(f"images/{r.folder}/{r.file}" for r in untracked)],
                       check=True)
    subprocess.run([*git, "-c", "user.name=bench", "-c", "user.email=bench@example.org", "-c", "commit.gpgsign=false",
                    "commit", "-q", "-m", "bench"], check=True)
    os.sync()  # a disk-backed temp dir would otherwise write the fixture back during the first timings
    return untracked


def dataclass_replace(w: Wallpaper, **kw) -> Wallpaper:
    import dataclasses
    return dataclasses.replace(w, **kw)


class BenchTool(Magick):
    """Magick, except that thumbnails can be faked (a copy of one real thumbnail) while list pages are measured."""

    def __init__(self) -> None:
        self.fast_thumbs = False
        self._sample: Path | None = None

    def thumbnail(self, src: Path, dst: Path, width: int, quality: int = 82) -> None:
        if self.fast_thumbs and self._sample is not None:
            shutil.copyfile(self._sample, dst)
            return
        super().thumbnail(src, dst, width, quality)
        if self._sample is None:
            self._sample = Path(str(dst) + ".sample")
            shutil.copyfile(dst, self._sample)


class NoFetcher:
    def get(self, url: str):
        raise RuntimeError("the benchmark never downloads")


class Client:
    def __init__(self, port: int, token: str) -> None:
        self.port, self.token = port, token
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
        h = {"X-Wallman-Token": self.token, "Origin": f"http://127.0.0.1:{self.port}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        self.conn.request(method, path, body=data, headers=h)
        r = self.conn.getresponse()
        payload = r.read()
        return r.status, dict(r.getheaders()), payload

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        status, _, payload = self.call(method, path, body)
        if status >= 400:
            raise RuntimeError(f"{method} {path} -> {status}: {payload[:300]!r}")
        return json.loads(payload)


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def sample(fn, runs: int) -> tuple[float, float]:
    """(median, max) in ms over `runs` calls, after one discarded call."""
    fn()
    times = [timed(fn) for _ in range(runs)]
    return statistics.median(times), max(times)


def pick_up_external(app) -> None:
    """What the next request does once the freshness window has passed: stat the folders, reload the changed one."""
    app.index._stat_at = float("-inf")
    app.index.ensure_fresh()


def small_file(i: int) -> str:
    return f"monet_pond-{i:02d}_1900.jpg"


def settle(server, app, timeout: float = 60) -> None:
    """Let background work finish (thumbnail queue, git statuses) so it does not land in the next timing."""
    end = time.monotonic() + timeout
    while server.thumbs._jobs and time.monotonic() < end:
        time.sleep(0.05)
    app.index.wait_for_git(timeout)


def thumb_url(item: dict) -> str:
    ref = urllib.parse.quote(f"{item['folder']}/{item['file']}")
    return f"/api/thumbs/{ref}?v={item['thumb']}"


def profile(app, what: str, refs: list[Ref], top: int = 30) -> None:
    """Profile one core call in this thread (the HTTP handler runs in a server thread cProfile cannot see)."""
    calls = {
        "warm": lambda: app.index.warm(),
        "list": lambda: [app.index.query(Query(folder=BIG, offset=max(0, len(refs) - 200), limit=200)) for _ in range(20)],
        "status": lambda: __import__("wallman.web.routes", fromlist=["status"]).status(app, None, _FakeReq()),
        "refresh": lambda: app.index.refresh({BIG}, [app.images_root / BIG / refs[0].file]),
        "patch": lambda: app.library.update(refs[len(refs) // 2], {"title": "Profiled Title"}),
    }
    app.index.warm()
    pr = cProfile.Profile()
    pr.enable()
    calls[what]()
    pr.disable()
    pstats.Stats(pr).sort_stats(os.environ.get("BENCH_SORT", "cumulative")).print_stats(top)


class _FakeReq:
    query: dict = {}
    headers: dict = {}


def run(n: int, gate: bool, keep: bool, prof: str | None) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wallman-bench-"))
    rows: list[tuple[str, float, float | None, str]] = []  # name, value ms, max ms, note
    try:
        t = time.perf_counter()
        untracked = build_repo(tmp / "repo", n)
        print(f"fixture: {n} + 40 entries in {time.perf_counter() - t:.1f} s at {tmp}", file=sys.stderr)
        app = bootstrap.build(tmp / "repo", tmp / "cache", NoFetcher(), config_home=tmp / "config")
        tool = BenchTool()
        app = dataclass_replace_app(app, tool)
        if prof:
            refs = [Ref(BIG, big_entry(i).file) for i in range(n)]
            profile(app, prof, refs)
            return 0

        rows.append(("warm-up (process-cold)", timed(app.index.warm), None,
                     f"{n + 40} entries; files in the page cache"))
        gc.collect()
        gc.freeze()  # what web.server.warm_up does at `just serve` start
        server, token = make_server(app, 0)
        server.thumbs.tool = tool
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            c = Client(server.server_address[1], token)
            end = max(0, n - 200)
            tool.fast_thumbs = True  # list pages prefetch thumbnails: copies, so no magick backlog skews the timings
            for name, qs in [("list folder offset 0", f"folder={BIG}&offset=0"),
                             ("list folder offset end", f"folder={BIG}&offset={end}"),
                             ("list search q", "q=artist%2007"),
                             ("list tag", "tag=blue&tag=synthetic"),
                             ("list year", "year=1880s&sort=year")]:
                med, mx = sample(lambda: c.json("GET", f"/api/wallpapers?{qs}&limit=200"), 15)
                rows.append((name, med, mx, qs))
            page = c.json("GET", f"/api/wallpapers?folder={BIG}&limit=200")
            for it in page["items"][:40]:  # cache them (fast copies) so the first paint is the warm case
                c.call("GET", thumb_url(it))
            settle(server, app)

            def first_paint():
                p = c.json("GET", f"/api/wallpapers?folder={BIG}&limit=200")
                for it in p["items"][:40]:
                    status, _, _ = c.call("GET", thumb_url(it))
                    assert status == 200, status
            med, mx = sample(first_paint, 10)
            rows.append(("first paint (page + 40 thumbs)", med, mx, "sequential on one connection"))
            first = page["items"][0]
            med, mx = sample(lambda: c.call("GET", thumb_url(first), headers={"If-None-Match": f'"{first["thumb"]}"'}), 30)
            rows.append(("thumb warm 304", med, mx, ""))
            med, mx = sample(lambda: c.call("GET", thumb_url(first)), 30)
            rows.append(("thumb warm 200 (cached file)", med, mx, ""))
            settle(server, app)
            tool.fast_thumbs = False  # real magick; asked for directly (no list request, so no prefetch)
            cold = [timed(lambda i=i: c.call("GET", "/api/thumbs/" + urllib.parse.quote(f"{SMALL}/{small_file(i)}")))
                    for i in range(1, 7)]
            tool.fast_thumbs = True
            rows.append(("thumb cold (magick)", statistics.median(cold), max(cold), f"{len(cold)} images, one at a time"))

            settle(server, app)
            ext_times = []
            outside = TomlCatalogue(app.images_root, FsStore(app.images_root))  # another process, as it were
            for k in range(3):
                coll = outside.load(BIG)
                coll.wallpapers[5000 % n] = dataclass_replace(coll.wallpapers[5000 % n], title=f"Edited outside {k}")
                outside.save(coll)
                ext_times.append(timed(lambda: pick_up_external(app)))
                settle(server, app)
            rows.append(("index refresh (external edit)", statistics.median(ext_times), max(ext_times),
                         "TOML parse + folder reload"))

            patch_times, after = [], []
            items = page["items"]
            for k in range(PATCH_EDITS + 1):  # edit 0 warms up (first save after a parse renders every entry)
                it = items[(10 + k) % len(items)]
                ref = urllib.parse.quote(f"{it['folder']}/{it['file']}")
                t = timed(lambda: c.json("PATCH", f"/api/wallpapers/{ref}", {"title": f"Renamed {k}"}))
                a = timed(lambda: c.json("GET", f"/api/wallpapers?folder={BIG}&limit=200"))
                if k:
                    patch_times.append(t)
                    after.append(a)
                else:
                    rows.append(("PATCH title (warm-up edit)", t, None, "excluded from the gate"))
                settle(server, app)
            p95 = statistics.quantiles(patch_times, n=20, method="inclusive")[18]
            rows.append(("PATCH title", statistics.median(patch_times), max(patch_times),
                         f"{len(patch_times)} edits, each renames the file (git mv); p95 {p95:.0f}"))
            rows.append(("PATCH title p95", p95, None, ""))
            rows.append(("list right after PATCH", statistics.median(after), max(after), "re-sorts the changed index"))

            med, mx = sample(lambda: c.json("GET", "/api/status"), 5)
            rows.append(("status (default)", med, mx, ""))

            if untracked:
                adds = [timed(lambda r=r: c.json("POST", "/api/git/add", {"refs": [{"folder": r.folder, "file": r.file}]}))
                        for r in untracked[:5]]
                rows.append(("git add (1 file)", statistics.median(adds), max(adds), f"{len(adds)} files"))
            c.conn.close()
        finally:
            server.shutdown()
            server.server_close()
            app.index.wait_for_git(5)
    finally:
        if keep:
            print(f"kept {tmp}", file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    missed = report(rows)
    if missed and gate:
        print(f"\nmissed: {', '.join(missed)}")
        return 1
    return 0


def dataclass_replace_app(app, tool):
    import dataclasses
    return dataclasses.replace(app, tool=tool)


def report(rows) -> list[str]:
    missed = []
    print(f"{'row':34} {'median ms':>10} {'max ms':>8} {'target':>8}  ok  note")
    for name, value, mx, note in rows:
        target = TARGETS.get(name)
        ok = target is None or value < target
        if not ok:
            missed.append(name)
        print(f"{name:34} {value:10.1f} {'' if mx is None else f'{mx:8.1f}':>8} "
              f"{'' if target is None else f'< {target:g}':>8}  {'--' if target is None else 'ok' if ok else 'NO'}  {note}")
    return missed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=10_000, help="entries in the big folder (default 10000)")
    ap.add_argument("--no-gate", action="store_true", help="always exit 0 (tests, small N)")
    ap.add_argument("--keep", action="store_true", help="keep the temp repository")
    ap.add_argument("--profile", choices=["warm", "list", "patch", "status", "refresh"],
                    help="print a cProfile of one core call instead of the table (BENCH_SORT=tottime to sort by own time)")
    a = ap.parse_args(argv)
    if a.n < 30:
        ap.error("--n must be at least 30")
    return run(a.n, not a.no_gate, a.keep, a.profile)


if __name__ == "__main__":
    sys.exit(main())
