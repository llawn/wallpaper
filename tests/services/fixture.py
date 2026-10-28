"""A small repo tree on disk shared by the service tests (README golden, gitignore)."""
from __future__ import annotations

from pathlib import Path


def entry(file, title, artist, license, year=None, source=None, extra=""):
    lines = ["[[wallpaper]]", f'file = "{file}"', f'title = "{title}"', f'artist = "{artist}"']
    if year:
        lines.append(f"year = {year}")
    lines.append(f'license = "{license}"')
    if source:
        lines.append(f'source = "{source}"')
    lines.append("resolution = [3840, 2160]")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def write_collection(root: Path, folder: str, medium: str, movement: str, entries: list[str]) -> None:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "wallpapers.toml").write_text(
        f'[collection]\nmedium = "{medium}"\nmovement = "{movement}"\ncount = {len(entries)}\n\n' + "\n".join(entries))


def build(root: Path) -> None:
    """Write the sample collections under <root>/images."""
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    write_collection(images, "painting/impressionism", "painting", "impressionism", [
        entry("claude-monet_water-lilies_1906.jpg", "Water Lilies", "Claude Monet", "public-domain", 1906,
              "https://commons.wikimedia.org/wiki/File:Monet.jpg"),
        entry("edgar-degas_dancers-pink_1885.jpg", "Dancers | in Pink", "Edgar Degas", "CC-BY-4.0", 1885, None,
              'processing = ["crop", "fit-16x9"]\noriginal = "../../../_originals/edgar-degas_dancers-pink_1885.jpg"'),
    ])
    write_collection(images, "poster", "poster", "film", [
        entry("studio_poster_2001.jpg", "Poster", "Some Studio", "CC0-1.0", 2001),
    ])
    write_collection(images, "animation/studio-ghibli/1988-my-neighbor-totoro", "animation", "studio-ghibli", [
        entry(f"studio-ghibli_totoro-{i:02d}_1988.jpg", f"Totoro {i}", "Studio Ghibli", "ghibli-free-use", 1988,
              "https://www.ghibli.jp/info/013344/") for i in range(1, 22)
    ])
    write_collection(images, "animation/pixar", "animation", "pixar", [
        entry("pixar_up_2009.jpg", "Up", "Pixar Animation Studios", "all-rights-reserved", 2009,
              "https://disneyparksblog.com/wallpapers/",
              'license_detail = "© 2009 Pixar Animation Studios — personal desktop use"'),
        entry("pixar_cars_2006.jpg", "Cars", "Pixar Animation Studios", "official-wallpaper", 2006),
    ])
    write_collection(images, "personal/mine", "personal", "mine", [
        entry("me_holiday.jpg", "Holiday", "Me", "personal"),
    ])
    write_collection(images, "ml4w", "ml4w", "ml4w", [
        entry("Some File.jpg", "Some File", "ML4W", "MIT"),
    ])
    (images / "ml4w" / ".git").write_text("gitdir: ../.git/modules/ml4w\n")
