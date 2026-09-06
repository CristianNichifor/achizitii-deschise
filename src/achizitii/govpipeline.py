"""Bulk ingest driver and indicator runner."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import govdata
from .config import DATA

log = logging.getLogger(__name__)

GOV_RAW = DATA / "gov_raw"
GOV_CORE = DATA / "gov"
FINDINGS = DATA / "findings"
PROPOSALS = DATA / "proposals"

INGEST_VERSION = 1

EMPTY_PARSE_ALARM_BYTES = 100_000
"""Above this size, a file that parses to zero rows is treated as an error."""


def check_coverage(years: list[int]) -> dict[str, Any]:
    """Report, per year, which tables were matched and what went unclassified.

    Resource names drift constantly ("Anunturi participare" in 2017, "Anunțuri
    inițiere" in 2020, "Anunturi de initiere publicate" in 2023). A regex that stops
    matching does not raise — the table just quietly vanishes for those years, which is
    how `initiere` was lost for four of them. This makes the gap visible without
    downloading anything.
    """
    report: list[dict[str, Any]] = []
    missing_any: set[str] = set()

    with govdata.make_client() as client:
        for year in years:
            try:
                found = govdata.discover(year, client)
            except Exception as exc:  # noqa: BLE001
                report.append({"year": year, "error": str(exc)[:200]})
                continue

            by_table: dict[str, int] = {}
            for r in found:
                by_table[r.table.key] = by_table.get(r.table.key, 0) + 1
            absent = sorted(set(govdata.TABLES_BY_KEY) - set(by_table))
            missing_any.update(absent)
            report.append(
                {
                    "year": year,
                    "matched": dict(sorted(by_table.items())),
                    "tables_absent": absent,
                }
            )

    return {
        "years": report,
        "tables_absent_somewhere": sorted(missing_any),
    }


def _write_resource(
    records: list[dict[str, Any]], res: govdata.Resource
) -> Path:
    """One Parquet file per source resource (i.e. per quarter).

    Deliberately not one file per year: a year of direct acquisitions is over a million
    rows, and holding that as Python dicts before writing exhausts memory long before a
    2016-2026 backfill finishes. Per-resource files also make the ingest resumable and
    keep one bad quarter from losing the whole year.
    """
    spec = res.table
    cols = [*spec.columns, "sursa"]
    table = pa.table(
        {c: pa.array([r.get(c) for r in records], type=pa.string()) for c in cols}
    )
    table = table.append_column(
        "an", pa.array([res.year] * len(records), type=pa.int16())
    )
    out = GOV_CORE / f"{spec.key}-{res.slug}.parquet"
    pq.write_table(table, out, compression="zstd", compression_level=9)
    return out


def ingest_years(
    years: list[int], tables: list[str] | None = None, force: bool = False
) -> dict[str, Any]:
    """Download, parse and normalise the quarterly exports for the given years.

    All columns are stored as text; casting happens in SQL, so a stray value in one year
    cannot fail the whole ingest. Already-written resources are skipped unless `force`.
    """
    GOV_CORE.mkdir(parents=True, exist_ok=True)
    wanted = set(tables) if tables else set(govdata.TABLES_BY_KEY)
    summary: list[dict[str, Any]] = []
    unmapped_seen: dict[str, set[str]] = {}
    missing_seen: dict[str, set[str]] = {}

    with govdata.make_client() as client:
        for year in years:
            # Discovery sits outside the per-resource guard below, so without its own
            # handler one unreachable year aborts an entire multi-year backfill.
            try:
                found = govdata.discover(year, client)
            except Exception as exc:  # noqa: BLE001
                log.warning("%d: discovery FAILED (%s)", year, exc)
                summary.append({"year": year, "rows": 0, "error": f"discovery: {exc}"[:200]})
                continue

            resources = [r for r in found if r.table.key in wanted]
            if not resources:
                log.warning("%d: no matching resources", year)
                continue

            for res in resources:
                out = GOV_CORE / f"{res.table.key}-{res.slug}.parquet"
                if out.exists() and not force:
                    log.info("%s: already ingested, skipping", res.name)
                    summary.append(
                        {"year": year, "table": res.table.key, "resource": res.name,
                         "skipped": "already ingested"}
                    )
                    continue
                try:
                    blob = govdata.fetch(res, GOV_RAW, client)
                    fmt = govdata.sniff(blob)
                    header, rows = govdata.read_table(blob)
                    header, rows = govdata.realign_header(header, rows, res.table)
                    records, unmapped, malformed = govdata.to_records(
                        header, rows, res.table, res.name
                    )
                    absent = govdata.missing_columns(header, res.table)
                    del rows
                    if not records:
                        # A substantial file that yields nothing is a parse failure, not
                        # an empty dataset. Recording it as an error rather than a
                        # warning is what makes the run fail instead of quietly
                        # continuing — 2019-2020 direct acquisitions were lost this way.
                        if len(blob) > EMPTY_PARSE_ALARM_BYTES:
                            msg = f"parsed 0 rows from {len(blob):,} bytes"
                            log.error("%s: %s", res.name, msg)
                            summary.append(
                                {"year": year, "table": res.table.key,
                                 "resource": res.name, "rows": 0, "error": msg}
                            )
                        else:
                            log.warning("%s: no rows", res.name)
                        continue
                    path = _write_resource(records, res)
                    n = len(records)
                    del records
                except Exception as exc:  # noqa: BLE001 — one bad file must not stop a backfill
                    log.warning("%s: FAILED (%s)", res.name, exc)
                    summary.append(
                        {"year": year, "table": res.table.key, "resource": res.name,
                         "rows": 0, "error": str(exc)[:200]}
                    )
                    continue

                if unmapped:
                    unmapped_seen.setdefault(res.table.key, set()).update(unmapped)
                if absent:
                    # A canonical field with no source column becomes a column of NULLs,
                    # not an error. Surface it or it hides, as it did for 2021.
                    log.warning("%s: no source column for %s", res.name, absent)
                    missing_seen.setdefault(res.table.key, set()).update(absent)
                log.info("%s [%s] -> %s (%d rows)", res.name, fmt, path.name, n)
                entry = {"year": year, "table": res.table.key, "resource": res.name,
                         "format": fmt, "rows": n}
                if malformed:
                    # Field-count mismatch: an unquoted delimiter inside a description
                    # shifted every later column. Dropped rather than realigned.
                    log.warning("%s: dropped %d malformed rows", res.name, malformed)
                    entry["malformed_rows"] = malformed
                if absent:
                    entry["columns_absent"] = absent
                summary.append(entry)

    return {
        "ingest_version": INGEST_VERSION,
        "files": summary,
        "total_rows": sum(s.get("rows", 0) or 0 for s in summary),
        "unmapped_columns": {k: sorted(v) for k, v in unmapped_seen.items()},
        "columns_absent": {k: sorted(v) for k, v in missing_seen.items()},
    }


# Derived at query time rather than at ingest, so the whole archive does not need
# reprocessing when the rule changes. Mirrors normalize.contract_category.
_CPV_DIVISION = "TRY_CAST(substr(regexp_replace(cpv, '[^0-9]', '', 'g'), 1, 2) AS INTEGER)"

CATEGORIE_SQL = f"""
CASE
  WHEN lower(coalesce(tip_contract, '')) IN ('furnizare', 'produse') THEN 'furnizare'
  WHEN lower(coalesce(tip_contract, '')) = 'servicii'                THEN 'servicii'
  WHEN lower(coalesce(tip_contract, '')) IN ('lucrari', 'lucrări')   THEN 'lucrari'
  WHEN {_CPV_DIVISION} = 45                                          THEN 'lucrari'
  WHEN {_CPV_DIVISION} >= 50                                         THEN 'servicii'
  WHEN {_CPV_DIVISION} >= 3                                          THEN 'furnizare'
END AS categorie
"""

# Tables that gain the derived category column.
_DERIVED: dict[str, str] = {
    "achizitii_directe": CATEGORIE_SQL,
    "contracte": CATEGORIE_SQL,
    "fara_anunt": CATEGORIE_SQL,
}


def _register(con: duckdb.DuckDBPyConnection, keys: set[str]) -> set[str]:
    """Expose each canonical table as a view over its per-year Parquet files.

    `achizitii_directe` and friends gain a derived `categorie`, because the declared
    `tip_contract` is only usable from 2022 onward — earlier exports put the procedure
    ("Cumparare directa") in that column, so filtering on it silently excluded five
    years.
    """
    available: set[str] = set()
    for key in keys:
        files = sorted(GOV_CORE.glob(f"{key}-*.parquet"))
        if not files:
            continue
        paths = ", ".join(f"'{f}'" for f in files)
        extra = _DERIVED.get(key)
        select = f"*, {extra}" if extra else "*"
        con.execute(
            f"CREATE OR REPLACE VIEW {key} AS SELECT {select} FROM read_parquet([{paths}])"
        )
        available.add(key)
    return available


# A column populated in neighbouring years but empty in one is a mapping failure, not a
# fact about the world. This is the check that would have caught 2021 automatically.
NULL_RATE_ALARM = 0.98
"""A column this empty in a year is treated as lost, not sparse."""

NULL_RATE_HEALTHY = 0.50
"""...but only when a neighbouring year has it at least this populated."""


def validate() -> dict[str, Any]:
    """Cross-year sanity checks on the ingested tables.

    Every data bug in this project so far has been silent: an unmatched alias yields a
    column of NULLs, a renamed resource yields a missing year, a mistyped slug yields
    nothing at all. None of them raise. This compares each column against its own
    history, where such failures are obvious.
    """
    con = duckdb.connect()
    available = _register(con, set(govdata.TABLES_BY_KEY))
    problems: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    tables: dict[str, Any] = {}

    for key in sorted(available):
        spec = govdata.TABLES_BY_KEY[key]
        cols = [c for c in spec.columns]
        exprs = ", ".join(
            f"avg(({c} IS NULL)::INT) AS {c}" for c in cols
        )
        rows = con.execute(
            f"SELECT an, count(*) AS n, {exprs} FROM {key} GROUP BY an ORDER BY an"
        ).fetch_arrow_table().to_pylist()
        tables[key] = rows

        # Per column, compare each year's null rate against the best year available.
        for col in cols:
            rates = {r["an"]: r[col] for r in rows if r[col] is not None}
            if not rates:
                continue
            best = min(rates.values())
            if best > NULL_RATE_HEALTHY:
                continue  # never populated anywhere — a genuine absence, not a bug
            for year, rate in sorted(rates.items()):
                if rate < NULL_RATE_ALARM:
                    continue
                known = govdata.is_known_gap(key, col, year)
                record = {
                    "table": key,
                    "column": col,
                    "year": year,
                    "null_rate": round(rate, 4),
                    "best_year_null_rate": round(best, 4),
                }
                if known:
                    # A documented publishing gap, not a defect. Reported so the
                    # limitation stays visible, but it must not fail the check.
                    record["known_gap"] = known["reason"]
                    expected.append(record)
                else:
                    record["note"] = (
                        "column empty this year but populated in others — "
                        "likely an unmatched header alias"
                    )
                    problems.append(record)

    # The derived category must resolve for essentially every direct acquisition; if it
    # does not, both the declared type and the CPV are unusable for those rows.
    if "achizitii_directe" in available:
        cat = con.execute(
            "SELECT an, count(*) n, avg((categorie IS NULL)::INT) unresolved "
            "FROM achizitii_directe GROUP BY an ORDER BY an"
        ).fetch_arrow_table().to_pylist()
        tables["categorie_unresolved"] = cat
        for row in cat:
            if row["unresolved"] and row["unresolved"] > 0.05:
                problems.append(
                    {
                        "table": "achizitii_directe",
                        "column": "categorie",
                        "year": row["an"],
                        "null_rate": round(row["unresolved"], 4),
                        "note": "category unresolved from both tip_contract and CPV",
                    }
                )

    # Rows whose award dwarfs the estimate are source-data errors rather than
    # procurement decisions — typically a row with a MISSING field, which shifts columns
    # left and is invisible to the malformed-row check (that only catches rows with too
    # MANY fields). Counted here so their exclusion from findings is visible.
    if "contracte" in available:
        try:
            suspect = con.execute(
                """
                SELECT an, count(*) n, round(max(
                         TRY_CAST(valoare_ron AS DOUBLE)
                         / nullif(TRY_CAST(valoare_estimata_ron AS DOUBLE), 0)), 1) max_ratio
                FROM contracte
                WHERE TRY_CAST(valoare_estimata_ron AS DOUBLE) > 0
                  AND TRY_CAST(valoare_ron AS DOUBLE)
                      > 5 * TRY_CAST(valoare_estimata_ron AS DOUBLE)
                GROUP BY an ORDER BY an
                """
            ).fetch_arrow_table().to_pylist()
        except duckdb.Error:
            suspect = []
        if suspect:
            tables["implausible_award_vs_estimate"] = suspect

    con.close()
    return {
        "tables": tables,
        "problems": problems,
        "known_gaps": expected,
        "ok": not problems,
    }


def ceilings_report(years: list[int] | None = None) -> dict[str, Any]:
    """Detected cliff and declared-threshold corroboration, per year.

    Two distinct questions, deliberately kept apart:

    - `detected` scans for the sharpest density cliff, which locates the ceiling
      without reference to any legal text. It cannot pin an exact value, because every
      candidate above the true ceiling scores identically.
    - `corroborated` tests a SPECIFIC declared figure by asking what share of records
      exceed it. That is what validates a legal value.

    A ceiling that visibly moves in the year a threshold changed is far stronger
    evidence than either measure in a single year.
    """
    from .indicators import (
        CORROBORATION_MAX_EXCEEDANCE_PCT,
        corroborate_ceiling_sql,
        detect_ceiling_sql,
        threshold_for,
    )

    con = duckdb.connect()
    if "achizitii_directe" not in _register(con, {"achizitii_directe"}):
        con.close()
        return {"detected": [], "corroborated": [], "note": "no achizitii_directe data"}

    detected = con.execute(detect_ceiling_sql()).fetch_arrow_table().to_pylist()

    corroborated: list[dict[str, Any]] = []
    for category in ("goods_services", "works"):
        prag = threshold_for(date(2026, 1, 1), category)
        if prag is None:
            corroborated.append(
                {"category": category, "skipped": "no verified declared threshold"}
            )
            continue
        rows = con.execute(
            corroborate_ceiling_sql(category), {"prag": prag}
        ).fetch_arrow_table().to_pylist()
        for row in rows:
            if years and row["an"] not in years:
                continue
            row["category"] = category
            row["declared_prag"] = prag
            # The declared figure only applies from 2023; earlier years are reported
            # for shape, not as a pass/fail.
            row["applicable"] = row["an"] >= 2023
            row["corroborated"] = bool(
                row["applicable"]
                and row["pct_peste"] is not None
                and row["pct_peste"] < CORROBORATION_MAX_EXCEEDANCE_PCT
            )
            corroborated.append(row)

    con.close()
    return {"detected": detected, "corroborated": corroborated}


def candidate_keys(cpv_prefix: str | None = None, limit: int = 40) -> list[str]:
    """Product keys that deterministic grouping left as singletons.

    These are where a synonym table would actually help: everything that grouped
    cleanly needs no model.
    """
    from .produs import product_key

    con = duckdb.connect()
    if "achizitii_directe" not in _register(con, {"achizitii_directe"}):
        con.close()
        return []
    where = "AND cpv LIKE ?" if cpv_prefix else ""
    params = [f"{cpv_prefix}%"] if cpv_prefix else []
    rows = con.execute(
        f"SELECT denumire, cpv FROM achizitii_directe "
        f"WHERE denumire IS NOT NULL AND cpv IS NOT NULL {where} LIMIT 20000",
        params,
    ).fetchall()
    con.close()

    counts: dict[str, int] = {}
    for denumire, cpv in rows:
        key = product_key(denumire, cpv).key
        if key:
            counts[key] = counts.get(key, 0) + 1
    return [k for k, n in sorted(counts.items()) if n == 1][:limit]


def propose_clusters(
    cpv_prefix: str | None = None, limit: int = 40, dry_run: bool = False
) -> dict[str, Any]:
    """Ask a model to cluster leftover keys, and validate every key it returns.

    Output is a proposal for review, never applied. See `achizitii.cluster`.
    """
    from .cluster import (
        InferenceClient,
        InferenceUnavailable,
        build_prompt,
        parse_proposals,
    )

    keys = candidate_keys(cpv_prefix, limit)
    if not keys:
        return {"keys": 0, "note": "no singleton keys found — nothing to cluster"}

    prompt = build_prompt(keys)
    if dry_run:
        return {
            "dry_run": True,
            "keys": len(keys),
            "candidates": keys,
            "prompt_chars": len(prompt),
            "note": "no API call made",
        }

    client = InferenceClient.from_env()
    try:
        reply = client.chat(prompt)
    except InferenceUnavailable as exc:
        # Expected, not exceptional: the provider states availability is not guaranteed.
        # Nothing downstream depends on this, so the run reports and stops cleanly.
        return {"keys": len(keys), "unavailable": str(exc), "model": client.model}

    proposals, rejections = parse_proposals(reply, set(keys))
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    out = PROPOSALS / f"clusters-{cpv_prefix or 'all'}.json"
    out.write_text(
        json.dumps(
            {
                "model": client.model,
                "candidates": keys,
                "proposals": [
                    {"eticheta": p.label, "chei": list(p.keys), "motiv": p.reason}
                    for p in proposals
                ],
                "rejected": rejections,
                "status": "AWAITING HUMAN REVIEW - not used by any pipeline",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "keys": len(keys),
        "proposals": len(proposals),
        "rejected": len(rejections),
        "output": str(out),
        "model": client.model,
    }


def detect_ceilings() -> list[dict[str, Any]]:
    """Recover the direct-acquisition ceiling in force each year, from the data.

    Preferred over the legal text: freely available consolidations of Legea 98/2016
    disagree with each other, and a wrong ceiling invents findings. Because exceeding
    the ceiling is unlawful, the value distribution collapses at it, and that cliff is
    unambiguous when present.
    """
    from .indicators import detect_ceiling_sql

    con = duckdb.connect()
    if "achizitii_directe" not in _register(con, {"achizitii_directe"}):
        con.close()
        return []
    rows = con.execute(detect_ceiling_sql()).fetch_arrow_table().to_pylist()
    con.close()
    return rows


def _unusable_columns(con: duckdb.DuckDBPyConnection, ind: Any) -> list[str]:
    """Required columns that do not exist, or exist but are entirely NULL."""
    unusable: list[str] = []
    for table in ind.applies_to:
        for col in ind.requires_columns:
            try:
                populated = con.execute(
                    f"SELECT count({col}) FROM {table}"
                ).fetchone()[0]
            except duckdb.Error:
                unusable.append(f"{table}.{col} (absent)")
                continue
            if not populated:
                unusable.append(f"{table}.{col} (all NULL)")
    return unusable


def run_indicators(
    only: list[str] | None = None, on_date: date | None = None, limit: int = 200
) -> dict[str, Any]:
    """Execute indicators against the ingested bulk data and write findings to Parquet."""
    from .indicators import INDICATORS, INDICATORS_BY_ID, threshold_for

    # Rule validity is evaluated in Romanian local time, matching the legal calendar
    # the thresholds are defined against.
    day = on_date or datetime.now(ZoneInfo("Europe/Bucharest")).date()
    selected = (
        [INDICATORS_BY_ID[i] for i in only] if only else list(INDICATORS)
    )

    FINDINGS.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    needed = {k for ind in selected for k in ind.applies_to}
    available = _register(con, needed)

    results: list[dict[str, Any]] = []
    for ind in selected:
        if not ind.active_on(day):
            results.append({"indicator": ind.identifier, "skipped": "outside validity dates"})
            continue
        missing = set(ind.applies_to) - available
        if missing:
            results.append(
                {"indicator": ind.identifier, "skipped": f"missing data: {sorted(missing)}"}
            )
            continue

        # A required column that is absent or entirely NULL means "no data", not
        # "no findings". Reporting zero results for those is how an empty pipeline
        # passes for a clean one.
        unusable = _unusable_columns(con, ind)
        if unusable:
            results.append(
                {
                    "indicator": ind.identifier,
                    "skipped": f"required column(s) absent or empty: {unusable}",
                }
            )
            continue

        params = dict(ind.params)
        # Word-boundary match: a plain substring test also fires on "$prag_raport",
        # injecting a parameter the query never binds and failing the whole indicator.
        if re.search(r"\$prag\b", ind.sql):
            prag = threshold_for(day, "goods_services")
            if prag is None:
                results.append(
                    {"indicator": ind.identifier,
                     "skipped": "no verified legal threshold for this date"}
                )
                continue
            params["prag"] = prag

        try:
            rel = con.execute(ind.sql, params) if params else con.execute(ind.sql)
            # `.arrow()` yields a RecordBatchReader on current DuckDB; we need a Table.
            arrow = rel.fetch_arrow_table()
        except Exception as exc:  # noqa: BLE001
            results.append({"indicator": ind.identifier, "error": str(exc)[:300]})
            continue

        out = FINDINGS / f"{ind.identifier}.parquet"
        pq.write_table(arrow, out, compression="zstd")
        results.append(
            {
                "indicator": ind.identifier,
                "name": ind.name_ro,
                "legal_basis": ind.legal_basis,
                "findings": arrow.num_rows,
                "output": str(out),
                "sample": arrow.slice(0, min(limit, 5)).to_pylist(),
            }
        )
        log.info("%s: %d findings", ind.identifier, arrow.num_rows)

    # Ceilings recovered from the data, reported alongside the findings so the declared
    # threshold can be checked against what the distribution actually shows.
    ceilings: list[dict[str, Any]] = []
    if "achizitii_directe" in available:
        from .indicators import detect_ceiling_sql

        try:
            ceilings = con.execute(detect_ceiling_sql()).fetch_arrow_table().to_pylist()
        except Exception as exc:  # noqa: BLE001
            log.warning("ceiling detection failed: %s", exc)

    con.close()
    return {
        "date": day.isoformat(),
        "detected_ceilings": ceilings,
        "results": results,
    }
