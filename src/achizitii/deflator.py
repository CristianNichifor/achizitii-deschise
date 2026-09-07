"""Comparing money across years.

Romanian prices rose 62% between 2016 and 2025. Every figure this project publishes is
nominal, so a reader comparing 2016 against 2024 sees inflation as much as procurement —
and the caveat in METHODOLOGY.md does not help anyone reading a table.

Two decisions shape this module.

**The deflator is published, not applied.** Rather than adding `*_real` columns to every
dataset, the index ships as its own small table and the browser applies it. Published
figures stay exactly as published, the reader chooses the base year, and the adjustment
is visible rather than baked in — METHODOLOGY.md's "everything traces back". It also
means a revised index does not invalidate every stored number.

**A missing year is a refusal, not a default.** Eurostat has not published 2026, so 2026
cannot be deflated. `index_for` returns None and `to_real` refuses, exactly as
`threshold_for` refuses a year with no established ceiling. Extrapolating from 2025, or
silently falling back to nominal, would both produce a number nobody could check.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from .config import ROOT


@functools.lru_cache(maxsize=1)
def _spec() -> dict[str, Any]:
    path = Path(ROOT) / "data" / "ipc.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@functools.lru_cache(maxsize=1)
def indices() -> dict[int, float]:
    """Year -> annual average index, 2015 = 100."""
    return {int(y): float(v) for y, v in (_spec().get("indici") or {}).items()}


def source() -> dict[str, Any]:
    """Provenance, published alongside the numbers so a reader can check them."""
    return dict(_spec().get("sursa") or {})


def index_for(year: int) -> float | None:
    """The index for a year, or None when it is not published.

    None means "cannot be deflated" and callers must skip rather than substitute. The
    archive covers 2026; Eurostat does not, and pretending otherwise would invent data.
    """
    return indices().get(int(year))


def to_real(value: float | None, year: int, base_year: int) -> float | None:
    """Convert a nominal amount in `year` into `base_year` money.

    Returns None when either year lacks an index, when the value is missing, or when the
    base index is zero — never a partially-adjusted figure.

        >>> to_real(100.0, 2016, 2025)   # 2016 money is worth more
        161.79
    """
    if value is None:
        return None
    here, base = index_for(year), index_for(base_year)
    if here is None or base is None or here == 0:
        return None
    return round(value * base / here, 2)


def deflator_rows(base_year: int | None = None) -> list[dict[str, Any]]:
    """The published table: one row per year with an index.

    `factor` is what a figure from that year is multiplied by to express it in
    `base_year` money — precomputed so the browser does not have to, and so the
    arithmetic is inspectable in the data itself rather than hidden in JavaScript.
    """
    known = indices()
    if not known:
        raise RuntimeError("data/ipc.yml carries no indices; refusing to publish")
    base = int(base_year or max(known))
    if base not in known:
        raise ValueError(f"no index for base year {base}")

    meta = source()
    return [
        {
            "an": year,
            "indice": index,
            "an_baza": base,
            "factor": round(known[base] / index, 6),
            "baza": str(meta.get("baza") or ""),
            "sursa": str(meta.get("nume") or ""),
        }
        for year, index in sorted(known.items())
    ]
