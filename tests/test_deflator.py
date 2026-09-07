"""Comparing money across years, and refusing to when we cannot.

Romanian prices rose 62% between 2016 and 2025, so a reader comparing those years
without adjustment is being misled by arithmetic rather than by anything in the
procurement data.

The rule that matters most here is the refusal: Eurostat has not published 2026, so 2026
cannot be deflated and must not be quietly left nominal in a column of adjusted figures.
"""

from __future__ import annotations

import pytest

from achizitii.deflator import (
    deflator_rows,
    index_for,
    indices,
    source,
    to_real,
)


def test_the_archive_years_have_an_index() -> None:
    known = indices()
    assert set(range(2016, 2026)) <= set(known), "a year the archive covers lost its index"
    assert known[2016] == 98.93
    assert known[2025] == 160.06


def test_2026_has_no_index_and_is_not_invented() -> None:
    """Eurostat's series stops at 2025; the monthly series does too.

    Extrapolating from 2025, or silently falling back to nominal, would both produce a
    figure nobody could check. This mirrors `threshold_for`, which refuses a year with
    no established ceiling rather than substituting a default.
    """
    assert index_for(2026) is None
    assert to_real(100.0, 2026, 2025) is None
    assert to_real(100.0, 2016, 2026) is None


def test_deflation_direction_and_round_trip() -> None:
    """2016 money is worth MORE in 2025 terms, not less."""
    assert to_real(100.0, 2016, 2025) == 161.79
    assert to_real(100.0, 2025, 2016) == 61.81
    # Round-tripping should return roughly the original.
    there = to_real(1000.0, 2016, 2025)
    assert abs(to_real(there, 2025, 2016) - 1000.0) < 0.05


def test_same_year_is_identity() -> None:
    assert to_real(1234.56, 2020, 2020) == 1234.56


def test_missing_value_is_not_zero() -> None:
    """A missing amount must stay missing, not become 0 after adjustment."""
    assert to_real(None, 2016, 2025) is None


def test_published_rows_carry_their_provenance() -> None:
    rows = deflator_rows(2025)
    assert len(rows) == len(indices())
    assert {r["an_baza"] for r in rows} == {2025}
    base = next(r for r in rows if r["an"] == 2025)
    assert base["factor"] == 1.0, "the base year must be its own reference"
    assert all(r["sursa"] and r["baza"] for r in rows), "provenance is not optional"


def test_factors_move_the_right_way() -> None:
    rows = {r["an"]: r["factor"] for r in deflator_rows(2025)}
    assert rows[2016] > rows[2020] > rows[2025], (
        "an older year needs a larger multiplier to reach base-year money"
    )


def test_unknown_base_year_is_rejected() -> None:
    with pytest.raises(ValueError, match="2026"):
        deflator_rows(2026)


def test_source_is_recorded() -> None:
    """A published index with no stated origin cannot be checked by anyone."""
    meta = source()
    for field in ("nume", "url", "baza", "publicat", "preluat"):
        assert meta.get(field), f"data/ipc.yml is missing sursa.{field}"
    assert "eurostat" in str(meta["url"]).lower()
