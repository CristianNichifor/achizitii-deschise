"""The unit-price archive is append-only, and that property is load-bearing.

Unit prices cannot be reconstructed after the fact. The bulk exports carry no
quantities, and rebuilding history from SEAP would need a per-record call for every
acquisition ever published. So the archive only ever grows forwards from the first day
collected, and a day lost is lost permanently.

Two mistakes would cause that loss quietly:

  - rewriting a day that already exists, replacing good data with a partial re-ingest
  - regenerating the manifest on a machine without the bulk archive, which would strip
    the bulk datasets off the published site while their Parquet files sat untouched in
    the repo

Both are covered here.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from achizitii.publish import (
    MIN_GROUP_FOR_MEDIAN,
    PRICES_DIR,
    UNIT_PRICE_DATASETS,
    archive_items,
    build,
)

ROWS = [
    # ocid, date, county, cpv, product key, unit, uncefact, pack, qty, unit price, buyer, supplier
    ("ocds-a-1", "2026-09-04", "Cluj", "15112000", "pulpe de pui", "kg", "KGM", None, 10.0, 21.5, "1", "9"),
    ("ocds-a-2", "2026-09-04", "Iasi", "15112000", "pulpe de pui", "kg", "KGM", None, 5.0, 23.0, "2", "9"),
    ("ocds-b-1", "2026-09-05", "Cluj", "30213100", "laptop", "buc", "H87", None, 1.0, 3600.0, "1", "8"),
]


def _make_items(tmp: Path, rows: list | None = None) -> None:
    """Write a raw items file shaped like what `achizitii seap` produces."""
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE it (ocid VARCHAR, data_finalizare DATE, judet VARCHAR, cpv VARCHAR,
           denumire_key VARCHAR, um VARCHAR, um_uncefact VARCHAR, marime_pachet INTEGER,
           cantitate DOUBLE, pret_unitar_ron DOUBLE, autoritate_cui VARCHAR,
           furnizor_cui VARCHAR, comparabil BOOLEAN)"""
    )
    for row in rows if rows is not None else ROWS:
        con.execute("INSERT INTO it VALUES (?,?,?,?,?,?,?,?,?,?,?,?,true)", list(row))
    target = tmp / "items" / "an=2026"
    target.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY it TO '{target / 'part-0.parquet'}' (FORMAT PARQUET)")
    con.close()


def test_one_file_per_day(tmp_path: Path) -> None:
    """Day files keep git from storing a growing monthly file once per day."""
    _make_items(tmp_path)
    written = archive_items(tmp_path)
    assert sorted(written) == [
        str(Path(PRICES_DIR) / "an=2026" / "luna=09" / "2026-09-04.parquet"),
        str(Path(PRICES_DIR) / "an=2026" / "luna=09" / "2026-09-05.parquet"),
    ]


def test_archiving_is_idempotent_and_never_rewrites(tmp_path: Path) -> None:
    """A re-run must not replace an archived day with a partial re-ingest."""
    _make_items(tmp_path)
    archive_items(tmp_path)
    day = tmp_path / PRICES_DIR / "an=2026" / "luna=09" / "2026-09-04.parquet"
    before = day.read_bytes()

    # Re-ingest the same day with FEWER rows, as a truncated run would produce.
    _make_items(tmp_path, rows=[ROWS[0]])
    assert archive_items(tmp_path) == [], "an existing day must not be rewritten"
    assert day.read_bytes() == before, "archived day was overwritten by a smaller re-ingest"


def test_incomparable_rows_are_not_archived(tmp_path: Path) -> None:
    """Only comparable rows earn a place; the rest cannot support a price comparison."""
    _make_items(tmp_path)
    items = tmp_path / "items" / "an=2026" / "part-0.parquet"
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{items}')")
    con.execute("UPDATE t SET comparabil = false WHERE judet = 'Iasi'")
    con.execute(f"COPY t TO '{items}' (FORMAT PARQUET)")
    con.close()

    archive_items(tmp_path)
    day = tmp_path / PRICES_DIR / "an=2026" / "luna=09" / "2026-09-04.parquet"
    con = duckdb.connect()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{day}')").fetchone()[0]
    con.close()
    assert n == 1


def test_prices_only_refuses_without_an_existing_manifest(tmp_path: Path) -> None:
    """Merging into a manifest that is not there would silently produce a partial one."""
    with pytest.raises(RuntimeError, match="existing manifest"):
        build(tmp_path, only="preturi")


def test_prices_only_preserves_the_bulk_sections(tmp_path: Path) -> None:
    """The daily job runs where the bulk archive is unreachable.

    Regenerating the whole manifest there would drop every bulk dataset from it and
    strip them off the published site, even though the Parquet files are still present.
    """
    _make_items(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "publish_version": 1,
                "datasets": [{"name": "sumar_an", "file": "sumar_an.parquet", "rows": 41}],
                "indicatori": [{"id": "prag-01", "file": "indicatori/prag-01.parquet"}],
                "acoperire": {"achizitii_directe_randuri": 26_672_772},
            }
        ),
        encoding="utf-8",
    )
    manifest = build(tmp_path, only="preturi")
    names = [d["name"] for d in manifest["datasets"]]
    assert "sumar_an" in names, "bulk dataset was dropped from the manifest"
    assert "preturi_unitare" in names
    assert manifest["acoperire"]["achizitii_directe_randuri"] == 26_672_772
    assert manifest["indicatori"], "indicators were dropped from the manifest"


def test_medians_are_suppressed_below_the_minimum_group(tmp_path: Path) -> None:
    _make_items(tmp_path)
    (tmp_path / "manifest.json").write_text(
        '{"datasets": [], "indicatori": []}', encoding="utf-8"
    )
    build(tmp_path, only="preturi")
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT n, mediana_ron FROM read_parquet('{tmp_path / 'preturi_unitare.parquet'}')"
    ).fetchall()
    con.close()
    assert rows, "no price groups were produced"
    for n, median in rows:
        assert (median is None) == (n < MIN_GROUP_FOR_MEDIAN), (
            f"group of {n} published median={median}; the rule is n>={MIN_GROUP_FOR_MEDIAN}"
        )


@pytest.mark.parametrize("dataset", UNIT_PRICE_DATASETS, ids=lambda d: d.name)
def test_price_groups_hold_unit_and_pack_size_constant(dataset) -> None:
    """METHODOLOGY.md: comparing across units or pack sizes is meaningless.

    This is the invariant that never bends. Two rows measured in different units, or in
    packs of different size, cannot be compared no matter what else they share.
    """
    grouping = dataset.sql.split("GROUP BY")[-1]
    for column in ("um", "marime_pachet"):
        assert column in grouping, f"{dataset.name} does not hold {column} constant"


def test_only_the_product_view_lets_cpv_vary() -> None:
    """CPV is a filter, not part of the identity — but only where that is intended.

    31.7% of specific-product rows appear under more than one CPV code: "hartie
    copiator a4" under three, "bonuri valorice carburanti" under codes in different
    divisions. `preturi_produs` groups those together deliberately and reports how many
    codes contributed. Every other price view keeps CPV in the key, because merging
    unrelated products that happen to share wording would be worse than splitting one.
    """
    by_name = {d.name: d.sql.split("GROUP BY")[-1] for d in UNIT_PRICE_DATASETS}
    assert "cpv" not in by_name["preturi_produs"], (
        "preturi_produs exists to group across CPV codes; keeping cpv in the key "
        "defeats it"
    )
    assert "coduri" in by_name["preturi_produs"] or "count(DISTINCT cpv)" in (
        next(d.sql for d in UNIT_PRICE_DATASETS if d.name == "preturi_produs")
    ), "preturi_produs must report how many CPV codes were merged"
    for name in ("preturi_unitare", "preturi_judet"):
        assert "cpv" in by_name[name], f"{name} must keep CPV in the group key"
