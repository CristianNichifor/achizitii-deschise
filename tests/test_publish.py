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
        (int(y), c): (None if v == "NULL" else float(v))
        for y, c, v, _max in re.findall(
            r"\((\d{4}), '(\w+)', (NULL|[\d.]+), ([\d.]+)\)", sql
        )
    }
    # Works before 2022 have no ceiling OF THEIR OWN. The row still exists, carrying a
    # NULL, so the fallback bound can be attached to it — see
    # test_unscreened_years_are_still_bounded.
    assert entries[(2019, "lucrari")] is None
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
            expected = "NULL" if declared is None else str(declared)
            assert f"({year}, '{category}', {expected}," in sql, (
                f"{category} {year}: schedule says {declared}, which is not what the "
                "bundle emits"
            )


def test_unscreened_rows_are_kept_not_dropped() -> None:
    """A NULL ceiling must not silently delete a year.

    `p.prag IS NULL OR ...` is the whole reason the join is a LEFT JOIN. An inner join,
    or dropping the NULL branch, would erase every pre-2022 works acquisition instead of
    treating it as unscreened.
    """
    assert "LEFT JOIN praguri" in BASE_VIEW
    # A year with no ceiling of its own falls back to the category's highest-ever
    # ceiling rather than being screened against nothing.
    assert "COALESCE(p.prag, p.prag_max)" in BASE_VIEW


@pytest.mark.parametrize("dataset", DATASETS, ids=lambda d: d.name)
def test_every_aggregate_publishes_its_denominator(dataset) -> None:
    """METHODOLOGY.md principle 2: no number without its denominator."""
    assert re.search(r"\bAS n\b|count\(\*\)\s+AS\s+n\b", dataset.sql), (
        f"{dataset.name} publishes aggregates without an n column"
    )


@pytest.mark.parametrize(
    "dataset",
    [d for d in DATASETS if "FROM ad" in d.sql],
    ids=lambda d: d.name,
)
def test_monetary_aggregates_only_use_plausible_values(dataset) -> None:
    """Sums and medians over direct acquisitions must exclude values above the ceiling.

    Scoped to the datasets built on the `ad` view. Contracts are a different case with
    no `plauzibil` flag to filter on: a public contract has no legal ceiling — that is
    what distinguishes it from a direct acquisition — so nothing there can be called
    impossible on legal grounds. Extreme contract values are disclosed alongside the
    total instead of excluded; see test_framework_ceilings_are_never_summed and
    EXTREME_CONTRACT.
    """
    for match in re.finditer(r"(sum|median|quantile_cont)\([^)]*\)", dataset.sql):
        tail = dataset.sql[match.end() : match.end() + 40]
        assert "FILTER (WHERE plauzibil)" in tail, (
            f"{dataset.name}: {match.group(0)} is not filtered to plausible values"
        )


def test_contract_extremes_are_disclosed_not_hidden() -> None:
    """No legal ceiling exists for contracts, so the tail is reported, not removed.

    301 rows (0.076%) carry 40% of the ordinary-contract total. A reader given only a
    sum would effectively be reading those rows, so the count and an
    extremes-excluded total are published beside it.
    """
    from achizitii.publish import DATASETS

    contracts = next(d for d in DATASETS if d.name == "contracte_an")
    assert "n_peste_prag_extrem" in contracts.sql
    assert "valoare_fara_extreme_ron" in contracts.sql


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


def test_unscreened_years_are_still_bounded() -> None:
    """The 543-billion-lei school asphalting job.

    PR #26 correctly stopped screening works against the goods ceiling — works have
    their own, far higher figure. But before 2022 the works ceiling was never
    established, so those years became screened against *nothing*, and a 2016 record of
    543,595,445,218 RON entered the published total. It was 98% of that year's works
    spending, against a real figure of about 1.2 billion.

    That was a false-exclusion bug traded for a false-inclusion one. A value above the
    most permissive ceiling the law has EVER set cannot be a lawful direct acquisition
    in any year — which bounds the unscreened years without claiming to know what their
    ceiling was.
    """
    from achizitii.publish import BASE_VIEW, ceilings_by_category_sql, max_ceiling_for

    assert max_ceiling_for("goods_services") == 270_120.0
    assert max_ceiling_for("works") == 900_400.0

    # Every year and category gets a row, and every row carries a fallback bound even
    # where the year's own ceiling is NULL.
    sql = ceilings_by_category_sql()
    entries = re.findall(r"\((\d{4}), '(\w+)', (NULL|[\d.]+), ([\d.]+)\)", sql)
    assert len(entries) == 33, f"expected 11 years x 3 categories, got {len(entries)}"
    unscreened = [e for e in entries if e[2] == "NULL"]
    assert unscreened, "pre-2022 works should have no ceiling of their own"
    for _year, _cat, _prag, prag_max in unscreened:
        assert float(prag_max) > 0, "an unscreened year must still carry a bound"

    # And the view must actually use it.
    assert "COALESCE(p.prag, p.prag_max)" in BASE_VIEW


@pytest.mark.skipif(
    not Path("site/data/sumar_an.parquet").is_file(), reason="bundle not built"
)
def test_published_totals_are_physically_plausible() -> None:
    """A guard against another trillion-lei record reaching a headline figure.

    Romania's entire direct-acquisition spending runs to a few billion lei per category
    per year. Anything an order of magnitude beyond that is a data error, not a finding.
    """
    import duckdb

    con = duckdb.connect()
    worst = con.execute(
        "SELECT an, categorie, valoare_totala_ron FROM 'site/data/sumar_an.parquet' "
        "ORDER BY valoare_totala_ron DESC NULLS LAST LIMIT 1"
    ).fetchone()
    con.close()
    assert worst[2] < 50_000_000_000, (
        f"{worst[1]} {worst[0]} totals {worst[2]:,.0f} RON — implausible for one year "
        "and one category; an impossible value has reached a published sum"
    )


def test_framework_ceilings_are_never_summed() -> None:
    """A framework publishes a ceiling, repeated on every supplier's row.

    The four largest rows in the archive are the same 177,930,419,250 RON — one
    authority, four pharmaceutical wholesalers, one framework. Summing them multiplies a
    single ceiling by the number of suppliers, and then double-counts again against the
    call-offs placed under it. So frameworks carry a count and a median and no total.
    """
    from achizitii.publish import DATASETS

    contracts = next(d for d in DATASETS if d.name == "contracte_an")
    assert "natura <> 'plafon_acord_cadru'" in contracts.sql, (
        "the sum must be suppressed for framework ceilings"
    )
    # The nature split has to consider call-offs BEFORE frameworks, or every call-off
    # under a framework would be classified as a ceiling.
    from achizitii.publish import CONTRACT_NATURE_SQL

    assert CONTRACT_NATURE_SQL.index("subsecvent") < CONTRACT_NATURE_SQL.index("acord-cadru")


@pytest.mark.skipif(
    not Path("site/data/contracte_an.parquet").is_file(), reason="bundle not built"
)
def test_published_contracts_suppress_framework_totals() -> None:
    import duckdb

    con = duckdb.connect()
    rows = con.execute(
        """SELECT natura, valoare_totala_ron, n FROM 'site/data/contracte_an.parquet'"""
    ).fetchall()
    con.close()
    assert rows, "no contract rows published"
    for natura, total, n in rows:
        assert n > 0
        if natura == "plafon_acord_cadru":
            assert total is None, "a framework ceiling was published as a total"
        else:
            assert total is not None
