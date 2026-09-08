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

import datetime as dt
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from .config import ROOT
from .deflator import deflator_rows
from .deflator import source as ipc_source
from .indicators import INDICATORS_BY_ID, threshold_for

log = logging.getLogger(__name__)

PUBLISH_VERSION = 1
"""Bumped when the shape of the bundle changes, so a cached site can detect staleness."""

YEARS = range(2016, 2027)
"""Years the archive covers; ceilings are emitted for each."""

MIN_GROUP_FOR_MEDIAN = 5
"""Below this a median describes the group's members, not a market. See METHODOLOGY.md."""


# Row groups are the unit a Parquet reader can skip. Ten groups of 71,000 rows meant a
# reader had to fetch all of them for any predicate; 20,000-row groups give a lookup
# something to prune against. The cost is a slightly larger footer, which is fetched once.
ROW_GROUP = 20_000


@dataclass(frozen=True)
class Dataset:
    """One published file."""

    name: str
    sql: str
    description: str
    # The column a reader looks this table up BY, if there is one.
    #
    # Sorting on it is what makes a Parquet file skippable over HTTP. Measured on
    # furnizori_an, 710,398 rows: written in insertion order, every one of its ten row
    # groups had a min/max range spanning the whole key space, so a query for one fiscal
    # code had to read all 14.9 MB. Sorted, one row group of thirty-five answers it.
    #
    # It also compresses far better, because like values end up adjacent — the same file
    # went from 14.9 MB to 8.1 MB. Both effects come from the same one line.
    order_by: str | None = None


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
       -- Normalised HERE, once, so every aggregate and every indicator downstream sees
       -- one spelling of one organisation.
       --
       -- The exports carry the same fiscal code both ways: "1590120" and "RO1590120".
       -- Grouped by the raw value, Romsilva became two institutions with 22,271 and
       -- 11,969 acquisitions, and the site's entity file — which matches a CUI exactly,
       -- so that 4340536 does not pull in 14340536 — showed whichever half the reader
       -- happened to search for. Measured across the archive: 389 institutions split in
       -- two, 800,135 acquisitions and 4.44 billion RON on the wrong side of a prefix.
       --
       -- This file already knew. The county join below normalised exactly this way to
       -- match ANAF, two hundred lines after the aggregates grouped by the raw column;
       -- the same module both knew and did not know. That join is now redundant and says
       -- so.
       --
       -- Spelling only. `firme.normalise_cui` additionally REJECTS codes outside 2-10
       -- digits, and that is a judgement about validity rather than about spelling —
       -- applying it here would silently drop rows from every count on the site under
       -- the guise of deduplication. A short code stays a short code; it just stops
       -- being two of them.
       nullif(ltrim(regexp_replace(a.autoritate_cui, '[^0-9]', '', 'g'), '0'), '')
           AS autoritate_cui,
       a.furnizor,
       nullif(ltrim(regexp_replace(a.furnizor_cui, '[^0-9]', '', 'g'), '0'), '')
           AS furnizor_cui,
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



# Contract values cannot be summed naively, and the reason is structural rather than a
# matter of outliers.
#
# A framework agreement (acord-cadru) publishes its CEILING — the maximum callable over
# the agreement's life — not money spent. Worse, for a multi-supplier framework that
# ceiling is repeated on EVERY supplier's row: the four largest rows in the archive are
# the same 177,930,419,250 RON, same authority, four different pharmaceutical
# wholesalers. Summing them multiplies one ceiling by the number of suppliers, and then
# double-counts again against the call-offs actually placed under it.
#
# So frameworks get a count and a median and NO sum. What was really committed lives in
# the call-offs (contract subsecvent) and in ordinary contracts.
CONTRACT_NATURE_SQL = """
CASE
  WHEN lower(COALESCE(incheiat_prin, '')) LIKE '%subsecvent%'
    THEN 'contract_subsecvent'
  WHEN lower(COALESCE(tip_incheiere, '')) LIKE '%acord-cadru%'
    OR lower(COALESCE(incheiat_prin, '')) LIKE 'cu acord cadru%'
    THEN 'plafon_acord_cadru'
  WHEN lower(COALESCE(tip_incheiere, '')) LIKE '%contract de achizitii%'
    OR lower(COALESCE(incheiat_prin, '')) LIKE 'fara acord%'
    THEN 'contract'
  ELSE 'nedeterminat'
END
"""

EXTREME_CONTRACT = 1_000_000_000.0
"""Above this a contract value is reported separately rather than trusted.

Unlike a direct acquisition there is no legal ceiling to screen against, so nothing here
is excluded — it is disclosed. The tail is small and dominant at once: 301 rows (0.076%)
carry 40% of the ordinary-contract total, and 562 rows (0.027%) carry 84.5% of the
framework total. A reader given only a sum would be reading those rows and nothing else,
so both figures are published side by side."""

CONTRACTE_VIEW = f"""
CREATE OR REPLACE VIEW ct AS
SELECT an,
       {CONTRACT_NATURE_SQL} AS natura,
       TRY_CAST(valoare_ron AS DOUBLE) AS v,
       autoritate_cui,
       furnizor_cui
FROM contracte
"""


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



# Buyer and supplier county, joined through the ANAF profile cache. The exports say
# where neither party is: supplier locality is missing on 77% of rows and the buyer's
# county is never given at all. With both sides enriched, 97.9% of the archive can be
# placed geographically.
GEO_VIEW = """
CREATE OR REPLACE VIEW geo AS
SELECT ad.an,
       fa.judet AS judet_autoritate,
       ff.judet AS judet_furnizor,
       ad.v,
       ad.plauzibil
FROM ad
-- `ad` normalises both codes now, so these join on the column directly. The stripping
-- that used to happen here is where the fix came from: this join has always matched ANAF
-- on the normalised code while the aggregates grouped on the raw one.
JOIN firme_geo fa ON fa.cui = ad.autoritate_cui
JOIN firme_geo ff ON ff.cui = ad.furnizor_cui
"""

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
        name="contracte_an",
        description=(
            "Contracts per year and nature. Framework agreements publish a CEILING that "
            "is repeated per supplier, so they carry no sum — only a count and a median. "
            "Money committed is the call-offs and ordinary contracts."
        ),
        sql=f"""
        SELECT an,
               natura,
               count(*)                                        AS n,
               -- No sum for framework ceilings: it would multiply one ceiling by the
               -- number of suppliers and then double-count the call-offs beneath it.
               CASE WHEN natura <> 'plafon_acord_cadru'
                    THEN round(sum(v), 2) END                  AS valoare_totala_ron,
               CASE WHEN natura <> 'plafon_acord_cadru'
                    THEN round(sum(v) FILTER (WHERE v <= {EXTREME_CONTRACT}), 2)
               END                                             AS valoare_fara_extreme_ron,
               count(*) FILTER (WHERE v > {EXTREME_CONTRACT})   AS n_peste_prag_extrem,
               round(median(v), 2)                             AS mediana_ron,
               round(quantile_cont(v, 0.90), 2)                AS p90_ron,
               round(max(v), 2)                                AS maxim_ron,
               count(DISTINCT autoritate_cui)                  AS autoritati,
               count(DISTINCT furnizor_cui)                    AS furnizori
        FROM ct
        WHERE v IS NOT NULL AND v > 0
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
    ),
    Dataset(
        name="judete_an",
        description=(
            "Where the money goes geographically: per year and buyer county, how many "
            "awards and how much value stayed with a supplier registered in the same "
            "county. Descriptive, not an indicator — buying locally is lawful and often "
            "sensible."
        ),
        sql="""
        SELECT an,
               judet_autoritate,
               count(*)                                          AS n,
               count(*) FILTER (WHERE judet_autoritate = judet_furnizor) AS n_local,
               round(100.0 * count(*) FILTER (WHERE judet_autoritate = judet_furnizor)
                     / count(*), 1)                              AS pct_local,
               round(sum(v) FILTER (WHERE plauzibil), 2)         AS valoare_totala_ron,
               round(sum(v) FILTER (WHERE plauzibil AND judet_autoritate = judet_furnizor), 2)
                                                                 AS valoare_locala_ron,
               count(DISTINCT judet_furnizor)                    AS judete_furnizoare
        FROM geo
        GROUP BY 1, 2
        ORDER BY 1 DESC, n DESC
        """,
    ),
    Dataset(
        name="autoritati_an",
        order_by="autoritate_cui, an",
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
        order_by="furnizor_cui, an",
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
    # ORDER BY here rather than in each dataset's SQL: it is a property of how the file is
    # STORED, not of what it contains, and every view applies its own ORDER BY when reading.
    body = dataset.sql
    if dataset.order_by:
        body = f"SELECT * FROM ({body}) ORDER BY {dataset.order_by}"
    con.execute(
        f"COPY ({body}) TO '{target}' "
        f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP})"
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
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = 0
        if target.exists():
            existing = con.execute(
                f"SELECT count(*) FROM read_parquet('{target}')"
            ).fetchone()[0]

        # Built in the system temp directory, never beside the archive. A staging file
        # written next to the day files lands inside the glob that `preturi` reads, and
        # every archived day gets counted twice.
        with tempfile.TemporaryDirectory() as staging:
            candidate = Path(staging) / "day.parquet"
            sql = ARCHIVE_SQL.format(glob=items / "**" / "*.parquet")
            con.execute(
                f"COPY ({sql} AND CAST(data_finalizare AS DATE) = DATE '{day}') "
                f"TO '{candidate}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            rows = con.execute(
                f"SELECT count(*) FROM read_parquet('{candidate}')"
            ).fetchone()[0]
            if not rows:
                continue
            if existing and rows < existing:
                # NEVER SHRINK. A day may GROW — running several times during the day is
                # how same-day prices arrive at all, and a later run legitimately sees
                # more. What must never happen is a truncated re-ingest replacing a
                # complete day with fewer rows.
                log.warning(
                    "%s: keeping %d archived rows, refusing a re-ingest with %d",
                    day, existing, rows,
                )
                continue
            if existing and rows == existing:
                continue
            shutil.copy2(candidate, target)

        written.append(str(target.relative_to(out)))
        log.info(
            "archived %s: %s rows%s, %.2f MB",
            day, f"{rows:,}",
            f" (was {existing:,})" if existing else "",
            target.stat().st_size / 1e6,
        )
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
            SELECT *,
                   quantile_cont(pret_unitar_ron, 0.01) OVER g AS lo,
                   quantile_cont(pret_unitar_ron, 0.99) OVER g AS hi,
                   count(*) OVER g                             AS grup
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
        -- Trim outliers only where trimming means anything. On a group of two the 1st
        -- and 99th percentiles fall BETWEEN the two values — for 21.50 and 23.00 the
        -- band is 21.515 to 22.985 — so the filter discarded both and the group
        -- disappeared from the table entirely. Below the size at which a median is
        -- publishable there is no distribution to trim.
        WHERE comparabil
          AND (grup < {MIN_GROUP_FOR_MEDIAN}
               OR pret_unitar_ron BETWEEN lo AND hi)
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
        -- `cpv_principal` used to be mode(cpv), which is not deterministic: where a
        -- group's codes appear the same number of times, DuckDB returns whichever the
        -- scan reached first. Measured on the published archive — the same query, the
        -- same data, the same process, three times — 26 of the 472 multi-code groups
        -- came back different on every run. Every republish therefore produced a diff
        -- of rows nobody had changed, which is noise in a repository whose claim is
        -- that the figures regenerate from the data, and it hides the changes that are
        -- real. This PR's own diff was 92 such rows.
        --
        -- The most frequent code, ties broken by the lowest. On the 21,444 groups with
        -- a clear winner this is exactly what mode() returned; on the 274 that tie it
        -- returns the same answer every time instead of a coin flip.
        WITH per_cpv AS (
            SELECT denumire_key, um, marime_pachet, cpv, count(*) AS k
            FROM preturi
            WHERE comparabil AND denumire_key IS NOT NULL AND denumire_key <> ''
            GROUP BY 1, 2, 3, 4
        ),
        principal AS (
            SELECT denumire_key, um, marime_pachet, cpv AS cpv_principal
            FROM (
                SELECT *, row_number() OVER (
                            PARTITION BY denumire_key, um, marime_pachet
                            ORDER BY k DESC, cpv ASC) AS rn
                FROM per_cpv
            )
            WHERE rn = 1
        )
        SELECT p.denumire_key, p.um, p.marime_pachet,
               count(*)                                        AS n,
               count(DISTINCT p.cpv)                           AS coduri,
               any_value(pr.cpv_principal)                     AS cpv_principal,
               count(DISTINCT p.autoritate_cui)                AS autoritati,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(median(p.pret_unitar_ron), 2) END AS mediana_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(p.pret_unitar_ron, 0.10), 2) END AS p10_ron,
               CASE WHEN count(*) >= {MIN_GROUP_FOR_MEDIAN}
                    THEN round(quantile_cont(p.pret_unitar_ron, 0.90), 2) END AS p90_ron
        FROM preturi p
        -- IS NOT DISTINCT FROM, not `=`: a group with no unit of measure and no pack
        -- size is a real group, and `=` would drop every one of them.
        LEFT JOIN principal pr
               ON pr.denumire_key = p.denumire_key
              AND pr.um IS NOT DISTINCT FROM p.um
              AND pr.marime_pachet IS NOT DISTINCT FROM p.marime_pachet
        WHERE p.comparabil AND p.denumire_key IS NOT NULL AND p.denumire_key <> ''
        GROUP BY p.denumire_key, p.um, p.marime_pachet
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


# The front door, precomputed.
#
# Measured against the published site: rendering it cost **4.67 MB**, of which 3.32 MB was
# `autoritati_an.parquet` — essentially the entire file, fetched as one 200 and seven 206
# ranges, to display SIX ROWS. Range requests cannot help, and that is the point worth
# understanding rather than optimising around: the query is `GROUP BY autoritate_cui` over
# all 155,521 rows, so it genuinely needs every one of them. The only way to not download a
# table is to not aggregate it in the browser.
#
# So the six rows are computed here, once, at publish time. The front door is a fixed
# summary — it takes no filters and never varies — which is exactly the shape that belongs
# in a file rather than in a query.
#
# THE RISK, AND WHAT REMOVES IT. A precomputed summary is a second source of truth, and the
# front door's whole claim is that every figure on it is the same figure as the tab it links
# to. These read the PUBLISHED parquet files, not the underlying view, so they aggregate the
# identical rows the tabs read — including the same per-year rounding, which aggregating
# `ad` directly would silently change. A test asserts the files equal the live query.
PANORAMA_DATASETS = (
    Dataset(
        name="panorama_cumparatori",
        description=(
            "The six largest buyers, precomputed for the front door so it does not "
            "aggregate a 3.3 MB table in the browser to show six rows."
        ),
        sql="""
        SELECT autoritate_cui,
               any_value(autoritate)                    AS autoritate,
               sum(n)::BIGINT                           AS n,
               round(sum(valoare_totala_ron))           AS valoare_totala_ron
        FROM pub_autoritati_an
        WHERE autoritate_cui IS NOT NULL AND autoritate_cui <> ''
        GROUP BY autoritate_cui
        ORDER BY valoare_totala_ron DESC NULLS LAST
        LIMIT 6
        """,
    ),
    Dataset(
        name="panorama_preturi",
        description=(
            "The six widest unit-price spreads, precomputed for the front door. Carries "
            "cpv, pack size and the date window because the row opens a drill-down."
        ),
        sql=f"""
        SELECT denumire_key, um, n,
               round(p90_ron / nullif(p10_ron, 0), 1)   AS de_cate_ori,
               p10_ron, mediana_ron, p90_ron,
               cpv, marime_pachet, din, pana_la
        FROM pub_preturi_unitare
        WHERE n >= {MIN_GROUP_FOR_MEDIAN} AND p10_ron > 0
        ORDER BY de_cate_ori DESC NULLS LAST
        LIMIT 6
        """,
    ),
)


def _panorama(con: duckdb.DuckDBPyConnection, out: Path) -> dict[str, object] | None:
    """Build the front door as JSON, from what was just published.

    JSON RATHER THAN PARQUET, and that is the whole point of this function rather than an
    implementation detail. Reading Parquet in the browser means booting DuckDB-Wasm — 3.4 MB
    from a CDN plus a 0.7 MB worker — and the front door needs about three kilobytes of
    data. Measured on the published site, that engine is what a first-time visitor spends
    roughly four seconds waiting for before anything appears. As JSON the page can draw
    itself from `fetch`, and the engine loads behind it for whoever opens a tab.

    Reads the PUBLISHED Parquet rather than the source views, so the numbers cannot drift
    from the tables the front door links to — `autoritati_an.valoare_totala_ron` is already
    rounded per year, and aggregating `ad` directly would disagree with its own table by a
    few lei for no visible reason.
    """
    for name, view in (("autoritati_an", "pub_autoritati_an"),
                       ("preturi_unitare", "pub_preturi_unitare")):
        src = out / f"{name}.parquet"
        if not src.is_file():
            return None      # the site falls back to querying for itself
        con.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet('{src}')"
        )

    def rows(sql: str) -> list[dict[str, object]]:
        cur = con.execute(sql)
        names = [c[0] for c in cur.description]
        out_rows = []
        for row in cur.fetchall():
            rec: dict[str, object] = {}
            for name, value in zip(names, row, strict=True):
                # JSON has no date and no decimal. Dates go out ISO, which is what the
                # drill-down link already expects; everything numeric goes out as a number.
                if isinstance(value, (dt.date, dt.datetime)):
                    rec[name] = value.isoformat()[:10]
                elif isinstance(value, Decimal):
                    rec[name] = float(value)
                else:
                    rec[name] = value
            out_rows.append(rec)
        return out_rows

    blocks = {d.name.removeprefix("panorama_"): rows(d.sql) for d in PANORAMA_DATASETS}
    target = out / "panorama.json"
    target.write_text(
        json.dumps(blocks, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log.info("panorama.json: %s blocks, %d bytes", len(blocks), target.stat().st_size)
    return {
        "file": "panorama.json",
        "bytes": target.stat().st_size,
        "blocuri": {k: len(v) for k, v in blocks.items()},
    }



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

        _register(con, {"achizitii_directe", "contracte"})
        con.execute(CONTRACTE_VIEW)
        con.execute(
            BASE_VIEW.format(
                thresholds=ceilings_by_category_sql(),
                numeric_label=NUMERIC_LABEL,
            )
        )
        con.execute(CPV_LABELS_VIEW)
        firme_cache = Path(ROOT) / "data" / "firme" / "anaf.parquet"
        if firme_cache.is_file():
            con.execute(
                "CREATE OR REPLACE VIEW firme_geo AS SELECT cui, judet FROM "
                f"read_parquet('{firme_cache}') WHERE judet IS NOT NULL"
            )
            con.execute(GEO_VIEW)
        buildable = [
            d for d in DATASETS
            if d.name != "judete_an" or firme_cache.is_file()
        ]
        manifest["datasets"] = [_write(con, d, out) for d in buildable]

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
        # The browser cannot discover these for itself. DuckDB-Wasm reads Parquet over
        # HTTP range requests, and a glob needs a directory listing, which HTTP does not
        # provide — verified against a real page:
        #
        #   read_parquet('.../preturi/**/*.parquet')  -> WebAssembly.Exception
        #   read_parquet(['.../a.parquet', '...'])    -> OK, 22,349 rows
        #
        # So every file that a reader may need has to be named here. Paths are relative
        # to PRICES_DIR and sorted, which makes them date-ordered: the file name is the
        # day, so the browser can pick just the days a group actually spans instead of
        # opening the whole archive.
        manifest["preturi_arhiva"] = {
            "baza": PRICES_DIR,
            "fisiere": [str(p.relative_to(out / PRICES_DIR)).replace("\\", "/") for p in prices],
        }
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

    # Supplier company facts from ANAF. Published as a profile table rather than
    # joined into the aggregates: it is a fact about a company, not about a purchase,
    # and it is refreshed on a different cadence from the procurement archive.
    #
    # This is the county backfill the archive has always needed — furnizor_localitate is
    # missing on 77% of rows, and ANAF answers for essentially every supplier it knows.
    firme_src = Path(ROOT) / "data" / "firme" / "anaf.parquet"
    if firme_src.is_file():
        con.execute(
            f"""COPY (SELECT cui, denumire, judet, cod_judet_auto, data_inregistrare,
                             inactiv, data_inactivare, data_reactivare, data_radiere,
                             platitor_tva, verificat_la
                      FROM read_parquet('{firme_src}') ORDER BY cui)
                TO '{out / "furnizori_profil.parquet"}'
                (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP})"""
        )
        rows, with_county, inactive, struck = con.execute(
            f"""SELECT count(*), count(judet), count(*) FILTER (WHERE inactiv),
                       count(*) FILTER (WHERE data_radiere IS NOT NULL)
                FROM read_parquet('{out / "furnizori_profil.parquet"}')"""
        ).fetchone()
        manifest["furnizori_profil"] = {
            "file": "furnizori_profil.parquet",
            "firme": rows,
            "cu_judet": with_county,
            "inactive_fiscal": inactive,
            "radiate": struck,
            "sursa": "ANAF — webservicesp.anaf.ro (serviciu public, fără cheie)",
            "nota": (
                "Coloana `inactiv` este starea la data verificării. Pentru a judeca o "
                "achiziție folosiți datele absolute — data_inactivare, data_reactivare, "
                "data_radiere — comparate cu data achiziției: o firmă inactivă azi "
                "putea fi perfect activă când a câștigat contractul."
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

    # The front door, rebuilt from what was just written. Also drops the Parquet entries
    # an earlier version of this wrote, so a bundle upgraded in place does not keep
    # advertising two files that are no longer produced.
    pan_names = {d.name for d in PANORAMA_DATASETS}
    manifest["datasets"] = [
        d for d in manifest.get("datasets", []) if d["name"] not in pan_names
    ]
    pan = _panorama(con, out)
    if pan:
        manifest["panorama"] = pan
    else:
        manifest.pop("panorama", None)
    for stale in pan_names:
        (out / f"{stale}.parquet").unlink(missing_ok=True)

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
            # Rewritten sorted, not copied. Every findings table carries autoritate_cui
            # and the entity file looks all of them up by it — the same reason the
            # aggregates are sorted. `shutil.copy2` left them in whatever order the
            # indicator produced, so a lookup had to read the file whole.
            cols = [
                d[0] for d in con.execute(
                    f"SELECT * FROM read_parquet('{src}') LIMIT 0"
                ).description
            ]
            key = "autoritate_cui" if "autoritate_cui" in cols else None
            if key:
                con.execute(
                    f"""COPY (SELECT * FROM read_parquet('{src}') ORDER BY {key})
                        TO '{dest / src.name}'
                        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP})"""
                )
            else:
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
            # The human name and description already exist on the Indicator; the site
            # was showing the bare id ("dependenta-01"), which means nothing to a reader
            # who has not read METHODOLOGY.md.
            meta = INDICATORS_BY_ID.get(src.stem)
            manifest["indicatori"].append(  # type: ignore[union-attr]
                {
                    "id": src.stem,
                    "file": f"indicatori/{src.name}",
                    "rows": rows,
                    "columns": columns,
                    "nume": meta.name_ro if meta else src.stem,
                    "descriere": meta.description_ro if meta else "",
                    "temei": meta.legal_basis if meta else "",
                }
            )
    else:
        log.warning("no findings directory; publishing without indicators")

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    con.close()
    return manifest
