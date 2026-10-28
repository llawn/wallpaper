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
  - [`painting/fantasy-and-fairy/`](images/painting/fantasy-and-fairy/wallpapers.toml)
  - [`painting/group-of-seven/`](images/painting/group-of-seven/wallpapers.toml)
  - [`painting/hudson-river-school/`](images/painting/hudson-river-school/wallpapers.toml)
  - [`painting/impressionism/`](images/painting/impressionism/wallpapers.toml)
  - [`painting/marine/`](images/painting/marine/wallpapers.toml)
  - [`painting/nocturne/`](images/painting/nocturne/wallpapers.toml)
  - [`painting/post-impressionism/`](images/painting/post-impressionism/wallpapers.toml)
  - [`painting/romanticism/`](images/painting/romanticism/wallpapers.toml)
  - [`painting/ruins-and-apocalypse/`](images/painting/ruins-and-apocalypse/wallpapers.toml)
  - [`painting/russian-realism/`](images/painting/russian-realism/wallpapers.toml)
  - [`painting/symbolism/`](images/painting/symbolism/wallpapers.toml)
- Illustration
  - [`illustration/satirical-press/`](images/illustration/satirical-press/wallpapers.toml)
- Printmaking (woodblock, etching, engraving)
  - [`printmaking/shin-hanga/`](images/printmaking/shin-hanga/wallpapers.toml)
  - [`printmaking/ukiyo-e/`](images/printmaking/ukiyo-e/wallpapers.toml)
- Photography
  - [`photography/abandoned/`](images/photography/abandoned/wallpapers.toml)
  - [`photography/astronomy/`](images/photography/astronomy/wallpapers.toml)
  - [`photography/cyberpunk-neon/`](images/photography/cyberpunk-neon/wallpapers.toml)
  - [`photography/frutiger-aero/`](images/photography/frutiger-aero/wallpapers.toml)
  - [`photography/landscape/`](images/photography/landscape/wallpapers.toml)
  - [`photography/lyon/`](images/photography/lyon/wallpapers.toml)
- Animation
  - [`animation/blue-sky/`](images/animation/blue-sky/) — one folder per subfolder
  - [`animation/disney/`](images/animation/disney/) — one folder per subfolder
  - [`animation/dreamworks/`](images/animation/dreamworks/) — one folder per subfolder
  - [`animation/illumination/`](images/animation/illumination/) — one folder per subfolder
  - [`animation/independent/`](images/animation/independent/) — one folder per subfolder
  - [`animation/pixar/`](images/animation/pixar/) — one folder per subfolder
  - [`animation/studio-ghibli/`](images/animation/studio-ghibli/) — one folder per subfolder
- Linux (official distribution and desktop wallpapers)
  - [`linux/desktops/`](images/linux/desktops/) — one folder per subfolder
  - [`linux/distributions/`](images/linux/distributions/) — one folder per subfolder
- ML4W collection
  - [`ml4w/`](images/ml4w/wallpapers.toml) (git submodule)

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
- The UI theme is read from `~/.config/wallman/config.toml`: see [UI theme](#ui-theme) below.
- The UI serves a plain-language page about the licenses at `/docs/licenses`.
- Nothing commits or pushes: the UI and the add/rm/mv/sync commands only stage. Review with `git status`, then commit yourself.
- Sorting is done by deleting images: in the UI, with `just rm`, or by deleting the files and running `just sync --stage`, which drops their entries and stages their deletion (the pre-commit hook does the same).
- `just fetch` downloads each missing file from its `download` URL and refuses it if the checksum differs. The `crop = [x, y, w, h]` of an entry is re-applied after the download whenever `crop` is set.
- External collections such as `images/ml4w/` are git submodules with their own history and `wallpapers.toml`. Commit inside the submodule first, then commit the new pointer here.
- On every commit the hook runs `just guard` (refuses staged images whose license is not public domain, CC, GPL, LGPL, MIT, BSD or Apache, or that are listed nowhere), `just sync --stage` and `just validate`, whose problems are flagged in the commit output without blocking it; `.gitignore` and this README are refreshed and staged with the commit.

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
```
