"""Bulk ingest driver and indicator runner."""

from __future__ import annotations

import logging
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

INGEST_VERSION = 1


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
                    records, unmapped = govdata.to_records(
                        header, rows, res.table, res.name
                    )
                    del rows
                    if not records:
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
                log.info("%s [%s] -> %s (%d rows)", res.name, fmt, path.name, n)
                summary.append(
                    {"year": year, "table": res.table.key, "resource": res.name,
                     "format": fmt, "rows": n}
                )

    return {
        "ingest_version": INGEST_VERSION,
        "files": summary,
        "total_rows": sum(s.get("rows", 0) or 0 for s in summary),
        "unmapped_columns": {k: sorted(v) for k, v in unmapped_seen.items()},
    }


def _register(con: duckdb.DuckDBPyConnection, keys: set[str]) -> set[str]:
    """Expose each canonical table as a view over its per-year Parquet files."""
    available: set[str] = set()
    for key in keys:
        files = sorted(GOV_CORE.glob(f"{key}-*.parquet"))
        if not files:
            continue
        paths = ", ".join(f"'{f}'" for f in files)
        con.execute(f"CREATE OR REPLACE VIEW {key} AS SELECT * FROM read_parquet([{paths}])")
        available.add(key)
    return available


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

        params = dict(ind.params)
        if "$prag" in ind.sql:
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
