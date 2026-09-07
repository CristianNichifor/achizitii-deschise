"""The published bundle must not misstate what it contains.

Everything here guards a mistake that was actually made while writing `publish.py`, or
one the site depends on not happening.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from achizitii.indicators import threshold_for
from achizitii.publish import (
    BASE_VIEW,
    DATASETS,
    MIN_GROUP_FOR_MEDIAN,
    YEARS,
    ceilings_by_category_sql,
)


def test_ceilings_are_per_category_not_per_year_alone() -> None:
    """The bug this catches excluded 52,239 lawful works acquisitions.

    Applying the goods ceiling (135,060 before 2022) to `lucrari` marked 12% of 2019's
    works as impossible. Works have their own, far higher figure and, before 2022, none
    at all.
    """
    sql = ceilings_by_category_sql()
    entries = {
        (int(y), c): float(v)
        for y, c, v in re.findall(r"\((\d{4}), '(\w+)', ([\d.]+)\)", sql)
    }
    # Works before 2022: no established ceiling, so no entry — absence means unscreened.
    assert not any(cat == "lucrari" and yr < 2022 for yr, cat in entries)
    # From 2022 works are screened, and far above goods.
    assert entries[(2022, "lucrari")] == 900_400.0
    assert entries[(2022, "furnizare")] == 270_120.0
    assert entries[(2022, "lucrari")] > entries[(2022, "furnizare")]


def test_every_year_and_category_matches_the_declared_schedule() -> None:
    sql = ceilings_by_category_sql()
    for category, key in (
        ("furnizare", "goods_services"),
        ("servicii", "goods_services"),
        ("lucrari", "works"),
    ):
        for year in YEARS:
            declared = threshold_for(date(year, 7, 1), key)
            present = f"({year}, '{category}', {declared})" in sql
            assert present is (declared is not None), (
                f"{category} {year}: schedule says {declared}, bundle "
                f"{'has' if present else 'lacks'} an entry"
            )


def test_unscreened_rows_are_kept_not_dropped() -> None:
    """A NULL ceiling must not silently delete a year.

    `p.prag IS NULL OR ...` is the whole reason the join is a LEFT JOIN. An inner join,
    or dropping the NULL branch, would erase every pre-2022 works acquisition instead of
    treating it as unscreened.
    """
    assert "LEFT JOIN praguri" in BASE_VIEW
    assert "p.prag IS NULL OR" in BASE_VIEW


@pytest.mark.parametrize("dataset", DATASETS, ids=lambda d: d.name)
def test_every_aggregate_publishes_its_denominator(dataset) -> None:
    """METHODOLOGY.md principle 2: no number without its denominator."""
    assert re.search(r"\bAS n\b|count\(\*\)\s+AS\s+n\b", dataset.sql), (
        f"{dataset.name} publishes aggregates without an n column"
    )


@pytest.mark.parametrize("dataset", DATASETS, ids=lambda d: d.name)
def test_monetary_aggregates_only_use_plausible_values(dataset) -> None:
    """Sums and medians must never include values above the legal ceiling."""
    for match in re.finditer(r"(sum|median|quantile_cont)\([^)]*\)", dataset.sql):
        tail = dataset.sql[match.end() : match.end() + 40]
        assert "FILTER (WHERE plauzibil)" in tail, (
            f"{dataset.name}: {match.group(0)} is not filtered to plausible values"
        )


def test_percentiles_are_suppressed_on_small_groups() -> None:
    """A median over four rows describes those four rows, not a market."""
    cpv = next(d for d in DATASETS if d.name == "cpv_an")
    for stat in ("median(", "quantile_cont("):
        assert f">= {MIN_GROUP_FOR_MEDIAN}" in cpv.sql, "missing group-size guard"
        assert stat in cpv.sql


@pytest.mark.skipif(
    not Path("site/data/manifest.json").is_file(), reason="bundle not built"
)
class TestBuiltBundle:
    """Checks against the committed bundle, which is what Pages actually serves."""

    @staticmethod
    def manifest() -> dict:
        return json.loads(Path("site/data/manifest.json").read_text())

    def test_every_listed_file_exists(self) -> None:
        m = self.manifest()
        for entry in m["datasets"] + m["indicatori"]:
            assert Path("site/data", entry["file"]).is_file(), f"missing {entry['file']}"

    def test_indicator_columns_are_recorded(self) -> None:
        """The site offers a year filter only where `an` exists.

        Four of the seven findings tables have no year column; without this the site
        emits SQL that fails to bind.
        """
        for entry in self.manifest()["indicatori"]:
            assert entry.get("columns"), f"{entry['id']} records no columns"

    def test_bundle_fits_github_pages(self) -> None:
        total = sum(p.stat().st_size for p in Path("site").rglob("*") if p.is_file())
        assert total < 900_000_000, f"site is {total / 1e6:.0f} MB, near the 1 GB limit"
