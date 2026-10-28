"""ThemeService: built-in palettes, wallman config, Noctalia following, colour mixing, theme-init."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from wallman.cli.main import main
from wallman.services import theme as T
from wallman.services.theme import ROLE_KEYS, ThemeService, load_builtin, mix

BUILTIN = load_builtin()


def pal(primary="#112233", surface="#000000"):
    roles = {k: "#101010" for k in ROLE_KEYS}
    roles.update(mPrimary=primary, mSurface=surface, mOnSurface="#ffffff", mSurfaceVariant="#202020")
    return roles


class ThemeBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.cfg = self.base / "wallman" / "config.toml"
        self.nd = self.base / "noctalia"
        self.state = self.base / "state" / "noctalia"
        self.gtk = self.base / "gtk-4.0" / "noctalia.css"

    def write_cfg(self, text):
        self.cfg.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(text)

    def write_nd(self, text, name="config.toml"):
        self.nd.mkdir(parents=True, exist_ok=True)
        (self.nd / name).write_text(text)

    def write_palette(self, name, data, where=None):
        d = (where or self.nd) / "palettes"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(data))

    def svc(self):
        return ThemeService(self.cfg, self.nd, BUILTIN, self.state, self.gtk)


class BuiltinTest(ThemeBase):
    def test_ten_builtins_with_sixteen_roles(self):
        self.assertEqual(len(BUILTIN), 10)
        self.assertNotIn("_source", BUILTIN)
        for name, modes in BUILTIN.items():
            for mode in ("dark", "light"):
                self.assertEqual(set(modes[mode]), set(ROLE_KEYS), (name, mode))

    def test_each_builtin_resolves_dark_and_light(self):
        for name, modes in BUILTIN.items():
            for mode in ("dark", "light"):
                self.write_cfg(f'[theme]\npalette = "{name}"\nmode = "{mode}"\n')
                t = self.svc().resolve()
                self.assertEqual((t.name, t.mode, t.roles, t.warnings), (name, mode, modes[mode], []))

    def test_no_config_file_follows_live_noctalia(self):
        """Ruling v2-19: without a config file the palette is "noctalia" (follow the live shell)."""
        self.state.mkdir(parents=True)
        (self.state / "settings.toml").write_text('[theme]\nsource = "builtin"\nbuiltin = "Gruvbox"\n')
        t = self.svc().resolve("light")
        self.assertEqual((t.name, t.mode, t.roles, t.warnings), ("Gruvbox", "light", BUILTIN["Gruvbox"]["light"], []))
        self.assertEqual(self.svc().resolve("dark").mode, "dark")

    def test_no_config_file_and_no_noctalia_falls_back_with_warning(self):
        t = self.svc().resolve("light")
        self.assertEqual((t.name, t.mode, t.roles), ("Noctalia", "light", BUILTIN["Noctalia"]["light"]))
        self.assertEqual(len(t.warnings), 1)
        self.assertIn("Noctalia settings not found", t.warnings[0])

    def test_config_without_palette_key_follows_live_noctalia(self):
        self.write_cfg('[theme]\nmode = "dark"\n')
        self.state.mkdir(parents=True)
        (self.state / "settings.toml").write_text('[theme]\nsource = "builtin"\nbuiltin = "Nord"\n')
        self.assertEqual(self.svc().resolve().name, "Nord")

    def test_palette_name_is_case_insensitive(self):
        self.write_cfg('[theme]\npalette = "tokyo-night"\nmode = "dark"\n')
        self.assertEqual(self.svc().resolve().name, "Tokyo-Night")

    def test_auto_uses_scheme_param_explicit_ignores_it(self):
        self.write_cfg('[theme]\npalette = "Nord"\nmode = "auto"\n')
        self.assertEqual(self.svc().resolve("light").roles, BUILTIN["Nord"]["light"])
        self.assertEqual(self.svc().resolve("dark").roles, BUILTIN["Nord"]["dark"])
        self.assertEqual(self.svc().resolve("bogus").mode, "dark")
        self.write_cfg('[theme]\npalette = "Nord"\nmode = "dark"\n')
        self.assertEqual(self.svc().resolve("light").mode, "dark")

    def test_unknown_palette_and_bad_mode_warn(self):
        self.write_cfg('[theme]\npalette = "Nope"\nmode = "sepia"\n')
        t = self.svc().resolve("dark")
        self.assertEqual(t.name, "Noctalia")
        self.assertEqual(len(t.warnings), 2)

    def test_broken_toml_falls_back_with_warning(self):
        self.write_cfg("[theme\n")
        t = self.svc().resolve("dark")
        # the default is then the live Noctalia palette, absent here: one warning each
        self.assertEqual((t.name, len(t.warnings)), ("Noctalia", 2))

    def test_custom_palette_with_unknown_user_warns_instead_of_raising(self):
        self.write_cfg('[theme]\npalette = "custom"\ncustom_palette = "~no-such-user-xyz/p.json"\n')
        t = self.svc().resolve("dark")
        self.assertEqual((t.name, len(t.warnings)), ("Noctalia", 1))

    def test_custom_palette_file_in_wallman_dir(self):
        self.write_palette("mine", {"dark": pal("#aa0000")}, where=self.cfg.parent)
        self.write_cfg('[theme]\npalette = "custom"\ncustom_palette = "mine"\nmode = "light"\n')
        t = self.svc().resolve()
        self.assertEqual((t.name, t.roles["mPrimary"], t.warnings), ("mine", "#aa0000", []))  # light falls back to dark

    def test_custom_palette_missing_warns(self):
        self.write_cfg('[theme]\npalette = "custom"\ncustom_palette = "ghost"\n')
        t = self.svc().resolve("dark")
        self.assertEqual((t.name, len(t.warnings)), ("Noctalia", 1))


class NoctaliaTest(ThemeBase):
    def test_follows_builtin(self):
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "dark"\n')
        self.write_nd('[theme]\nsource = "builtin"\nbuiltin = "Gruvbox"\n')
        t = self.svc().resolve()
        self.assertEqual((t.name, t.roles, t.warnings), ("Gruvbox", BUILTIN["Gruvbox"]["dark"], []))

    def test_follows_custom_json(self):
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "light"\n')
        self.write_nd('[theme]\nsource = "custom"\ncustom_palette = "MyPalette"\n')
        self.write_palette("MyPalette", {"dark": pal("#111111"), "light": pal("#eeeeee")})
        t = self.svc().resolve()
        self.assertEqual((t.name, t.roles["mPrimary"], t.warnings), ("MyPalette", "#eeeeee", []))

    def test_any_toml_in_noctalia_dir_is_read(self):
        self.write_cfg('[theme]\npalette = "noctalia"\n')
        self.write_nd('[theme]\nsource = "builtin"\nbuiltin = "Dracula"\n', "theme.toml")
        self.assertEqual(self.svc().resolve("dark").name, "Dracula")

    def test_invalid_noctalia_palette_falls_back(self):
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "dark"\n')
        for nd in ('[theme]\nsource = "custom"\ncustom_palette = "Missing"\n',
                   '[theme]\nsource = "custom"\ncustom_palette = "../evil"\n',
                   '[theme]\nsource = "builtin"\nbuiltin = "Nope"\n',
                   '[theme]\nsource = "wallpaper"\n',
                   '[theme\n'):
            self.write_nd(nd)
            t = self.svc().resolve()
            self.assertEqual((t.name, t.roles), ("Noctalia", BUILTIN["Noctalia"]["dark"]), nd)
            self.assertEqual(len(t.warnings), 1, nd)

    def test_invalid_palette_json_falls_back(self):
        self.write_cfg('[theme]\npalette = "noctalia"\n')
        self.write_nd('[theme]\nsource = "custom"\ncustom_palette = "Bad"\n')
        for payload in ({"dark": {"mPrimary": "red"}}, {"dark": "x"}, [], {"light": pal()}):
            self.write_palette("Bad", payload)
            t = self.svc().resolve("dark")
            self.assertEqual((t.name, len(t.warnings)), ("Noctalia", 1), payload)
        (self.nd / "palettes" / "Bad.json").write_text("{nope")
        self.assertEqual(len(self.svc().resolve("dark").warnings), 1)

    def test_missing_noctalia_config_falls_back(self):
        self.write_cfg('[theme]\npalette = "noctalia"\n')
        t = self.svc().resolve("dark")
        self.assertEqual((t.name, len(t.warnings)), ("Noctalia", 1))

    def test_mtime_tracks_noctalia_files(self):
        self.write_cfg('[theme]\npalette = "noctalia"\n')
        self.write_nd('[theme]\nsource = "custom"\ncustom_palette = "P"\n')
        self.write_palette("P", {"dark": pal()})
        s = self.svc()
        before = s.mtime()
        os.utime(self.nd / "palettes" / "P.json", (before + 50, before + 50))
        self.assertEqual(s.mtime(), before + 50)
        os.utime(self.cfg, (before + 90, before + 90))
        self.assertEqual(s.mtime(), before + 90)

    def test_mtime_without_files_is_zero(self):
        self.assertEqual(self.svc().mtime(), 0.0)


GTK_CSS = """/* Generated by Noctalia */
@define-color accent_color #a1c8cd;
@define-color accent_bg_color #a1c8cd;
@define-color accent_fg_color #1b2122;
@define-color error_bg_color #fd4663;
@define-color error_fg_color #1b2122;
@define-color window_bg_color #000000;
@define-color window_fg_color #f2f3f3;
@define-color popover_bg_color #101e20;
@define-color card_bg_color #101e20;
@define-color headerbar_backdrop_color @window_bg_color;
"""
COMMUNITY = {"dark": {**pal("#00a8f4", "#000000"), "terminal": {"normal": {"black": "#000000"}}},
             "light": pal("#0284c7", "#f0faff")}


class LiveNoctaliaTest(ThemeBase):
    """Noctalia 5.x reality: settings in $XDG_STATE_HOME/noctalia/settings.toml, community palettes and the GTK template."""
    def setUp(self):
        super().setUp()
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "auto"\n')

    def settings(self, text):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "settings.toml").write_text(text)

    def test_state_settings_win_over_config_dir(self):
        self.write_nd('[theme]\nsource = "builtin"\nbuiltin = "Dracula"\n')
        self.settings('[theme]\nsource = "builtin"\nbuiltin = "Gruvbox"\nmode = "light"\n')
        t = self.svc().resolve("dark")
        self.assertEqual((t.name, t.mode, t.roles), ("Gruvbox", "light", BUILTIN["Gruvbox"]["light"]))

    def test_explicit_wallman_mode_beats_noctalia_mode(self):
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "dark"\n')
        self.settings('[theme]\nsource = "builtin"\nbuiltin = "Nord"\nmode = "light"\n')
        self.assertEqual(self.svc().resolve("light").mode, "dark")

    def test_community_palette(self):
        self.settings('[theme]\nsource = "community"\ncommunity_palette = "Neon Surf"\nmode = "dark"\n')
        (self.state / "community-palettes").mkdir()
        (self.state / "community-palettes" / "Neon%20Surf.json").write_text(json.dumps(COMMUNITY))
        t = self.svc().resolve("light")
        self.assertEqual((t.name, t.mode, t.roles["mPrimary"], t.warnings), ("Neon Surf", "dark", "#00a8f4", []))
        self.write_cfg('[theme]\npalette = "noctalia"\nmode = "light"\n')
        self.assertEqual(self.svc().resolve().roles["mPrimary"], "#0284c7")

    def test_community_missing_warns_with_file_name(self):
        self.settings('[theme]\nsource = "community"\ncommunity_palette = "Neon Surf"\n')
        t = self.svc().resolve("dark")
        self.assertEqual(t.name, "Noctalia")
        self.assertIn("Neon%20Surf.json", t.warnings[0])

    def test_wallpaper_source_maps_gtk_css(self):
        self.settings('[theme]\nsource = "wallpaper"\nmode = "dark"\n')
        self.gtk.parent.mkdir(parents=True)
        self.gtk.write_text(GTK_CSS)
        t = self.svc().resolve("light")  # one variant only: mode comes from the surface luminance
        r = t.roles
        self.assertEqual((t.mode, t.warnings), ("dark", []))
        self.assertEqual((r["mPrimary"], r["mOnPrimary"], r["mError"], r["mOnError"], r["mSurface"], r["mOnSurface"],
                          r["mSurfaceVariant"], r["mHover"], r["mShadow"]),
                         ("#a1c8cd", "#1b2122", "#fd4663", "#1b2122", "#000000", "#f2f3f3", "#101e20", "#a1c8cd", "#000000"))
        self.assertEqual(set(r), set(ROLE_KEYS))
        self.assertTrue(all(v.startswith("#") and len(v) == 7 for v in r.values()))
        self.assertEqual(r["mSecondary"], mix("#a1c8cd", "#f2f3f3", .35))

    def test_wallpaper_light_surface_detected(self):
        self.settings('[theme]\nsource = "wallpaper"\n')
        self.gtk.parent.mkdir(parents=True)
        self.gtk.write_text(GTK_CSS.replace("window_bg_color #000000", "window_bg_color #f5f5f5"))
        self.assertEqual(self.svc().resolve("dark").mode, "light")

    def test_wallpaper_missing_or_incomplete_css_warns(self):
        self.settings('[theme]\nsource = "wallpaper"\n')
        t = self.svc().resolve("dark")
        self.assertEqual(t.name, "Noctalia")
        self.assertIn("noctalia.css", t.warnings[0])
        self.gtk.parent.mkdir(parents=True)
        self.gtk.write_text("@define-color accent_bg_color #ffffff;")
        self.assertIn("missing", self.svc().resolve("dark").warnings[0])

    def test_pure_black_dark(self):
        self.settings('[theme]\nsource = "builtin"\nbuiltin = "Nord"\nmode = "dark"\npure_black_dark = true\n')
        r = self.svc().resolve().roles
        self.assertEqual(r["mSurface"], "#000000")
        self.assertEqual(r["mOnSurface"], BUILTIN["Nord"]["dark"]["mOnSurface"])
        self.settings('[theme]\nsource = "builtin"\nbuiltin = "Nord"\nmode = "light"\npure_black_dark = true\n')
        self.assertEqual(self.svc().resolve().roles, BUILTIN["Nord"]["light"])

    def test_missing_settings_warning_names_files(self):
        w = self.svc().resolve("dark").warnings[0]
        self.assertIn(str(self.state / "settings.toml"), w)

    def test_mtime_includes_settings_community_and_css(self):
        self.settings('[theme]\nsource = "community"\ncommunity_palette = "P"\n')
        (self.state / "community-palettes").mkdir()
        j = self.state / "community-palettes" / "P.json"
        j.write_text(json.dumps(COMMUNITY))
        base = self.svc().mtime()
        os.utime(j, (base + 10, base + 10))
        self.assertEqual(self.svc().mtime(), base + 10)
        os.utime(self.state / "settings.toml", (base + 20, base + 20))
        self.assertEqual(self.svc().mtime(), base + 20)
        self.settings('[theme]\nsource = "wallpaper"\n')
        self.gtk.parent.mkdir(parents=True)
        self.gtk.write_text(GTK_CSS)
        os.utime(self.gtk, (base + 99, base + 99))
        os.utime(self.state / "settings.toml", (base + 1, base + 1))
        self.assertEqual(self.svc().mtime(), base + 99)


class MixTest(unittest.TestCase):
    def test_mix(self):
        self.assertEqual(mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(mix("#102030", "#ffffff", 0), "#102030")
        self.assertEqual(mix("#102030", "#ffffff", 1), "#ffffff")
        self.assertEqual(mix("#ff0000", "#0000ff", 0.25), "#bf0040")

    def test_parse_and_format(self):
        self.assertEqual(T.to_rgb("#0A0b0C"), (10, 11, 12))
        self.assertEqual(T.to_hex((255, 0, 128)), "#ff0080")
        with self.assertRaises(ValueError):
            T.to_rgb("red")


class CssTest(ThemeBase):
    def test_css_variables_and_derived(self):
        self.write_cfg('[theme]\npalette = "Nord"\nmode = "dark"\n')
        css = self.svc().css()
        r = BUILTIN["Nord"]["dark"]
        self.assertTrue(css.startswith(":root{"))
        self.assertIn(f"--m-primary:{r['mPrimary']};", css)
        self.assertIn(f"--m-on-surface-variant:{r['mOnSurfaceVariant']};", css)
        self.assertIn(f"--container:{mix(r['mSurface'], r['mSurfaceVariant'], .5)};", css)
        self.assertIn(f"--container-high:{mix(r['mSurfaceVariant'], r['mOnSurface'], .06)};", css)
        self.assertIn(f"--primary-container:{mix(r['mSurface'], r['mPrimary'], .18)};", css)
        self.assertIn(f"--focus:{r['mPrimary']};", css)
        self.assertIn("--scrim:rgba(0,0,0,.5);", css)
        self.assertIn("color-scheme:dark;", css)

    def test_css_follows_scheme_for_auto(self):
        self.assertIn("color-scheme:light;", self.svc().css("light"))


class ThemeInitTest(ThemeBase):
    def run_cli(self, *args):
        env = {"XDG_CONFIG_HOME": str(self.base)}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in old.items()])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args), cache_dir=self.base / "cache")

    def test_writes_commented_default_and_refuses_overwrite(self):
        self.assertEqual(self.run_cli("theme-init"), 0)
        text = self.cfg.read_text()
        for needle in ("[theme]", "palette", "mode", "custom_palette", "Tokyo-Night", "noctalia"):
            self.assertIn(needle, text)
        self.write_nd('[theme]\nsource = "builtin"\nbuiltin = "Nord"\n')
        t = ThemeService(self.cfg, self.nd, BUILTIN, self.state, self.gtk).resolve("dark")
        self.assertEqual((t.name, t.warnings), ("Nord", []))  # the default follows Noctalia
        self.cfg.write_text("# mine\n")
        self.assertEqual(self.run_cli("theme-init"), 1)
        self.assertEqual(self.cfg.read_text(), "# mine\n")


if __name__ == "__main__":
    unittest.main()
