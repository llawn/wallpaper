#!/usr/bin/env python3
"""Extract Noctalia's built-in palettes into wallman/web/palettes.json.

Source: noctalia-shell (MIT) src/theme/builtin_palettes.cpp.
Usage: extract_noctalia_palettes.py [path-or-url] [output]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/noctalia-dev/noctalia-shell/main/src/theme/builtin_palettes.cpp"
OUT = Path(__file__).resolve().parent.parent / "wallman" / "web" / "palettes.json"
ROLES = ["primary", "onPrimary", "secondary", "onSecondary", "tertiary", "onTertiary", "error", "onError",
         "surface", "onSurface", "surfaceVariant", "onSurfaceVariant", "outline", "shadow", "hover", "onHover"]
SOURCE = {"url": URL, "license": "MIT (noctalia-shell, https://github.com/noctalia-dev/noctalia-shell)"}


def key(role: str) -> str:
    return "m" + role[0].upper() + role[1:]


def parse(text: str) -> dict:
    out: dict = {"_source": SOURCE}
    names = list(re.finditer(r'\.name\s*=\s*"([^"]+)"', text))
    for i, m in enumerate(names):
        block = text[m.end(): names[i + 1].start() if i + 1 < len(names) else len(text)]
        modes = {}
        for mode in ("dark", "light"):
            at = re.search(rf"\.{mode}\s*=\s*FixedPaletteMode", block)
            fp = re.compile(r"FixedPalette\s*\{(.*?)\}", re.S).search(block, at.end()) if at else None
            if not fp:
                raise SystemExit(f"{m.group(1)}: no {mode} palette")
            roles = {}
            for role in ROLES:
                c = re.search(rf'\.{role}\s*=\s*hex\("(#[0-9A-Fa-f]{{6}})"\)', fp.group(1))
                if not c:
                    raise SystemExit(f"{m.group(1)}/{mode}: missing {role}")
                roles[key(role)] = c.group(1).lower()
            modes[mode] = roles
        out[m.group(1)] = modes
    return out


def main(argv: list[str]) -> int:
    src = argv[1] if len(argv) > 1 else URL
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=30) as r:
            text = r.read().decode()
    else:
        text = Path(src).read_text()
    data = parse(text)
    out = Path(argv[2]) if len(argv) > 2 else OUT
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(data) - 1} palettes -> {out}: {', '.join(k for k in data if k != '_source')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
