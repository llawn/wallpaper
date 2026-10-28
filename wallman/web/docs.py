"""HTML pages for the license docs (`/docs/licenses`), rendered from wallman/docs/licenses/*.md."""
from __future__ import annotations

import html
import re
from pathlib import Path

from ..domain import licenses
from .markdown import parse_front_matter, render_markdown

DOCS = Path(__file__).resolve().parent.parent / "docs" / "licenses"
BASE = "/docs/licenses"
KINDS = (("open", "Open licenses"), ("specific", "Specific terms"), ("public-domain", "Public domain"))
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")

_CSS = """
body{margin:0;background:var(--m-surface);color:var(--m-on-surface);font:15px/1.6 system-ui,sans-serif}
main{max-width:52rem;margin:0 auto;padding:1.5rem 1rem 4rem}
a{color:var(--m-primary)}
nav{margin-bottom:1.5rem;font-size:.9rem}
h1,h2,h3{line-height:1.25}h2{margin-top:2rem}
ul.lic{list-style:none;padding:0}
ul.lic li{display:flex;gap:.75rem;align-items:baseline;padding:.45rem 0;border-bottom:1px solid var(--m-outline)}
ul.lic .label{color:var(--m-on-surface-variant);font-size:.875rem;flex:1}
.badge{font-size:.75rem;padding:.05rem .5rem;border-radius:999px;border:1px solid var(--m-outline);white-space:nowrap}
.badge.yes{background:var(--primary-container);color:var(--m-on-surface)}
.meta{color:var(--m-on-surface-variant);font-size:.9rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--container);border:1px solid var(--m-outline);
  border-radius:6px;padding:1rem;font:13px/1.5 ui-monospace,monospace}
code{font-family:ui-monospace,monospace}
"""
_THEME_JS = ("var l=document.getElementById('theme');if(window.matchMedia&&matchMedia('(prefers-color-scheme: light)').matches)"
             "{l.href='/api/theme?scheme=light'}")


def _page(title: str, body: str, status: int = 200) -> tuple[int, str]:
    doc = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
           "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
           f"<title>{html.escape(title)} · wallman</title>"
           "<link id=theme rel=stylesheet href=\"/api/theme\">"
           f"<style>{_CSS}</style></head><body><main>"
           f"<nav><a href=\"/\">&larr; wallman</a> · <a href=\"{BASE}\">Licenses</a></nav>"
           f"{body}</main><script>{_THEME_JS}</script></body></html>")
    return status, doc


def _meta(lid: str) -> dict[str, str]:
    return parse_front_matter((DOCS / f"{lid}.md").read_text(encoding="utf-8"))[0]


def _badge(shareable: bool) -> str:
    return ('<span class="badge yes">Shareable</span>' if shareable
            else '<span class="badge">Download only</span>')


def index_page() -> tuple[int, str]:
    groups: dict[str, list[str]] = {k: [] for k, _ in KINDS}
    for lid in sorted(licenses.LICENSES):
        groups[_meta(lid)["kind"]].append(lid)
    parts = ["<h1>Licenses</h1>",
             "<p class=meta>Every wallpaper in the collection carries one exact license. "
             "<em>Shareable</em> licenses allow the file to be committed to the git repository; "
             "the others are downloaded on demand and never committed.</p>"]
    for kind, heading in KINDS:
        if not groups[kind]:
            continue
        parts.append(f"<h2>{heading}</h2><ul class=lic>")
        for lid in groups[kind]:
            m = _meta(lid)
            parts.append(f'<li><a href="{BASE}/{html.escape(lid)}">{html.escape(m["name"])}</a>'
                         f'<span class=label>{html.escape(licenses.LABELS.get(lid, ""))}</span>'
                         f'{_badge(licenses.is_shareable(lid))}</li>')
        parts.append("</ul>")
    return _page("Licenses", "".join(parts))


def license_page(lid: str) -> tuple[int, str]:
    if lid not in licenses.LICENSES or not _ID.fullmatch(lid) or not (DOCS / f"{lid}.md").is_file():
        return _page("Not found", "<h1>Not found</h1><p>There is no such license. "
                     f"<a href=\"{BASE}\">All licenses</a></p>", 404)
    meta, body = parse_front_matter((DOCS / f"{lid}.md").read_text(encoding="utf-8"))
    parts = [f"<h1>{html.escape(meta['name'])}</h1>",
             f"<p class=meta><code>{html.escape(lid)}</code> · {_badge(licenses.is_shareable(lid))}"]
    if meta.get("url"):
        parts.append(f' · <a href="{html.escape(meta["url"], quote=True)}" rel="noopener noreferrer" '
                     f'target=_blank>Official page</a>')
    parts.append("</p>")
    parts.append(render_markdown(body))
    text = DOCS / "texts" / f"{lid}.txt"
    if text.is_file():
        parts.append("<h2>Full text</h2>"
                     f"<pre>{html.escape(text.read_text(encoding='utf-8'))}</pre>")
    return _page(meta["name"], "".join(parts))
