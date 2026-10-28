"""A tiny, safe Markdown subset for the license docs. Every character of input is HTML-escaped."""
from __future__ import annotations

import html
import re

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%]+")
_DOCS = re.compile(r"/docs(?:/[A-Za-z0-9._-]+)*")
_HEADING = re.compile(r"(#{1,3})\s+(.*)")
_UL = re.compile(r"[-*]\s+(.*)")
_OL = re.compile(r"\d+\.\s+(.*)")


def safe_url(url: str) -> bool:
    """http(s) absolute URLs and relative /docs paths only."""
    if "'" in url:
        return False
    if _URL.fullmatch(url):
        return True
    return bool(_DOCS.fullmatch(url)) and ".." not in url.split("/")


def _inline(text: str) -> str:
    stash: list[str] = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"\x00{len(stash) - 1}\x00"

    text = text.replace("\x00", "")
    text = _CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not safe_url(url):
            return m.group(0)
        external = url.startswith("http")
        rel = ' rel="noopener noreferrer" target="_blank"' if external else ""
        return keep(f'<a href="{html.escape(url, quote=True)}"{rel}>{html.escape(label)}</a>')

    text = _LINK.sub(link, text)
    text = html.escape(text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def render_markdown(text: str) -> str:
    """Headings (#..###), paragraphs, unordered/ordered lists, inline code, bold, italic, safe links."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush() -> None:
        nonlocal list_tag
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append(f"<{list_tag}>{''.join(f'<li>{_inline(i)}</li>' for i in items)}</{list_tag}>")
            items.clear()
            list_tag = ""

    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            flush()
        elif m := _HEADING.fullmatch(line):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif (m := _UL.fullmatch(line)) or (m := _OL.fullmatch(line)):
            tag = "ul" if _UL.fullmatch(line) else "ol"
            if para or (items and tag != list_tag):
                flush()
            list_tag = tag
            items.append(m.group(1))
        elif items and line[:1] in " \t":
            items[-1] += " " + line.strip()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """`---` delimited `key: value` lines at the top; returns (meta, body). No front matter -> ({}, text)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        key, sep, value = lines[i].partition(":")
        if sep and key.strip():
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            meta[key.strip()] = value
    return {}, text
