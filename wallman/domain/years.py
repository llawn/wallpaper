"""Year spans: a wallpaper's date is one year or an uncertain range. Pure, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MIN_YEAR, MAX_YEAR = 1400, 2100
CIRCA = 5  # "c. 1880" means 1875-1885
_THIS_YEAR = date.today().year


@dataclass(frozen=True)
class YearSpan:
    start: int
    end: int

    @property
    def label(self) -> str:
        return str(self.start) if self.start == self.end else f"{self.start}–{self.end}"

    @property
    def is_valid(self) -> bool:
        return MIN_YEAR <= self.start <= self.end <= MAX_YEAR

    def overlaps(self, other: "YearSpan") -> bool:
        return self.start <= other.end and other.start <= self.end


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def year_from_toml(value: object) -> YearSpan | None:
    """int -> single year, [start, end] -> span; anything else (or None) -> None. Range validity is the rules' job."""
    if _is_int(value):
        return YearSpan(value, value)
    if isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value):
        return YearSpan(value[0], value[1])
    return None


def year_to_toml(span: YearSpan) -> int | list[int]:
    return span.start if span.start == span.end else [span.start, span.end]


def _checked(start: int, end: int) -> YearSpan | None:
    span = YearSpan(start, end)
    return span if span.is_valid else None


_YEAR = re.compile(r"\d{4}")
_DECADE = re.compile(r"(\d{3})x")
_CENTURY_X = re.compile(r"(\d{2})xx")
_S = re.compile(r"(\d{3})0s")
_NTH = re.compile(r"(\d{1,2})(?:st|nd|rd|th) century")
_RANGE = re.compile(r"(\d{4})\s*[-–—]\s*(\d{4})")
_CIRCA = re.compile(r"(?:circa|ca\.?|c\.?)\s*(\d{4})")


def parse_year(text: str) -> YearSpan | None:
    """Parse what a person types into a span inside 1400-2100, or None.

    1889; 188x (decade); 18xx (century); 1880s (decade); 1800s (a round hundred means the whole century, so
    1800s = 1800-1899 and 1900s = 1900-1999); "19th century" = 1800-1899 (the common convention: the n-th century
    is years (n-1)*100 .. n*100-1); 1880-1895 (hyphen, en or em dash); c. 1880 / ca 1880 / circa 1880 = +-5 years,
    clamped to the valid range. A reversed range or an out-of-range result is None.
    """
    t = text.strip().lower()
    if _YEAR.fullmatch(t):
        return _checked(int(t), int(t))
    if m := _DECADE.fullmatch(t):
        d = int(m[1]) * 10
        return _checked(d, d + 9)
    if m := _CENTURY_X.fullmatch(t):
        c = int(m[1]) * 100
        return _checked(c, c + 99)
    if m := _S.fullmatch(t):
        d = int(m[1]) * 10
        return _checked(d, d + (99 if d % 100 == 0 else 9))
    if m := _NTH.fullmatch(t):
        n = int(m[1])
        return _checked((n - 1) * 100, n * 100 - 1) if n >= 1 else None
    if m := _RANGE.fullmatch(t):
        return _checked(int(m[1]), int(m[2]))
    if m := _CIRCA.fullmatch(t):
        y = int(m[1])
        if not MIN_YEAR <= y <= MAX_YEAR:
            return None
        return _checked(max(MIN_YEAR, y - CIRCA), min(MAX_YEAR, y + CIRCA))
    return None


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _century(n: int) -> str:
    return f"{_ordinal(n)} century"


def _decade(d: int) -> str:
    """Label that parse_year reads back as the decade itself (a round hundred would read as a century)."""
    return f"{d}s" if d % 100 else f"{d // 10}x"


def _in_range(lo: int, hi: int) -> bool:
    return lo <= MAX_YEAR and hi >= MIN_YEAR


def suggest_years(prefix: str, limit: int = 8) -> list[str]:
    """Menu entries for the digits typed so far; every entry parses with parse_year (others are filtered out).

    1-2 digits ("20"): decades up to the current year ("2000s" is the whole century 2000-2099, as in parse_year),
    then the centuries whose number starts with the prefix ("20th century") and the one holding those years
    ("21st century"). 3 digits ("188"): "1880s", then 1880, 1881, ... 4 digits: the year, its decade, its century.
    """
    p = prefix.strip()
    if not p.isdigit() or len(p) > 4:
        return []
    out: list[str] = []
    if len(p) == 4:
        y = int(p)
        if not MIN_YEAR <= y <= MAX_YEAR:
            return []
        out = [p, _decade(y // 10 * 10), _century(y // 100 + 1)]
    elif len(p) == 3:
        d = int(p) * 10
        if not _in_range(d, d + 9):
            return []
        out = [f"{d}s"]  # for 1800-1809 that reads as the whole century, which contains them
        out += [str(y) for y in range(d, d + 10) if MIN_YEAR <= y <= MAX_YEAR]
    else:
        lo = int(p) * 10 ** (4 - len(p))
        hi = lo + 10 ** (4 - len(p)) - 1
        for d in range(lo, hi + 1, 10):
            if MIN_YEAR <= d <= min(MAX_YEAR, _THIS_YEAR):
                out.append(f"{d}s" if d % 100 == 0 else _decade(d))
        centuries = {n for n in range(1, 22) if str(n).startswith(p) and _in_range((n - 1) * 100, n * 100 - 1)}
        centuries |= {n for n in {lo // 100 + 1, hi // 100 + 1} if _in_range((n - 1) * 100, n * 100 - 1)}
        out += [_century(n) for n in sorted(centuries)]
    return [s for s in out if parse_year(s) is not None][:limit]
