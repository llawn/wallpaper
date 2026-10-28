"""Composition root: wires adapters and services for one repository."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters.fs_store import FsStore
from .adapters.git_cli import GitCli
from .adapters.http_fetcher import HttpFetcher
from .adapters.magick import Magick
from .domain.rules import MEDIA_DIR
from .errors import WallmanError
from .adapters.toml_catalogue import TomlCatalogue
from .ports import Catalogue, Fetcher, ImageStore, ImageTool, Vcs
from .services.cropper import Cropper
from .services.fetcher import FetchService
from .services.gitignore import GitignoreService
from .services.importer import Importer
from .services.git_actions import GitActions
from .services.library import Library
from .services.library_index import LibraryIndex
from .services.readme import ReadmeService
from .services.remover import Remover
from .services.syncer import Syncer
from .services.theme import ThemeService, config_paths, load_builtin
from .services.unit_of_work import UnitOfWork, in_unit, record_index
from .services.uploads import Uploads
from .services.validator import Validator

USER_AGENT = "WallpaperFetcher/1.0 (personal desktop wallpapers)"


@dataclass(frozen=True)
class App:
    root: Path  # repository root (git, justfile, wallman/)
    images_root: Path  # collection root: <root>/images
    store: ImageStore
    catalogue: Catalogue
    tool: ImageTool
    vcs: Vcs
    fetcher: Fetcher
    uploads: Uploads
    importer: Importer
    remover: Remover
    library: Library
    index: LibraryIndex  # warmed on first use, so CLI commands stay fast
    git_actions: GitActions
    cropper: Cropper
    syncer: Syncer
    fetch_service: FetchService
    validator: Validator
    gitignore: GitignoreService
    readme: ReadmeService
    uow_factory: Callable[[], UnitOfWork]
    thumbs_dir: Path
    theme: ThemeService


def build(root: Path, cache_dir: Path | None = None, fetcher: Fetcher | None = None,
          config_home: Path | None = None) -> App:
    """Wire everything for `root`. `fetcher` replaces the HTTP downloader, `config_home` the user config dir (tests)."""
    root = Path(root).resolve()
    cache_dir = Path(cache_dir) if cache_dir is not None else Path.home() / ".cache" / "wallman"
    images = root / MEDIA_DIR
    if not images.is_dir():
        raise WallmanError(f"no {MEDIA_DIR}/ folder in {root}: run the migration or create it")
    store = FsStore(images)
    catalogue = TomlCatalogue(images, store)
    tool = Magick()
    vcs = GitCli(root)
    fetcher = fetcher if fetcher is not None else HttpFetcher(USER_AGENT)
    gitignore = GitignoreService(root, catalogue)
    readme = ReadmeService(root, catalogue)
    index = LibraryIndex(catalogue, store, tool, vcs)
    vcs.on_index_write = record_index  # a rolled-back unit restores the git index too (Ruling v2-17)

    def on_commit(uow: UnitOfWork) -> None:
        generated = [root / ".gitignore", root / "README.md"]
        for p in generated:
            uow.record_file(p)
        gitignore.write()
        readme.write()
        # one `git add` for the operation's tomls and the generated files: each call rewrites .git/index
        # every index write of the operation lands here, after the last step that can fail (Ruling v2-17)
        force = uow.stage_force
        staged = uow.take_staged() + ([p for p in generated if p.exists()] if uow.stage else [])
        if staged:
            vcs.stage_add(staged, force=force)
        index.refresh(uow.touched, uow.touched_paths)  # no-op until the index has been warmed

    def uow_factory() -> UnitOfWork:
        if not in_unit():  # external edits are loaded before the operation: its refresh only re-reads its own files
            index.check_external()
        return UnitOfWork(catalogue, store, vcs, on_commit)

    cfg, noctalia_dir, noctalia_state, gtk_css = config_paths(config_home)
    uploads = Uploads(cache_dir / "uploads", tool, fetcher)
    return App(
        root=root, images_root=images, store=store, catalogue=catalogue, tool=tool, vcs=vcs, fetcher=fetcher, uploads=uploads,
        importer=Importer(catalogue, store, tool, vcs, uow_factory, uploads),
        remover=Remover(catalogue, store, vcs, uow_factory),
        library=Library(catalogue, store, vcs, uow_factory, fetcher, index=index),
        index=index, git_actions=GitActions(catalogue, store, vcs, uow_factory),
        cropper=Cropper(catalogue, store, tool, vcs, uow_factory, fetcher),
        syncer=Syncer(catalogue, store, tool, vcs, uow_factory),
        fetch_service=FetchService(catalogue, store, tool, fetcher),
        validator=Validator(catalogue, store, tool),
        gitignore=gitignore, readme=readme, uow_factory=uow_factory, thumbs_dir=cache_dir / "thumbs",
        theme=ThemeService(cfg, noctalia_dir, load_builtin(), noctalia_state, gtk_css),
    )
