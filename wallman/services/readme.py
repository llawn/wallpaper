"""Regenerate README.md from every wallpapers.toml (short and stable: nothing here changes when one wallpaper is added)."""
from __future__ import annotations

from pathlib import Path

from ..domain.licenses import LABELS as LICENSES, SHAREABLE
from ..domain.rules import MEDIA_DIR
from ..ports import Catalogue

MEDIA = {
    "painting": "Painting",
    "drawing": "Drawing",
    "illustration": "Illustration",
    "printmaking": "Printmaking (woodblock, etching, engraving)",
    "photography": "Photography",
    "animation": "Animation",
    "poster": "Film posters",
    "album-cover": "Album covers",
    "linux": "Linux (official distribution and desktop wallpapers)",
    "ml4w": "ML4W collection",
    "personal": "Personal",
}


INTRO = (
    "Desktop wallpapers by human painters, illustrators, printmakers, photographers and animation studios: "
    "no AI-generated images. Besides the art collections it holds the official wallpapers of Linux distributions and desktops. "
    "Every image is 16:9-friendly (ratio 1.6 to 1.95) and at least 1800×900."
)

RULES = """\
## Rules

- Human-made art only, no AI-generated images.
- Every wallpaper is at least 1800×900 with a ratio between 1.6 and 1.95 (not enforced in external collections or in `personal`).
- File names: lowercase kebab-case parts joined by `_`, as `<artist>_<title>[_<year>].jpg` (or `.png`).
- Only images whose license allows redistribution are committed; the others are downloaded by `just fetch`.
- `just validate` checks every rule, and the pre-commit hook runs it."""

LAYOUT = """\
## Layout

```
wallpapers/
├── images/                           only images and their wallpapers.toml
│   ├── <medium>/<movement>/          e.g. painting/impressionism/
│   │   ├── <artist>_<title>[_<year>].jpg
│   │   └── wallpapers.toml           typed metadata for every image in the folder
├── _originals/                       untouched originals of tall works fitted to 16:9 (outside images/)
├── wallman/                          the wallman package: schema, services, CLI, web UI
├── scripts/                          git hooks (pre-commit refuses images that may not be redistributed)
├── tests/
├── docs/
└── justfile
```

- Point your wallpaper app (Noctalia, Waypaper, …) at `images/`: it holds only images and their wallpapers.toml."""

WORKFLOW = """\
## Workflow

```sh
git clone --recurse-submodules <repo> && cd <repo>
just setup                         # hooksPath -> scripts/hooks, submodule init, fetch missing files
just serve                         # open the UI (prints the URL with its token)
just add photo.jpg photography/landscape --artist "..." --title "..." --license CC-BY-4.0 --source URL [--crop x,y,w,h]
just add-url https://... photography/landscape ...   # same, downloaded (download + sha256 recorded)
just rm painting/marine/foo.jpg    # delete the image, its entry and its git index entry
just mv painting/marine/foo.jpg painting/nocturne
just crop painting/marine/foo.jpg 0,120,3840,2160   # x,y,w,h; from the original download when there is one
just sync | validate | guard | gitignore | readme
just theme-init                    # write a commented ~/.config/wallman/config.toml (never overwrites)
just bench [--n 10000]             # UI performance check on a synthetic collection
just fetch [folders]               # download the files that are not in git
just test                          # python3 -m unittest discover -s tests -t .
git status && git commit           # committing stays manual
```

- `just` alone lists the recipes; most recipes call `python3 -m wallman <command>`.
- `just serve` prints a URL with a token. Open it once: a cookie keeps you signed in. The UI listens on `127.0.0.1` only.
- Tags (`tags = ["night", "snow"]`, unique lowercase-kebab) and uncertain dates (`year = 1889` or a range, `year = [1880, 1889]`) are edited in the UI details panel and live in `wallpapers.toml`.
- The UI theme is read from `~/.config/wallman/config.toml`: see [UI theme](#ui-theme) below.
- The UI serves a plain-language page about the licenses at `/docs/licenses`.
- Nothing commits or pushes: the UI and the add/rm/mv/sync commands only stage. Review with `git status`, then commit yourself.
- Sorting is done by deleting images: in the UI, with `just rm`, or by deleting the files and running `just sync --stage`, which drops their entries and stages their deletion (the pre-commit hook does the same).
- `just fetch` downloads each missing file from its `download` URL and refuses it if the checksum differs. The `crop = [x, y, w, h]` of an entry is re-applied after the download whenever `crop` is set.
- External collections such as `images/ml4w/` are git submodules with their own history and `wallpapers.toml`. Commit inside the submodule first, then commit the new pointer here.
- On every commit the hook runs `just guard` (refuses staged images whose license is not public domain, CC, GPL, LGPL, MIT, BSD or Apache, or that are listed nowhere), `just validate` and `just sync --stage`; `.gitignore` and this README are refreshed and staged with the commit."""

THEME = """\
## UI theme

`~/.config/wallman/config.toml` is optional: without it the UI follows your live Noctalia palette (built-in,
community, custom or wallpaper colours), falling back to the built-in Noctalia palette with a warning in the status
bar when Noctalia's settings cannot be read. `just theme-init` writes a commented copy; saved edits reload the open
page by themselves.

```toml
[theme]
# "noctalia" follows your Noctalia shell; or a built-in palette: Ayu, Catppuccin, Dracula, Eldritch, Gruvbox,
# Kanagawa, Noctalia, Nord, Rosé Pine, Tokyo-Night; or "custom" with custom_palette below
palette = "Catppuccin"

# dark | light | auto (auto follows the browser's prefers-color-scheme)
mode = "dark"

# palette = "custom": a name in ~/.config/wallman/palettes/ (without .json) or a path to a JSON file holding
# {"dark": {"mPrimary": "#rrggbb", ...}, "light": {...}} with Noctalia's 16 m... colour roles
# custom_palette = "MyPalette"
```"""

METADATA = (
    "Per-image title, artist, year, exact license, source and download URL are in each folder's `wallpapers.toml`. "
    "`wallman/schema/wallpapers.schema.json` describes the format (editors with "
    "[taplo / Even Better TOML](https://taplo.tamasfe.dev/) check it as you type) and `just validate` enforces it."
)


SOURCES = """\
## Sources

- [Wikimedia Commons](https://commons.wikimedia.org): paintings, drawings, prints and photos.
- [Studio Ghibli free stills](https://www.ghibli.jp/info/013344/).
- [Disney Parks Blog](https://disneyparksblog.com/wallpapers/): official wallpapers.
- [Diaphana Distribution](https://diaphana.fr/): press stills.
- [wallhaven](https://wallhaven.cc/): studio stills and key art, AI-tagged art excluded, every image checked by hand.
- Official artwork repositories of the Linux distributions and desktops.
- The ML4W collection, a fork kept as a git submodule."""


class ReadmeService:
    def __init__(self, root: Path, catalogue: Catalogue) -> None:
        self.root = Path(root)
        self.catalogue = catalogue

    def render(self) -> str:
        # key: folder cut to two levels (medium/sub); deeper collections collapse into their parent directory
        by_medium: dict[str, dict[str, dict]] = {}
        for c in self.catalogue.collections():
            parts = c.folder.split("/")
            key = "/".join(parts[:2])
            e = by_medium.setdefault(c.medium, {}).setdefault(key, {"own": False, "deeper": False, "external": False})
            if len(parts) > 2:
                e["deeper"] = True
            else:
                e["own"] = True
                e["external"] = c.external

        out = ["# Wallpapers", "", INTRO, "", RULES, "", LAYOUT, "", "## Folders", ""]
        for med, label in MEDIA.items():
            entries = by_medium.get(med)
            if not entries:
                continue
            out.append(f"- {label}")
            for key in sorted(entries):
                e = entries[key]
                if e["own"]:
                    line = f"  - [`{key}/`]({MEDIA_DIR}/{key}/wallpapers.toml)"
                else:
                    line = f"  - [`{key}/`]({MEDIA_DIR}/{key}/)"
                if e["deeper"]:
                    line += " — one folder per subfolder"
                if e["external"]:
                    line += " (git submodule)"
                out.append(line)
        out += ["", METADATA, "", SOURCES, "", "## Licenses", "", "| License | Meaning | In git |", "|---|---|---|"]
        for k, meaning in LICENSES.items():
            out.append(f"| `{k}` | {meaning} | {'committed' if k in SHAREABLE else 'download-only'} |")
        out += ["", WORKFLOW, "", THEME]
        return "\n".join(out) + "\n"

    def write(self) -> int:
        """Write README.md; returns the number of folders listed."""
        text = self.render()
        (self.root / "README.md").write_text(text, encoding="utf-8")
        return sum(1 for line in text.splitlines() if line.startswith("  - ["))
