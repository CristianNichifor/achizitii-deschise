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


def test_county_view_requires_both_sides() -> None:
    """A county comparison needs the buyer's county as well as the supplier's.

    Neither is in the exports: supplier locality is missing on 77% of rows and the
    buyer's county is never given. Both come from the ANAF profile cache, so the join
    must be on two sides — a single-sided join would silently compare a supplier county
    against nothing.
    """
    from achizitii.publish import GEO_VIEW

    assert GEO_VIEW.count("JOIN firme_geo") == 2
    assert "judet_autoritate" in GEO_VIEW and "judet_furnizor" in GEO_VIEW


def test_county_dataset_is_descriptive_not_an_indicator() -> None:
    """Buying locally is lawful, and the published table must not imply otherwise.

    It carries counts and values, no threshold and no flag. If this ever grows a column
    that scores or ranks counties, it needs a legal basis first — like every indicator.
    """
    from achizitii.publish import DATASETS

    judete = next(d for d in DATASETS if d.name == "judete_an")
    lowered = judete.sql.lower()
    for word in ("suspect", "risc", "alert", "incalcare", "flag"):
        assert word not in lowered, f"{word!r} implies a judgement this data cannot support"
    assert "pct_local" in judete.sql and "n_local" in judete.sql


def _ad(con, rows: list[tuple]) -> None:
    """Register a minimal `achizitii_directe` and the `ad` view over it."""
    con.execute("""
        CREATE TABLE achizitii_directe (
            an SMALLINT, cpv VARCHAR, cpv_denumire VARCHAR, categorie VARCHAR,
            autoritate VARCHAR, autoritate_cui VARCHAR,
            furnizor VARCHAR, furnizor_cui VARCHAR, valoare_ron VARCHAR)
    """)
    con.executemany(
        "INSERT INTO achizitii_directe VALUES (?,?,?,?,?,?,?,?,?)", rows
    )
    con.execute(BASE_VIEW.format(
        thresholds=ceilings_by_category_sql(),
        numeric_label=r"^\\d+$",
    ))


def test_one_organisation_is_not_two_spellings_of_its_fiscal_code() -> None:
    """The exports carry the same code both ways: "1590120" and "RO1590120".

    Grouped by the raw value, Romsilva became two institutions with 22,271 and 11,969
    acquisitions, and the site's entity file — which matches a CUI exactly, so that
    4340536 does not pull in 14340536 — showed whichever half the reader happened to
    search for. Measured across the published archive before this fix: 389 institutions
    split in two, 800,135 acquisitions and 4.44 billion RON on the wrong side of a prefix.

    This module already knew. The county join normalised exactly this way to match ANAF,
    two hundred lines after the aggregates grouped on the raw column.
    """
    import duckdb

    con = duckdb.connect()
    _ad(con, [
        (2024, "03222111-4", "Banane", "furnizare",
         "PRIMARIA X", "RO1590120", "F SRL", "RO2816464", "100"),
        (2024, "03222111-4", "Banane", "furnizare",
         "PRIMARIA X", "1590120", "F SRL", "2816464", "150"),
        (2024, "03222111-4", "Banane", "furnizare",
         "PRIMARIA X", " 1590120 ", "F SRL", "0002816464", "200"),
    ])
    aut = next(d for d in DATASETS if d.name == "autoritati_an")
    rows = con.execute(aut.sql).fetchall()
    assert len(rows) == 1, f"one authority, one row — got {rows}"
    assert rows[0][1] == "1590120", "the normalised spelling is the one published"
    assert rows[0][3] == 3, "and it keeps every acquisition"

    furn = next(d for d in DATASETS if d.name == "furnizori_an")
    frows = con.execute(furn.sql).fetchall()
    assert len(frows) == 1, f"one supplier, one row — got {frows}"
    assert frows[0][1] == "2816464", "leading zeros are spelling, not identity"
    con.close()


def test_normalisation_is_about_spelling_not_validity() -> None:
    """`firme.normalise_cui` also REJECTS codes outside 2-10 digits. That is a judgement
    about validity, and applying it in the base view would silently drop rows from every
    count on the site under the guise of deduplication. A short code stays a short code;
    it just stops being two of them.
    """
    import duckdb

    con = duckdb.connect()
    _ad(con, [
        (2024, "03222111-4", "Banane", "furnizare", "MICA", "7", "F", "9", "100"),
        (2024, "03222111-4", "Banane", "furnizare", "FARA", "", "F", "9", "100"),
    ])
    rows = con.execute(
        "SELECT autoritate_cui, count(*) FROM ad GROUP BY 1 ORDER BY 1 NULLS LAST"
    ).fetchall()
    assert ("7", 1) in rows, "a one-digit code is kept, not dropped"
    assert (None, 1) in rows, "an empty code becomes NULL rather than an empty string"
    con.close()


def test_the_county_join_no_longer_re_normalises() -> None:
    """It normalised to match ANAF while the aggregates grouped on the raw column — the
    same module both knowing and not knowing. `ad` does it once now."""
    source = Path("src/achizitii/publish.py").read_text(encoding="utf-8")
    assert "JOIN firme_geo fa ON fa.cui = ad.autoritate_cui" in source
    assert "JOIN firme_geo ff ON ff.cui = ad.furnizor_cui" in source
    # Exactly twice: once for each column, in the base view and nowhere else.
    assert source.count("regexp_replace(a.autoritate_cui") == 1
    assert source.count("regexp_replace(a.furnizor_cui") == 1


def test_the_principal_cpv_is_chosen_deterministically() -> None:
    """`mode(cpv)` returned whichever tied code the scan reached first.

    Measured on the published archive — the same query, the same data, the same process,
    three times — 26 of the 472 multi-code groups came back different on every run. Every
    republish therefore produced a diff of rows nobody had changed, which is noise in a
    repository whose claim is that the figures regenerate from the data rather than being
    written by hand, and it hides the changes that are real: this fix's own data diff was
    92 such rows before it landed.

    Most frequent code, ties broken by the lowest. Where a group has a clear winner this
    is exactly what mode() returned.
    """
    import duckdb

    from achizitii.publish import UNIT_PRICE_DATASETS

    produs = next(d for d in UNIT_PRICE_DATASETS if d.name == "preturi_produs")
    # SQL comments stripped first. The comment above the replacement names what it
    # replaced, so a bare substring search finds "mode(cpv)" in the explanation of why
    # mode(cpv) is gone. That has now happened four times in this suite.
    code = "\n".join(
        line for line in produs.sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert "mode(cpv)" not in code, "mode() is not deterministic on ties"
    assert "ORDER BY k DESC, cpv ASC" in code

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE preturi AS SELECT * FROM (VALUES
            -- one group, two codes, one occurrence each: a tie, and the whole point
            ('creion', 'buc', NULL, '30192125-3', 2.0, TRUE, 'A'),
            ('creion', 'buc', NULL, '30192121-5', 3.0, TRUE, 'B'),
            -- one group with a clear winner, which must not move
            ('hartie', 'top', NULL, '30197644-2', 20.0, TRUE, 'A'),
            ('hartie', 'top', NULL, '30197644-2', 22.0, TRUE, 'B'),
            ('hartie', 'top', NULL, '30197630-1', 25.0, TRUE, 'C')
        ) t(denumire_key, um, marime_pachet, cpv, pret_unitar_ron, comparabil, autoritate_cui)
    """)
    got = {r[0]: r[1] for r in con.execute(
        f"SELECT denumire_key, cpv_principal FROM ({produs.sql})").fetchall()}
    assert got["creion"] == "30192121-5", "a tie resolves to the lowest code, every time"
    assert got["hartie"] == "30197644-2", "a clear winner is still the winner"
    con.close()


def test_a_group_without_a_unit_keeps_its_principal_cpv() -> None:
    """The join onto the per-code ranking matches on unit and pack size, both of which
    are legitimately NULL — "no unit of measure" is a real group. With `=` instead of
    IS NOT DISTINCT FROM, every one of those groups would lose its code."""
    import duckdb

    from achizitii.publish import UNIT_PRICE_DATASETS

    produs = next(d for d in UNIT_PRICE_DATASETS if d.name == "preturi_produs")
    assert "IS NOT DISTINCT FROM p.um" in produs.sql
    assert "IS NOT DISTINCT FROM p.marime_pachet" in produs.sql

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE preturi AS SELECT * FROM (VALUES
            ('servicii', NULL, NULL, '79000000-4', 100.0, TRUE, 'A'),
            ('servicii', NULL, NULL, '79000000-4', 120.0, TRUE, 'B')
        ) t(denumire_key, um, marime_pachet, cpv, pret_unitar_ron, comparabil, autoritate_cui)
    """)
    rows = con.execute(f"SELECT cpv_principal FROM ({produs.sql})").fetchall()
    assert rows == [("79000000-4",)], f"a unit-less group lost its code: {rows}"
    con.close()


def test_the_front_door_files_match_the_query_they_replace() -> None:
    """The front door's whole claim is that every figure on it is the same figure as the
    tab it links to. A precomputed summary is a second source of truth, and this is what
    stops it becoming a divergent one.

    They read the PUBLISHED Parquet rather than the underlying view — which matters more
    than it looks. `autoritati_an.valoare_totala_ron` is already rounded per year, so
    summing those rounded figures is not the same number as rounding a sum of the raw
    values. Aggregating `ad` directly here would produce a front door that disagreed with
    its own table by a few lei, for no visible reason.
    """
    import duckdb

    out = Path("site/data")
    for name in ("autoritati_an.parquet", "preturi_unitare.parquet"):
        if not (out / name).is_file():
            pytest.skip(f"{name} not published in this checkout")

    con = duckdb.connect()
    con.execute(
        "CREATE VIEW pub_autoritati_an AS SELECT * FROM "
        f"read_parquet('{out / 'autoritati_an.parquet'}')"
    )
    con.execute(
        "CREATE VIEW pub_preturi_unitare AS SELECT * FROM "
        f"read_parquet('{out / 'preturi_unitare.parquet'}')"
    )
    from achizitii.publish import PANORAMA_DATASETS

    for d in PANORAMA_DATASETS:
        target = out / f"{d.name}.parquet"
        if not target.is_file():
            pytest.skip(f"{d.name} not published in this checkout")
        live = con.execute(d.sql).fetchall()
        stored = con.execute(f"SELECT * FROM read_parquet('{target}')").fetchall()
        assert stored == live, (
            f"{d.name} on disk differs from the query it stands in for — the front door "
            "would show different numbers from the tab it links to"
        )
    con.close()


def test_the_front_door_files_are_small_enough_to_be_worth_it() -> None:
    """The point of them. Rendering the front door pulled 3.32 MB of autoritati_an and
    0.48 MB of preturi_unitare — essentially both files in full, to show six rows each,
    because `GROUP BY` over every row cannot be served by a range request."""
    out = Path("site/data")
    for name in ("panorama_cumparatori", "panorama_preturi"):
        f = out / f"{name}.parquet"
        if not f.is_file():
            pytest.skip(f"{name} not published in this checkout")
        assert f.stat().st_size < 20_000, (
            f"{name} is {f.stat().st_size:,} bytes; if it grows to the size of the table "
            "it summarises it has stopped being worth its own existence"
        )
