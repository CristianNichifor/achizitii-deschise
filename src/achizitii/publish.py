"""Build the browser-queryable bundle served from GitHub Pages.

The archive is 1.5 GB of Parquet, 95.6% of it direct acquisitions. GitHub Pages caps a
site at 1 GB, so the raw rows cannot be published there — and should not be, because a
browser has no use for 26.7 million rows it must download before answering anything.

What a reader actually asks is aggregate: how much did this authority spend, who did it
buy from, what does this CPV usually cost, which acquisitions cluster under the ceiling.
Those answers fit in tens of megabytes, and DuckDB-Wasm can range-request them.

Three rules the published numbers follow, all inherited from METHODOLOGY.md:

1. **No number without its denominator.** Every aggregate carries `n`.
2. **Medians are suppressed below a minimum group size.** A median over two rows is not
   a benchmark; the column is null and `n` says why.
3. **Exclusions are counted, never silent.** Values above the year's legal ceiling cannot
   be lawful direct acquisitions (2017 contains one of 29,977,280,000,000 RON). They are
   dropped from monetary aggregates and the count is written to the manifest.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

from .config import ROOT
from .indicators import threshold_for

log = logging.getLogger(__name__)

PUBLISH_VERSION = 1
"""Bumped when the shape of the bundle changes, so a cached site can detect staleness."""

YEARS = range(2016, 2027)
"""Years the archive covers; ceilings are emitted for each."""

MIN_GROUP_FOR_MEDIAN = 5
"""Below this a median describes the group's members, not a market. See METHODOLOGY.md."""


@dataclass(frozen=True)
class Dataset:
    """One published file."""

    name: str
    sql: str
    description: str


# The ceiling is per YEAR AND CATEGORY, and getting that wrong invents exclusions.
# Works have their own, much higher figure — 900,400 against 270,120 for goods and
# services — and before 2022 the works ceiling is not established at all. Screening
# `lucrari` against the goods ceiling dropped 6,713 works acquisitions in 2019 alone
# (12% of that year's works) as "impossible" when they were very likely lawful.
#
# Where no ceiling is established, `prag` is NULL. Those rows are kept and treated as
# unscreened rather than excluded: without a threshold to judge by, we cannot call a
# value impossible. That is the whole reason for the LEFT JOIN and the NULL branch.
BASE_VIEW = """
CREATE OR REPLACE VIEW ad AS
WITH praguri(an, categorie, prag) AS (VALUES {thresholds})
SELECT a.an,
       a.cpv,
       a.cpv_denumire,
       a.categorie,
       a.autoritate,
       a.autoritate_cui,
       a.furnizor,
       a.furnizor_cui,
       TRY_CAST(a.valoare_ron AS DOUBLE) AS v,
       p.prag,
       (TRY_CAST(a.valoare_ron AS DOUBLE) > 0
        AND (p.prag IS NULL OR TRY_CAST(a.valoare_ron AS DOUBLE) <= p.prag)) AS plauzibil
FROM achizitii_directe a
LEFT JOIN praguri p ON p.an = a.an AND p.categorie = a.categorie
"""


def ceilings_by_category_sql() -> str:
    """A VALUES list of (year, category, ceiling).

    `thresholds_values_sql` answers for one category at a time; publishing aggregates
    over every category at once needs them side by side. Categories with no established
    ceiling are simply absent, which the LEFT JOIN turns into "unscreened".
    """
    rows: list[str] = []
    for category, key in (
        ("furnizare", "goods_services"),
        ("servicii", "goods_services"),
        ("lucrari", "works"),
    ):
        for year in YEARS:
            ceiling = threshold_for(date(year, 7, 1), key)
            if ceiling is not None:
                rows.append(f"({year}, '{category}', {ceiling})")
    if not rows:
        raise RuntimeError("no ceilings established for any year; refusing to publish")
    return ", ".join(rows)

DATASETS = (
    Dataset(
        name="sumar_an",
        description="Totals per year and category — the top-level view.",
        sql="""
        SELECT an,
               categorie,
               count(*)                                       AS n,
               count(*) FILTER (WHERE plauzibil)              AS n_valori_folosite,
               round(sum(v) FILTER (WHERE plauzibil), 2)      AS valoare_totala_ron,
               round(median(v) FILTER (WHERE plauzibil), 2)   AS mediana_ron,
               count(*) FILTER (WHERE v IS NOT NULL AND NOT plauzibil)
                                                              AS n_excluse_peste_plafon
        FROM ad
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
    ),
    Dataset(
        name="cpv_an",
        description=(
            "Per CPV code and year: how many acquisitions and what they typically cost. "
            f"Percentiles are null below n={MIN_GROUP_FOR_MEDIAN}."
        ),
        sql=f"""
        SELECT an,
               cpv,
               any_value(cpv_denumire)                        AS cpv_denumire,
               count(*)                                       AS n,
               count(*) FILTER (WHERE plauzibil)              AS n_valori_folosite,
               round(sum(v) FILTER (WHERE plauzibil), 2)      AS valoare_totala_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(v) FILTER (WHERE plauzibil), 2) END AS mediana_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(v, 0.10) FILTER (WHERE plauzibil), 2) END AS p10_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(v, 0.90) FILTER (WHERE plauzibil), 2) END AS p90_ron
        FROM ad
        WHERE cpv IS NOT NULL
        GROUP BY 1, 2
        """,
    ),
    Dataset(
        name="autoritati_an",
        description="Per contracting authority and year: volume and spend.",
        sql="""
        SELECT an,
               autoritate_cui,
               any_value(autoritate)                          AS autoritate,
               count(*)                                       AS n,
               count(*) FILTER (WHERE plauzibil)              AS n_valori_folosite,
               round(sum(v) FILTER (WHERE plauzibil), 2)      AS valoare_totala_ron,
               count(DISTINCT furnizor_cui)                   AS furnizori_distincti
        FROM ad
        WHERE autoritate_cui IS NOT NULL
        GROUP BY 1, 2
        """,
    ),
    Dataset(
        name="furnizori_an",
        description="Per supplier and year: volume, revenue and how many buyers.",
        sql="""
        SELECT an,
               furnizor_cui,
               any_value(furnizor)                            AS furnizor,
               count(*)                                       AS n,
               count(*) FILTER (WHERE plauzibil)              AS n_valori_folosite,
               round(sum(v) FILTER (WHERE plauzibil), 2)      AS valoare_totala_ron,
               count(DISTINCT autoritate_cui)                 AS autoritati_distincte
        FROM ad
        WHERE furnizor_cui IS NOT NULL
        GROUP BY 1, 2
        """,
    ),
)


def _write(con: duckdb.DuckDBPyConnection, dataset: Dataset, out: Path) -> dict[str, object]:
    target = out / f"{dataset.name}.parquet"
    con.execute(
        f"COPY ({dataset.sql}) TO '{target}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0]
    log.info("%s: %s rows, %.1f MB", dataset.name, f"{rows:,}", target.stat().st_size / 1e6)
    return {
        "name": dataset.name,
        "file": f"{dataset.name}.parquet",
        "rows": rows,
        "bytes": target.stat().st_size,
        "description": dataset.description,
    }


def build(out_dir: Path | None = None) -> dict[str, object]:
    """Write the published bundle and return its manifest."""
    from .govpipeline import _register

    out = Path(out_dir or Path(ROOT) / "site" / "data")
    out.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    _register(con, {"achizitii_directe"})
    con.execute(BASE_VIEW.format(thresholds=ceilings_by_category_sql()))

    manifest: dict[str, object] = {
        "publish_version": PUBLISH_VERSION,
        "min_group_for_median": MIN_GROUP_FOR_MEDIAN,
        "licenta_date": "CC BY 4.0",
        "sursa": "data.gov.ro — exporturile trimestriale ANAP/ADR",
        "datasets": [],
        "indicatori": [],
    }

    manifest["datasets"] = [_write(con, d, out) for d in DATASETS]

    # Coverage and the exclusion count, so a reader can see what was left out.
    total, plausible, excluded, years = con.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE plauzibil),
               count(*) FILTER (WHERE v IS NOT NULL AND NOT plauzibil),
               list(DISTINCT an ORDER BY an)
        FROM ad
        """
    ).fetchone()
    manifest["acoperire"] = {
        "achizitii_directe_randuri": total,
        "valori_folosite": plausible,
        "valori_excluse_peste_plafon": excluded,
        "ani": years,
    }

    # The indicator findings are already Parquet; copy them in rather than recompute.
    findings = Path(ROOT) / "data" / "findings"
    if findings.is_dir():
        dest = out / "indicatori"
        dest.mkdir(exist_ok=True)
        for src in sorted(findings.glob("*.parquet")):
            shutil.copy2(src, dest / src.name)
            rows = con.execute(
                f"SELECT count(*) FROM read_parquet('{dest / src.name}')"
            ).fetchone()[0]
            # The findings tables do not share a schema — only three of the seven carry
            # a year column, because the others aggregate across years by nature (a
            # supplier's share of an authority's budget is not a per-year row). The
            # published columns are recorded so the site can offer a year filter only
            # where one exists, instead of emitting SQL that fails to bind.
            columns = [
                d[0]
                for d in con.execute(
                    f"SELECT * FROM read_parquet('{dest / src.name}') LIMIT 0"
                ).description
            ]
            manifest["indicatori"].append(  # type: ignore[union-attr]
                {
                    "id": src.stem,
                    "file": f"indicatori/{src.name}",
                    "rows": rows,
                    "columns": columns,
                }
            )
    else:
        log.warning("no findings directory; publishing without indicators")

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    con.close()
    return manifest
