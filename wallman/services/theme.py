"""Theme: resolves the palette (built-in, custom JSON or Noctalia's live one) and renders it as CSS variables.

Without a config file (or a `palette` key) the palette is "noctalia": the live one (Ruling v2-19). Noctalia 5.x
writes its live settings to $XDG_STATE_HOME/noctalia/settings.toml (NOCTALIA_STATE_HOME overrides the directory);
the documented config dir (NOCTALIA_CONFIG_HOME, else $XDG_CONFIG_HOME/noctalia, `*.toml`) is the fallback.
Noctalia docs followed (noctalia-shell, docs/user/theming/): `index.mdx` (`[theme]` keys `source`, `builtin`,
`custom_palette`) and `palette.mdx` (custom palette JSON: `dark`/`light` objects of `m…` keys, `light` optional).
"""
from __future__ import annotations

import json
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

PALETTES_JSON = Path(__file__).resolve().parent.parent / "web" / "palettes.json"
ROLE_KEYS = ["mPrimary", "mOnPrimary", "mSecondary", "mOnSecondary", "mTertiary", "mOnTertiary", "mError", "mOnError",
             "mSurface", "mOnSurface", "mSurfaceVariant", "mOnSurfaceVariant", "mOutline", "mShadow", "mHover", "mOnHover"]
DEFAULT = "Noctalia"  # the built-in palette used when the configured one cannot be resolved
DEFAULT_PALETTE = "noctalia"  # no config file or no `palette` key: follow the live Noctalia palette (Ruling v2-19)
MODES = ("dark", "light", "auto")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_NAME = re.compile(r"[\w][\w .-]*")  # a bare file name: no separators, not hidden


def load_builtin() -> dict[str, dict[str, dict[str, str]]]:
    data = json.loads(PALETTES_JSON.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# -- colour mixing (pure) -----------------------------------------------------------------------

def to_rgb(color: str) -> tuple[int, int, int]:
    if not _HEX.fullmatch(color):
        raise ValueError(f"not a #rrggbb colour: {color!r}")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(a: str, b: str, ratio: float) -> str:
    """`a` moved `ratio` (0..1) of the way towards `b`."""
    return to_hex(tuple(x + (y - x) * ratio for x, y in zip(to_rgb(a), to_rgb(b))))


def _kebab(key: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", key).lower()  # mOnSurface -> m-on-surface


@dataclass(frozen=True)
class Theme:
    name: str
    mode: str  # resolved: dark | light
    roles: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    files: tuple[Path, ...] = ()  # config files the result depends on (mtime source)


class _Invalid(Exception):
    pass


class ThemeService:
    def __init__(self, config_path: Path, noctalia_dir: Path, builtin: dict,
                 noctalia_state: Path | None = None, gtk_css: Path | None = None) -> None:
        self.config_path, self.noctalia_dir, self.builtin = Path(config_path), Path(noctalia_dir), builtin
        self.noctalia_state = Path(noctalia_state) if noctalia_state else None  # $XDG_STATE_HOME/noctalia
        self.gtk_css = Path(gtk_css) if gtk_css else None  # ~/.config/gtk-4.0/noctalia.css

    # -- public -----------------------------------------------------------------------------
    def resolve(self, prefers: str = "dark") -> Theme:
        prefers = prefers if prefers in ("dark", "light") else "dark"
        warnings: list[str] = []
        files: list[Path] = [self.config_path]
        conf: dict = {}
        if self.config_path.is_file():
            try:
                conf = tomllib.loads(self.config_path.read_text(encoding="utf-8")).get("theme", {})
                if not isinstance(conf, dict):
                    raise ValueError("[theme] must be a table")
            except (OSError, ValueError) as e:
                warnings.append(f"{self.config_path.name}: {e}; using the default palette")
                conf = {}
        mode = conf.get("mode", "auto")
        if mode not in MODES:
            warnings.append(f"mode {mode!r} is not dark, light or auto; using auto")
            mode = "auto"
        hint: dict = {}
        palette = conf.get("palette", DEFAULT_PALETTE)
        default = DEFAULT if DEFAULT in self.builtin else next(iter(self.builtin))
        name, variants = default, self.builtin[default]
        try:
            if not isinstance(palette, str):
                raise _Invalid("palette must be a string")
            if palette == "noctalia":  # the lowercase keyword; "Noctalia" is the built-in
                name, variants, hint = self._noctalia(files)
            elif palette == "custom":
                custom = conf.get("custom_palette")
                if not isinstance(custom, str) or not custom:
                    raise _Invalid("palette = \"custom\" needs custom_palette")
                name, variants = self._custom(self._wallman_palette_path(custom), custom, files)
            else:
                name, variants = self._builtin_named(palette)
        except _Invalid as e:
            warnings.append(f"{e}; using {default}")
            name, variants = default, self.builtin[default]
        if "mode" in hint:  # the palette is a single fixed variant (generated from the wallpaper)
            resolved = hint["mode"]
        elif mode != "auto":
            resolved = mode
        else:  # auto: follow Noctalia's own dark/light when following it, else the browser's preference
            resolved = hint.get("noctalia_mode") or prefers
        roles = dict(variants.get(resolved) or variants["dark"])
        if resolved == "dark" and hint.get("pure_black_dark"):
            roles["mSurface"] = "#000000"
        return Theme(name, resolved, roles, warnings, tuple(files))

    def css(self, prefers: str = "dark") -> str:
        t = self.resolve(prefers)
        r = t.roles
        decl = [f"--{_kebab(k)}:{r[k]};" for k in ROLE_KEYS]
        decl += [f"--container:{mix(r['mSurface'], r['mSurfaceVariant'], .5)};",
                 f"--container-high:{mix(r['mSurfaceVariant'], r['mOnSurface'], .06)};",
                 f"--primary-container:{mix(r['mSurface'], r['mPrimary'], .18)};",
                 f"--focus:{r['mPrimary']};", "--scrim:rgba(0,0,0,.5);", f"color-scheme:{t.mode};"]
        return ":root{" + "".join(decl) + "}\n"

    def mtime(self) -> float:
        """Newest mtime among the wallman config and the Noctalia files the current palette depends on."""
        best = 0.0
        for p in self.resolve().files:
            try:
                best = max(best, p.stat().st_mtime)
            except OSError:
                pass
        return best

    # -- sources ----------------------------------------------------------------------------
    def _builtin_named(self, name: str) -> tuple[str, dict]:
        for k, v in self.builtin.items():
            if k.lower() == name.lower():
                return k, v
        raise _Invalid(f"unknown palette {name!r}")

    def _wallman_palette_path(self, custom: str) -> Path:
        if "/" in custom or custom.endswith(".json"):
            try:
                return Path(custom).expanduser()
            except RuntimeError as e:  # ~unknown-user: a config typo, not a server error
                raise _Invalid(f"custom_palette {custom!r}: {e}") from None
        return self.config_path.parent / "palettes" / f"{custom}.json"

    def _custom(self, path: Path, label: str, files: list[Path]) -> tuple[str, dict]:
        files.append(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise _Invalid(f"palette file {path.name}: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("dark"), dict):
            raise _Invalid(f"palette file {path.name}: needs a \"dark\" object")
        variants: dict[str, dict[str, str]] = {}
        for mode in ("dark", "light"):
            src = data.get(mode, data["dark"] if mode == "light" else None)
            if not isinstance(src, dict):
                raise _Invalid(f"palette file {path.name}: \"{mode}\" must be an object")
            bad = [k for k in ROLE_KEYS if not (isinstance(src.get(k), str) and _HEX.fullmatch(src[k]))]
            if bad:
                raise _Invalid(f"palette file {path.name}: {mode} lacks valid #rrggbb {', '.join(bad[:3])}")
            variants[mode] = {k: src[k].lower() for k in ROLE_KEYS}
        return Path(label).stem, variants

    def _noctalia_settings(self, files: list[Path]) -> dict:
        """[theme] of Noctalia 5.x. Live settings: $XDG_STATE_HOME/noctalia/settings.toml (what the shell writes);
        fallback: the documented config dir (`*.toml`, merged in file name order)."""
        candidates = []
        if self.noctalia_state is not None:
            candidates.append(self.noctalia_state / "settings.toml")
        if self.noctalia_dir.is_dir():
            candidates += sorted(self.noctalia_dir.glob("*.toml"))
        found = [p for p in candidates if p.is_file()]
        if not found:
            where = " or ".join(str(p) for p in ([candidates[0]] if candidates else []) + [self.noctalia_dir / "*.toml"])
            raise _Invalid(f"Noctalia settings not found ({where})")
        if self.noctalia_state is not None and found[0] == self.noctalia_state / "settings.toml":
            found = found[:1]  # the live file wins outright
        theme: dict = {}
        for p in found:
            files.append(p)
            try:
                section = tomllib.loads(p.read_text(encoding="utf-8")).get("theme", {})
            except (OSError, ValueError) as e:
                raise _Invalid(f"Noctalia settings {p}: {e}") from None
            if isinstance(section, dict):
                theme.update(section)
        return theme

    def _noctalia(self, files: list[Path]) -> tuple[str, dict, dict]:
        theme = self._noctalia_settings(files)
        hint: dict = {"pure_black_dark": theme.get("pure_black_dark") is True}
        if theme.get("mode") in ("dark", "light"):
            hint["noctalia_mode"] = theme["mode"]
        source = theme.get("source", "builtin")
        if source == "builtin":
            return (*self._builtin_named(str(theme.get("builtin", DEFAULT))), hint)
        if source in ("custom", "community"):
            key = "custom_palette" if source == "custom" else "community_palette"
            name = theme.get(key)
            if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
                raise _Invalid(f"Noctalia {key} {name!r} is not a plain palette name")
            if source == "custom":
                path = self.noctalia_dir / "palettes" / f"{name}.json"
            else:  # downloaded by the shell: <state>/community-palettes/<url-encoded name>.json
                if self.noctalia_state is None:
                    raise _Invalid("Noctalia state directory unknown")
                path = self.noctalia_state / "community-palettes" / f"{urllib.parse.quote(name)}.json"
            return (*self._custom(path, name, files), hint)
        if source == "wallpaper":
            name, variants, extra = self._wallpaper(files)
            return name, variants, {**hint, **extra}
        raise _Invalid(f"Noctalia source {source!r} is not supported")

    def _wallpaper(self, files: list[Path]) -> tuple[str, dict, dict]:
        """source = "wallpaper": Noctalia only exposes the generated colours through its templates, so read the
        GTK4 template (`@define-color name #rrggbb;`) and map it onto the 16 roles. The file holds ONE variant
        (Noctalia's current mode), so the mode follows the surface luminance, not wallman's `mode`."""
        path = self.gtk_css
        if path is None or not path.is_file():
            raise _Invalid(f"Noctalia wallpaper colours: {path or 'gtk-4.0/noctalia.css'} not found "
                           "(enable the gtk4 template in Noctalia)")
        files.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise _Invalid(f"{path}: {e}") from None
        c = {m.group(1): m.group(2).lower() for m in re.finditer(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})\s*;", text)}
        need = ["accent_bg_color", "accent_fg_color", "window_bg_color", "window_fg_color"]
        missing = [k for k in need if k not in c]
        if missing:
            raise _Invalid(f"{path}: missing {', '.join(missing)}")
        return "Noctalia (wallpaper)", {"dark": _gtk_roles(c), "light": _gtk_roles(c)}, \
            {"mode": "dark" if luminance(c["window_bg_color"]) < .5 else "light"}


def luminance(color: str) -> float:
    r, g, b = (v / 255 for v in to_rgb(color))
    return .2126 * r + .7152 * g + .0722 * b


def _gtk_roles(c: dict[str, str]) -> dict[str, str]:
    """Mapping GTK4 template colours -> Noctalia roles. Direct: primary=accent_bg, onPrimary=accent_fg,
    error=error_bg (else destructive_bg), onError=error_fg, surface=window_bg, onSurface=window_fg,
    surfaceVariant=card_bg (else popover_bg, else surface mixed 8% to onSurface), hover=accent_bg.
    Derived: onSurfaceVariant=onSurface 25% towards surface; outline=surfaceVariant 30% towards onSurface;
    secondary=accent 35% towards onSurface; tertiary=accent 35% towards error (the generator exposes no
    other accents); onSecondary/onTertiary=accent_fg; shadow=#000000; onHover=accent_fg."""
    primary, on_primary = c["accent_bg_color"], c["accent_fg_color"]
    surface, on_surface = c["window_bg_color"], c["window_fg_color"]
    error = c.get("error_bg_color") or c.get("destructive_bg_color") or "#e5484d"
    variant = c.get("card_bg_color") or c.get("popover_bg_color") or mix(surface, on_surface, .08)
    return {"mPrimary": primary, "mOnPrimary": on_primary,
            "mSecondary": mix(primary, on_surface, .35), "mOnSecondary": on_primary,
            "mTertiary": mix(primary, error, .35), "mOnTertiary": on_primary,
            "mError": error, "mOnError": c.get("error_fg_color", on_primary),
            "mSurface": surface, "mOnSurface": on_surface,
            "mSurfaceVariant": variant, "mOnSurfaceVariant": mix(on_surface, surface, .25),
            "mOutline": mix(variant, on_surface, .30), "mShadow": "#000000",
            "mHover": primary, "mOnHover": on_primary}


def config_paths(config_home: Path | None = None) -> tuple[Path, Path, Path, Path]:
    """(wallman config.toml, Noctalia config dir, Noctalia state dir, GTK4 noctalia.css).
    `config_home` replaces the whole environment (tests): state lives in <config_home>/state."""
    import os
    if config_home is not None:
        return (config_home / "wallman" / "config.toml", config_home / "noctalia", config_home / "state" / "noctalia",
                config_home / "gtk-4.0" / "noctalia.css")
    home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    state = Path(os.environ.get("NOCTALIA_STATE_HOME") or Path(os.environ.get("XDG_STATE_HOME")
                 or Path.home() / ".local" / "state") / "noctalia")
    noctalia = os.environ.get("NOCTALIA_CONFIG_HOME")
    return home / "wallman" / "config.toml", Path(noctalia) if noctalia else home / "noctalia", state, \
        home / "gtk-4.0" / "noctalia.css"


def default_config_text(builtin: dict) -> str:
    names = ", ".join(builtin)
    return f"""# wallman web UI theme. Edit and save: the open page reloads the colours by itself.
[theme]
# A built-in Noctalia palette: {names}
# "noctalia" (lowercase, also the default without this file) follows the palette your Noctalia shell uses
# right now: its live settings in $XDG_STATE_HOME/noctalia/settings.toml (built-in, community or custom palette,
# or the wallpaper colours from ~/.config/gtk-4.0/noctalia.css). If they cannot be read, the built-in
# "Noctalia" palette is used and the UI shows a warning.
# "custom" loads custom_palette.
palette = "noctalia"

# dark | light | auto (auto follows the browser's prefers-color-scheme)
mode = "auto"

# Used when palette = "custom": a file name (without .json) in $XDG_CONFIG_HOME/wallman/palettes/,
# or a path to a JSON file. Format: {{"dark": {{"mPrimary": "#rrggbb", ...16 m... keys}}, "light": {{...}}}}
# custom_palette = "MyPalette"
"""


def write_default_config(path: Path, builtin: dict) -> None:
    """Create the commented default config; FileExistsError if there is one already."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(default_config_text(builtin))
