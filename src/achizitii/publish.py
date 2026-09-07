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
from .deflator import deflator_rows
from .deflator import source as ipc_source
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
WITH praguri(an, categorie, prag, prag_max) AS (VALUES {thresholds})
SELECT a.an,
       a.cpv,
       a.cpv_denumire,
       a.categorie,
       a.autoritate,
       a.autoritate_cui,
       a.furnizor,
       a.furnizor_cui,
       TRY_CAST(a.valoare_ron AS DOUBLE) AS v,
       CASE WHEN regexp_matches(a.cpv_denumire, '{numeric_label}')
            THEN NULL ELSE a.cpv_denumire END AS denumire_reala,
       p.prag,
       -- Where the year has its own ceiling, judge against it. Where it does not, fall
       -- back to the highest ceiling the category has ever had, so an unscreened year
       -- is still bounded by something rather than by nothing.
       (TRY_CAST(a.valoare_ron AS DOUBLE) > 0
        AND TRY_CAST(a.valoare_ron AS DOUBLE) <= COALESCE(p.prag, p.prag_max))
           AS plauzibil
FROM achizitii_directe a
LEFT JOIN praguri p ON p.an = a.an AND p.categorie = a.categorie
"""


# A CPV label that is just a number is not a label. The pre-2019 exports put the
# internal CPV_CODE_ID in the name column, so 39831240 reads "15113" instead of "Produse
# de curatenie" — 14,074,968 rows, 56% of every labelled row in the archive, and 100% of
# 2016 and 2017. Null-rate checks cannot see this: the column is populated, just with
# something meaningless, which is why it survived every validation pass until someone
# looked at the published table.
#
# The decimal branch matters. Matching only ^[0-9]+$ finds 7.4M rows and misses the ones
# written "11728.0" — exactly half the problem.
NUMERIC_LABEL = r'^[0-9]+([.,][0-9]+)?$'

# The fix needs no external vocabulary: the same code carries a proper label in the later
# exports, so the archive can repair itself. 8,572 codes have one; 510 never do, and
# those are left NULL rather than filled with a guess.
CPV_LABELS_VIEW = f"""
CREATE OR REPLACE VIEW cpv_labels AS
SELECT substr(cpv, 1, 8) AS code, mode(cpv_denumire) AS nume
FROM achizitii_directe
WHERE cpv IS NOT NULL
  AND cpv_denumire IS NOT NULL
  AND NOT regexp_matches(cpv_denumire, '{NUMERIC_LABEL}')
GROUP BY 1
"""


def max_ceiling_for(key: str) -> float | None:
    """The highest ceiling ever established for a category.

    Ceilings are a schedule, and before 2022 the works ceiling was never established at
    all. Screening those years against nothing is how a 2016 works record of
    543,595,445,218 RON — a school asphalting job, 98% of that year's works total —
    ended up inside a published sum.

    A value above the most permissive ceiling the law has EVER had cannot be a lawful
    direct acquisition in any year. That is a bound we can defend without claiming to
    know what the 2016 works ceiling was: we are not asserting a ceiling, only that
    543 billion exceeds every ceiling this law has ever set.
    """
    from .indicators import THRESHOLDS

    values = [
        row.get(key) for row in THRESHOLDS if row.get("verified") and row.get(key)
    ]
    return max(values) if values else None


def ceilings_by_category_sql() -> str:
    """A VALUES list of (year, category, ceiling, highest-ever ceiling).

    `prag` is the year's own ceiling and is NULL where none is established. `prag_max`
    is always present, and is what an otherwise-unscreened row is judged against.
    """
    rows: list[str] = []
    for category, key in (
        ("furnizare", "goods_services"),
        ("servicii", "goods_services"),
        ("lucrari", "works"),
    ):
        ceiling_ever = max_ceiling_for(key)
        if ceiling_ever is None:
            raise RuntimeError(f"no ceiling ever established for {key}")
        for year in YEARS:
            ceiling = threshold_for(date(year, 7, 1), key)
            rows.append(
                f"({year}, '{category}', "
                f"{ceiling if ceiling is not None else 'NULL'}, {ceiling_ever})"
            )
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
               -- Prefer the repaired label; fall back to the row's own only when it is
               -- a real name, and publish NULL rather than a number nobody can read.
               any_value(COALESCE(l.nume, denumire_reala))    AS cpv_denumire,
               count(*)                                       AS n,
               count(*) FILTER (WHERE plauzibil)              AS n_valori_folosite,
               round(sum(v) FILTER (WHERE plauzibil), 2)      AS valoare_totala_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(v) FILTER (WHERE plauzibil), 2) END AS mediana_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(v, 0.10) FILTER (WHERE plauzibil), 2) END AS p10_ron,
               CASE WHEN count(*) FILTER (WHERE plauzibil) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(v, 0.90) FILTER (WHERE plauzibil), 2) END AS p90_ron
        FROM ad LEFT JOIN cpv_labels l ON l.code = substr(ad.cpv, 1, 8)
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


ITEMS_GLOB = "items/**/*.parquet"
PRICES_DIR = "preturi"

# Only the columns a price comparison needs. The raw line items carry 27, including
# authority and supplier names that are already in the bulk tables; slimming to fourteen
# takes a row from ~110 bytes to roughly 50.
#
# EVERY line item is archived, not only the comparable ones. An earlier version filtered
# on `comparabil` here, which quietly discarded a quarter of every day — 109 of 438 on
# the first day measured. That is indefensible for a source that cannot be backfilled:
# incomparable rows are still real public spending, the reason they are incomparable is
# recorded rather than inferred, and a later improvement to the comparability rules can
# reclassify them only if they were kept. METHODOLOGY.md already says incomparable rows
# are kept and excluded from benchmarks; the archive now actually does that, and the
# exclusion happens at benchmark time instead.
ARCHIVE_SQL = """
SELECT ocid, data_finalizare, judet, cpv, denumire_key, um, um_uncefact,
       marime_pachet, cantitate, pret_unitar_ron, autoritate_cui, furnizor_cui,
       comparabil, motiv_necomparabil
FROM read_parquet('{glob}')
WHERE pret_unitar_ron > 0
"""


def archive_items(out: Path) -> list[str]:
    """Move freshly ingested line items into the permanent, append-only price archive.

    Unit prices cannot be backfilled. The bulk exports carry no quantities at all, and
    reconstructing history from SEAP would need a per-record call for every acquisition
    ever published — millions of requests against a free public endpoint. So the archive
    can only ever grow forwards from the day collection starts, which makes not losing a
    day the single most important property here.

    **One file per day, never rewritten.** Appending to a monthly file would make git
    store a fresh copy of a growing file every day — roughly 150 MB a month by the end
    rather than the 10 MB the data actually occupies. Day files are written once and
    then immutable, so a year costs what a year of data costs (~115 MB), and Hive
    partitioning still lets DuckDB skip whole months.
    """
    items = out / "items"
    if not any(items.rglob("*.parquet")):
        return []

    con = duckdb.connect()
    written: list[str] = []
    days = con.execute(
        f"""
        SELECT DISTINCT CAST(data_finalizare AS DATE) AS zi
        FROM read_parquet('{items / "**" / "*.parquet"}')
        WHERE pret_unitar_ron > 0 AND data_finalizare IS NOT NULL
        ORDER BY 1
        """
    ).fetchall()

    for (day,) in days:
        target = out / PRICES_DIR / f"an={day.year}" / f"luna={day.month:02d}" / f"{day}.parquet"
        if target.exists():
            # Immutable by design: a day already archived is never rewritten, so a
            # re-run cannot corrupt history or churn git. Re-ingesting a day therefore
            # requires deleting its file first, deliberately.
            log.info("%s already archived, leaving it alone", day)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        sql = ARCHIVE_SQL.format(glob=items / "**" / "*.parquet")
        con.execute(
            f"COPY ({sql} AND CAST(data_finalizare AS DATE) = DATE '{day}') "
            f"TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0]
        if not rows:
            target.unlink()
            continue
        written.append(str(target.relative_to(out)))
        log.info("archived %s: %s rows, %.2f MB", day, f"{rows:,}", target.stat().st_size / 1e6)
    con.close()
    return written


UNIT_PRICE_DATASETS = (
    Dataset(
        name="preturi_unitare",
        description=(
            "Median unit price per product group. A group is CPV + product key + unit + "
            f"pack size; percentiles are null below n={MIN_GROUP_FOR_MEDIAN}. Never "
            "compare across units or pack sizes."
        ),
        sql=f"""
        WITH taiat AS (
            SELECT *, quantile_cont(pret_unitar_ron, 0.01) OVER g AS lo,
                      quantile_cont(pret_unitar_ron, 0.99) OVER g AS hi
            FROM preturi
            WHERE comparabil
            WINDOW g AS (PARTITION BY cpv, denumire_key, um, marime_pachet)
        )
        SELECT cpv, denumire_key, um, any_value(um_uncefact) AS um_uncefact,
               marime_pachet,
               count(*)                                        AS n,
               count(DISTINCT autoritate_cui)                  AS autoritati,
               count(DISTINCT furnizor_cui)                    AS furnizori,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(pret_unitar_ron), 2) END AS mediana_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(pret_unitar_ron, 0.10), 2) END AS p10_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(pret_unitar_ron, 0.90), 2) END AS p90_ron,
               min(CAST(data_finalizare AS DATE))              AS din,
               max(CAST(data_finalizare AS DATE))              AS pana_la
        FROM taiat
        WHERE comparabil AND pret_unitar_ron BETWEEN lo AND hi
        GROUP BY cpv, denumire_key, um, marime_pachet
        """,
    ),
    Dataset(
        name="preturi_produs",
        description=(
            "The same product across CPV codes: grouped on description, unit and pack "
            "size while the code is allowed to vary. `coduri` says how many CPV codes "
            "contributed, so a wide spread is visible rather than hidden."
        ),
        sql=f"""
        SELECT denumire_key, um, marime_pachet,
               count(*)                                        AS n,
               count(DISTINCT cpv)                             AS coduri,
               mode(cpv)                                       AS cpv_principal,
               count(DISTINCT autoritate_cui)                  AS autoritati,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(pret_unitar_ron), 2) END AS mediana_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(pret_unitar_ron, 0.10), 2) END AS p10_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(pret_unitar_ron, 0.90), 2) END AS p90_ron
        FROM preturi
        WHERE comparabil AND denumire_key IS NOT NULL AND denumire_key <> ''
        GROUP BY denumire_key, um, marime_pachet
        """,
    ),
    Dataset(
        name="preturi_judet",
        description=(
            "The same groups broken down by county, for comparing what different buyers "
            "paid for the same thing. Suppressed below "
            f"n={MIN_GROUP_FOR_MEDIAN} per county."
        ),
        sql=f"""
        SELECT cpv, denumire_key, um, marime_pachet, judet,
               count(*)                                        AS n,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(pret_unitar_ron), 2) END AS mediana_ron
        FROM preturi
        WHERE comparabil AND judet IS NOT NULL
        GROUP BY cpv, denumire_key, um, marime_pachet, judet
        """,
    ),
)


def build(out_dir: Path | None = None, *, only: str | None = None) -> dict[str, object]:
    """Write the published bundle and return its manifest.

    `only="preturi"` rebuilds just the unit-price section and merges it into the
    manifest already on disk. That mode exists because the daily job runs on a GitHub
    runner, where the bulk archive is NOT available — data.gov.ro refuses connections
    from GitHub and Azure IP ranges. Regenerating the whole manifest there would drop
    every bulk dataset from it and silently strip them off the published site, even
    though the Parquet files themselves were still sitting in the repo.
    """
    out = Path(out_dir or Path(ROOT) / "site" / "data")
    out.mkdir(parents=True, exist_ok=True)
    prices_only = only == "preturi"

    con = duckdb.connect()

    manifest: dict[str, object] = {
        "publish_version": PUBLISH_VERSION,
        "min_group_for_median": MIN_GROUP_FOR_MEDIAN,
        "licenta_date": "CC BY 4.0",
        "sursa": "data.gov.ro — exporturile trimestriale ANAP/ADR",
        "datasets": [],
        "indicatori": [],
    }

    existing_path = out / "manifest.json"
    if prices_only:
        if not existing_path.is_file():
            raise RuntimeError(
                "--only preturi merges into an existing manifest, and none was found. "
                "Run a full `achizitii publish` first."
            )
        manifest = json.loads(existing_path.read_text(encoding="utf-8"))
        # Drop only the price datasets; everything else is carried over untouched.
        names = {d.name for d in UNIT_PRICE_DATASETS}
        manifest["datasets"] = [
            d for d in manifest.get("datasets", []) if d["name"] not in names
        ]
    else:
        from .govpipeline import _register

        _register(con, {"achizitii_directe"})
        con.execute(
            BASE_VIEW.format(
                thresholds=ceilings_by_category_sql(),
                numeric_label=NUMERIC_LABEL,
            )
        )
        con.execute(CPV_LABELS_VIEW)
        manifest["datasets"] = [_write(con, d, out) for d in DATASETS]

    # Unit prices: the archive is append-only and starts the day collection began, so it
    # is normal for this to be empty on a fresh checkout. An absent section is honest;
    # an empty table presented as a benchmark would not be.
    manifest["arhivat_azi"] = archive_items(out)
    prices = sorted((out / PRICES_DIR).rglob("*.parquet"))
    if prices:
        con.execute(
            "CREATE OR REPLACE VIEW preturi AS "
            f"SELECT * FROM read_parquet('{out / PRICES_DIR / '**' / '*.parquet'}', "
            "hive_partitioning = true)"
        )
        manifest["datasets"] += [_write(con, d, out) for d in UNIT_PRICE_DATASETS]
        rows, first, last = con.execute(
            "SELECT count(*), min(CAST(data_finalizare AS DATE)), "
            "max(CAST(data_finalizare AS DATE)) FROM preturi"
        ).fetchone()
        manifest["preturi_unitare"] = {
            "randuri": rows,
            "zile_arhivate": len(prices),
            "din": str(first),
            "pana_la": str(last),
            "nota": (
                "Prețurile unitare nu pot fi reconstituite retroactiv: exporturile în "
                "masă nu conțin cantități. Arhiva crește doar înainte, de la prima zi "
                "colectată."
            ),
        }
    else:
        log.info("no unit-price archive yet; publishing without price benchmarks")

    # The deflator: published as its own table rather than applied to the figures, so
    # published numbers stay as-published and the reader picks the base year. Emitted in
    # both modes because it depends on nothing but a checked-in file.
    rows = deflator_rows()
    con.execute(
        # Cast explicitly: DuckDB infers DECIMAL from numeric literals, which Arrow
        # then hands the browser as a decimal type that formats badly.
        "CREATE OR REPLACE TABLE deflator AS SELECT an::INTEGER AS an, "
        "indice::DOUBLE AS indice, an_baza::INTEGER AS an_baza, "
        "factor::DOUBLE AS factor, baza, sursa FROM (VALUES "
        + ", ".join(
            "({an}, {indice}, {an_baza}, {factor}, '{baza}', '{sursa}')".format(
                **{k: str(v).replace("'", "''") if isinstance(v, str) else v
                   for k, v in r.items()}
            )
            for r in rows
        )
        + ") AS t(an, indice, an_baza, factor, baza, sursa)"
    )
    con.execute(
        f"COPY deflator TO '{out / 'deflator.parquet'}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    manifest["deflator"] = {
        "file": "deflator.parquet",
        "ani": [r["an"] for r in rows],
        "an_baza": rows[0]["an_baza"],
        **{k: v for k, v in ipc_source().items() if k in ("nume", "url", "baza", "publicat")},
        "nota": (
            "Sumele publicate sunt NOMINALE. Acest tabel permite exprimarea lor în "
            "moneda unui an de referință. 2026 nu are indice publicat de Eurostat, deci "
            "nu poate fi ajustat — rămâne nominal."
        ),
    }

    # RUTI: the meetings register, published as its own table and deliberately NOT
    # joined to anything. See src/achizitii/ruti.py for why a supplier<->meeting
    # indicator would be indefensible on this data.
    ruti_src = Path(ROOT) / "data" / "ruti" / "meetings.parquet"
    if ruti_src.is_file():
        shutil.copy2(ruti_src, out / "ruti.parquet")
        rows, first, last, conclusions = con.execute(
            f"""SELECT count(*), min(CAST(data_intalnirii AS DATE)),
                       max(CAST(data_intalnirii AS DATE)),
                       count(*) FILTER (WHERE concluzii IS NOT NULL)
                FROM read_parquet('{out / "ruti.parquet"}')"""
        ).fetchone()
        manifest["ruti"] = {
            "file": "ruti.parquet",
            "intalniri": rows,
            "din": str(first),
            "pana_la": str(last),
            # Surfaced because it is the register's central weakness: publishing that a
            # meeting happened is mandatory, saying what was discussed is not.
            "cu_concluzii_publicate": conclusions,
            "nota": (
                "Registrul întâlnirilor dintre decidenți și terți. O întâlnire este "
                "legală, iar registrul există tocmai pentru a o face vizibilă — "
                "prezența aici nu spune nimic despre vreun contract."
            ),
        }

    # Coverage and the exclusion count, so a reader can see what was left out. The
    # `ad` view only exists when the bulk archive was registered; in prices-only mode
    # the figures already in the manifest are still correct and are left alone.
    if prices_only:
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        con.close()
        return manifest

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
