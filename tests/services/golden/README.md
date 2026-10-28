# Wallpapers

Desktop wallpapers by human painters, illustrators, printmakers, photographers and animation studios: no AI-generated images. Besides the art collections it holds the official wallpapers of Linux distributions and desktops. Every image is 16:9-friendly (ratio 1.6 to 1.95) and at least 1800×900.

## Rules

- Human-made art only, no AI-generated images.
- Every wallpaper is at least 1800×900 with a ratio between 1.6 and 1.95 (not enforced in external collections or in `personal`).
- File names: lowercase kebab-case parts joined by `_`, as `<artist>_<title>[_<year>].jpg` (or `.png`).
- Only images whose license allows redistribution are committed; the others are downloaded by `just fetch`.
- `just validate` checks every rule, and the pre-commit hook runs it.

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

- Point your wallpaper app (Noctalia, Waypaper, …) at `images/`: it holds only images and their wallpapers.toml.

## Folders

- Painting
  - [`painting/impressionism/`](images/painting/impressionism/wallpapers.toml)
- Animation
  - [`animation/pixar/`](images/animation/pixar/wallpapers.toml)
  - [`animation/studio-ghibli/`](images/animation/studio-ghibli/) — one folder per subfolder
- Film posters
  - [`poster/`](images/poster/wallpapers.toml)
- ML4W collection
  - [`ml4w/`](images/ml4w/wallpapers.toml) (git submodule)
- Personal
  - [`personal/mine/`](images/personal/mine/wallpapers.toml)

Per-image title, artist, year, exact license, source and download URL are in each folder's `wallpapers.toml`. `wallman/schema/wallpapers.schema.json` describes the format (editors with [taplo / Even Better TOML](https://taplo.tamasfe.dev/) check it as you type) and `just validate` enforces it.

## Sources

- [Wikimedia Commons](https://commons.wikimedia.org): paintings, drawings, prints and photos.
- [Studio Ghibli free stills](https://www.ghibli.jp/info/013344/).
- [Disney Parks Blog](https://disneyparksblog.com/wallpapers/): official wallpapers.
- [Diaphana Distribution](https://diaphana.fr/): press stills.
- [wallhaven](https://wallhaven.cc/): studio stills and key art, AI-tagged art excluded, every image checked by hand.
- Official artwork repositories of the Linux distributions and desktops.
- The ML4W collection, a fork kept as a git submodule.

## Licenses

| License | Meaning | In git |
|---|---|---|
| `public-domain` | Public domain | committed |
| `CC0-1.0` | CC0 1.0: no rights reserved | committed |
| `CC-BY-2.0` | CC BY 2.0: credit the author if you republish | committed |
| `CC-BY-2.5` | CC BY 2.5: credit the author if you republish | committed |
| `CC-BY-3.0` | CC BY 3.0: credit the author if you republish | committed |
| `CC-BY-4.0` | CC BY 4.0: credit the author if you republish | committed |
| `CC-BY-SA-2.0` | CC BY-SA 2.0: credit and share alike if you republish | committed |
| `CC-BY-SA-3.0` | CC BY-SA 3.0: credit and share alike if you republish | committed |
| `CC-BY-SA-3.0-IGO` | CC BY-SA 3.0 IGO: credit and share alike if you republish | committed |
| `CC-BY-SA-4.0` | CC BY-SA 4.0: credit and share alike if you republish | committed |
| `ghibli-free-use` | © Studio Ghibli free stills: use within common sense | download-only |
| `official-wallpaper` | © Disney / Pixar official wallpaper: free for personal use | download-only |
| `all-rights-reserved` | © studio, all rights reserved (holder in each entry): personal desktop use only | download-only |
| `GPL-2.0-only` | GPL 2.0: free to share and modify, same license | committed |
| `GPL-2.0-or-later` | GPL 2.0 or later: free to share and modify, same license | committed |
| `GPL-3.0-only` | GPL 3.0: free to share and modify, same license | committed |
| `GPL-3.0-or-later` | GPL 3.0 or later: free to share and modify, same license | committed |
| `Apache-2.0` | Apache 2.0: free to share and modify, keep the notice | committed |
| `BSD-3-Clause` | BSD 3-Clause: free to share and modify, keep the notice | committed |
| `MIT` | MIT: free to share and modify, keep the notice | committed |
| `LGPL-2.0-or-later` | LGPL 2.0 or later: free to share and modify | committed |
| `LGPL-2.1-only` | LGPL 2.1: free to share and modify | committed |
| `LGPL-2.1-or-later` | LGPL 2.1 or later: free to share and modify | committed |
| `LGPL-3.0-only` | LGPL 3.0: free to share and modify | committed |
| `LGPL-3.0-or-later` | LGPL 3.0 or later: free to share and modify | committed |
| `unsplash-license` | Unsplash License: free to use, not to be sold unaltered or used for a competing service | download-only |
| `personal` | Your own files | download-only |
| `CC-BY-1.0` | CC BY 1.0: credit the author if you republish | committed |
| `CC-BY-SA-1.0` | CC BY-SA 1.0: credit and share alike if you republish | committed |
| `CC-BY-SA-2.5` | CC BY-SA 2.5: credit and share alike if you republish | committed |

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
- The UI theme is read from `~/.config/wallman/config.toml` (`[theme] palette`, `mode`, `custom_palette`). Without that file it follows your live Noctalia palette, falling back to the built-in Noctalia palette with a warning in the status bar when Noctalia's settings cannot be read; `just theme-init` writes a commented starting file.
- The UI serves a plain-language page about the licenses at `/docs/licenses`.
- Nothing commits or pushes: the UI and the add/rm/mv/sync commands only stage. Review with `git status`, then commit yourself.
- Sorting is done by deleting images: in the UI, with `just rm`, or by deleting the files and running `just sync --stage`, which drops their entries and stages their deletion (the pre-commit hook does the same).
- `just fetch` downloads each missing file from its `download` URL and refuses it if the checksum differs. The `crop = [x, y, w, h]` of an entry is re-applied after the download whenever `crop` is set.
- External collections such as `images/ml4w/` are git submodules with their own history and `wallpapers.toml`. Commit inside the submodule first, then commit the new pointer here.
- On every commit the hook runs `just guard` (refuses staged images whose license is not public domain, CC, GPL, LGPL, MIT, BSD or Apache, or that are listed nowhere), `just validate` and `just sync --stage`; `.gitignore` and this README are refreshed and staged with the commit.
