"""License ids, which of them may be republished in git, and their README labels."""
from __future__ import annotations

# SPDX ids for Creative Commons etc.; all-rights-reserved names the holder in license_detail
LICENSES: frozenset[str] = frozenset({
    "public-domain",
    "CC0-1.0",
    "CC-BY-1.0",
    "CC-BY-2.0",
    "CC-BY-2.5",
    "CC-BY-3.0",
    "CC-BY-4.0",
    "CC-BY-SA-1.0",
    "CC-BY-SA-2.0",
    "CC-BY-SA-2.5",
    "CC-BY-SA-3.0",
    "CC-BY-SA-3.0-IGO",
    "CC-BY-SA-4.0",
    "ghibli-free-use",
    "official-wallpaper",
    "all-rights-reserved",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "Apache-2.0",
    "BSD-3-Clause",
    "MIT",
    "LGPL-2.0-or-later",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "unsplash-license",
    "personal",
})

# Licenses that allow republishing the file in the git repo; everything else is downloaded.
SHAREABLE: frozenset[str] = frozenset(
    l for l in LICENSES if l == "public-domain" or l.startswith(("CC", "GPL", "LGPL", "Apache", "BSD", "MIT"))
)

LABELS: dict[str, str] = {
    "public-domain": "Public domain",
    "CC0-1.0": "CC0 1.0: no rights reserved",
    "CC-BY-2.0": "CC BY 2.0: credit the author if you republish",
    "CC-BY-2.5": "CC BY 2.5: credit the author if you republish",
    "CC-BY-3.0": "CC BY 3.0: credit the author if you republish",
    "CC-BY-4.0": "CC BY 4.0: credit the author if you republish",
    "CC-BY-SA-2.0": "CC BY-SA 2.0: credit and share alike if you republish",
    "CC-BY-SA-3.0": "CC BY-SA 3.0: credit and share alike if you republish",
    "CC-BY-SA-3.0-IGO": "CC BY-SA 3.0 IGO: credit and share alike if you republish",
    "CC-BY-SA-4.0": "CC BY-SA 4.0: credit and share alike if you republish",
    "ghibli-free-use": "© Studio Ghibli free stills: use within common sense",
    "official-wallpaper": "© Disney / Pixar official wallpaper: free for personal use",
    "all-rights-reserved": "© studio, all rights reserved (holder in each entry): personal desktop use only",
    "GPL-2.0-only": "GPL 2.0: free to share and modify, same license",
    "GPL-2.0-or-later": "GPL 2.0 or later: free to share and modify, same license",
    "GPL-3.0-only": "GPL 3.0: free to share and modify, same license",
    "GPL-3.0-or-later": "GPL 3.0 or later: free to share and modify, same license",
    "Apache-2.0": "Apache 2.0: free to share and modify, keep the notice",
    "BSD-3-Clause": "BSD 3-Clause: free to share and modify, keep the notice",
    "MIT": "MIT: free to share and modify, keep the notice",
    "LGPL-2.0-or-later": "LGPL 2.0 or later: free to share and modify",
    "LGPL-2.1-only": "LGPL 2.1: free to share and modify",
    "LGPL-2.1-or-later": "LGPL 2.1 or later: free to share and modify",
    "LGPL-3.0-only": "LGPL 3.0: free to share and modify",
    "LGPL-3.0-or-later": "LGPL 3.0 or later: free to share and modify",
    "unsplash-license": "Unsplash License: free to use, not to be sold unaltered or used for a competing service",
    "personal": "Your own files",
    "CC-BY-1.0": "CC BY 1.0: credit the author if you republish",
    "CC-BY-SA-1.0": "CC BY-SA 1.0: credit and share alike if you republish",
    "CC-BY-SA-2.5": "CC BY-SA 2.5: credit and share alike if you republish",
}


def is_shareable(license: str) -> bool:
    return license in SHAREABLE
