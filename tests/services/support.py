"""Test wiring shared by the importer, remover, library and cropper tests: fakes over a real temp root."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.fakes import FakeCatalogue, FakeFetcher, FakeImageTool, FakeStore, FakeVcs
from wallman.domain.models import Collection, Resolution, Wallpaper
from wallman.services.unit_of_work import UnitOfWork
from wallman.services.uploads import Uploads

R = Resolution(2560, 1440)


def wp(file, title="T", artist="A", license="public-domain", resolution=R, **kw):
    return Wallpaper(file=file, title=title, artist=artist, license=license, resolution=resolution, **kw)


def col(folder="painting/x", wallpapers=(), medium="painting"):
    return Collection(folder=folder, medium=medium, movement=folder.split("/")[-1], wallpapers=list(wallpapers))


class ServiceCase(unittest.TestCase):
    def build(self, collections=(), files=None, external=(), tracked=(), responses=None, default=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cat = FakeCatalogue(list(collections))
        self.store = FakeStore(files or {}, set(external), root=self.root)
        self.tool = FakeImageTool(default=default)
        self.vcs = FakeVcs({self.store.path(f, n) for f, n in tracked})
        self.fetcher = FakeFetcher(responses)
        self.commits = 0
        self.uploads = Uploads(self.root / ".cache", self.tool, self.fetcher)
        for folder, names in (files or {}).items():
            for n in names:
                self.disk(folder, n, b"orig:" + n.encode())
        for c in self.cat.collections():
            self.cat.raw[c.folder] = self.cat.render(c)
            self.toml(c.folder).parent.mkdir(parents=True, exist_ok=True)
            self.toml(c.folder).write_bytes(self.cat.raw[c.folder])
        self.cat.root = self.root

    def uow(self):
        return UnitOfWork(self.cat, self.store, self.vcs, self.on_commit)

    def on_commit(self, uow):
        self.commits += 1

    def publish_all(self, folder):
        """The legacy state: the whole wallpapers.toml in the git index (every entry published)."""
        self.vcs.index[self.toml(folder)] = self.cat.render(self.cat.load(folder))

    def toml(self, folder):
        return self.store.path(folder, "wallpapers.toml")

    def disk(self, folder, name, data):
        p = self.store.path(folder, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file() and ".cache" not in p.parts}
