#!/usr/bin/env python3
"""Fetch the canonical texts of the open licenses from SPDX license-list-data (run once, commit the result).

    python3 scripts/fetch_license_texts.py
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/spdx/license-list-data/main/text/{}.txt"
OUT = Path(__file__).resolve().parent.parent / "wallman" / "docs" / "licenses" / "texts"

# wallman id -> SPDX id of the text (the -only / -or-later variants share the base text)
MAP = {
    "CC0-1.0": "CC0-1.0",
    "CC-BY-1.0": "CC-BY-1.0", "CC-BY-2.0": "CC-BY-2.0", "CC-BY-2.5": "CC-BY-2.5",
    "CC-BY-3.0": "CC-BY-3.0", "CC-BY-4.0": "CC-BY-4.0",
    "CC-BY-SA-1.0": "CC-BY-SA-1.0", "CC-BY-SA-2.0": "CC-BY-SA-2.0", "CC-BY-SA-2.5": "CC-BY-SA-2.5",
    "CC-BY-SA-3.0": "CC-BY-SA-3.0", "CC-BY-SA-3.0-IGO": "CC-BY-SA-3.0-IGO", "CC-BY-SA-4.0": "CC-BY-SA-4.0",
    "GPL-2.0-only": "GPL-2.0-only", "GPL-2.0-or-later": "GPL-2.0-or-later",
    "GPL-3.0-only": "GPL-3.0-only", "GPL-3.0-or-later": "GPL-3.0-or-later",
    "LGPL-2.0-or-later": "LGPL-2.0-or-later", "LGPL-2.1-only": "LGPL-2.1-only",
    "LGPL-2.1-or-later": "LGPL-2.1-or-later", "LGPL-3.0-only": "LGPL-3.0-only",
    "LGPL-3.0-or-later": "LGPL-3.0-or-later",
    "MIT": "MIT", "BSD-3-Clause": "BSD-3-Clause", "Apache-2.0": "Apache-2.0",
}
# fallbacks when SPDX has no file under the exact id
FALLBACK = {"GPL-2.0-only": "GPL-2.0", "GPL-2.0-or-later": "GPL-2.0", "GPL-3.0-only": "GPL-3.0",
            "GPL-3.0-or-later": "GPL-3.0", "LGPL-2.0-or-later": "LGPL-2.0", "LGPL-2.1-only": "LGPL-2.1",
            "LGPL-2.1-or-later": "LGPL-2.1", "LGPL-3.0-only": "LGPL-3.0", "LGPL-3.0-or-later": "LGPL-3.0"}


def fetch(spdx: str) -> bytes:
    with urllib.request.urlopen(BASE.format(spdx), timeout=30) as r:
        return r.read()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = []
    for wid, spdx in MAP.items():
        for candidate in (spdx, FALLBACK.get(wid)):
            if not candidate:
                continue
            try:
                data = fetch(candidate)
            except OSError as e:
                print(f"{wid}: {candidate} failed ({e})", file=sys.stderr)
                continue
            (OUT / f"{wid}.txt").write_bytes(data)
            print(f"{wid}: {candidate} ({len(data)} bytes)")
            break
        else:
            missing.append(wid)
    if missing:
        print("MISSING:", ", ".join(missing), file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
