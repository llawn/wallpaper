"""Aspect-ratio buckets for the format filter: lower bound inclusive, upper bound exclusive."""
from __future__ import annotations

from .models import Resolution

# (lower bound in hundredths, name), widest first
_BUCKETS = ((340, "32:9"), (220, "21:9"), (170, "16:9"), (155, "16:10"), (145, "3:2"), (130, "4:3"),
            (120, "5:4"), (95, "square"))
BUCKETS = tuple(name for _, name in _BUCKETS) + ("portrait",)


def ratio_bucket(r: Resolution) -> str:
    """Integer arithmetic (width*100 >= bound*height) so the bounds are exact, never float-rounded."""
    for bound, name in _BUCKETS:
        if r.width * 100 >= bound * r.height:
            return name
    return "portrait"
