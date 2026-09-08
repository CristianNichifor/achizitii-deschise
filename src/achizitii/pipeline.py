"""Ingest -> normalise -> Parquet.

Layers, deliberately separated so provenance survives:

  raw/      immutable, exactly what the API returned (minus personal data)
  core/     OCDS releases
  marts/    the analytical table the site queries

SEAP edits records silently, so `raw/` is append-only: it is the only way to prove
later what a record said on the day we read it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from . import config, gdpr, ocds
from .client import SeapClient
from .normalize import normalize_item

log = logging.getLogger(__name__)

# Bump whenever parsing or normalisation semantics change, so rows built by different
# logic are distinguishable and only affected rows need reprocessing.
#   1 — initial
#   2 — UN/CEFACT unit codes; bundles detected from description as well as unit
PARSE_VERSION = 2


def daterange(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


AUTHORITY_CACHE = Path(config.ROOT) / "data" / "autoritati.parquet"
"""Contracting authorities learned by previous runs.

These lookups are the second-largest cost in a run and the easiest to avoid paying twice.
A real weekday holds ~8,200 acquisitions across ~2,992 distinct authorities: the in-memory
cache already removes 64% of the calls, but the remaining 2,992 are sequential at 4 rps,
which is ~12 minutes of a ~45 minute day. Persisting them means the next run pays only for
authorities it has never seen, and a backfill that walks month after month converges on
paying nothing at all.

Safe to delete: a missing file just means the next run is cold. Delete it if an authority
is known to have been re-registered in another county.
"""


class AuthorityResolver:
    """Resolve contracting authority -> county.

    Cached twice over, because authorities repeat both within a day and across days.
    """

    def __init__(self, client: SeapClient, cache_path: Path | None = None) -> None:
        self._client = client
        self._cache: dict[int, dict[str, Any]] = {}
        self._path = Path(cache_path) if cache_path is not None else AUTHORITY_CACHE
        self._failed: set[int] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            import duckdb

            rows = duckdb.connect().execute(
                "SELECT authority_id, county, city, fiscal_number, is_utility"
                f" FROM read_parquet('{self._path}')"
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - a bad cache must never stop a run
            log.warning("authority cache unreadable (%s); starting cold", exc)
            return
        for aid, county, city, fiscal, utility in rows:
            self._cache[int(aid)] = {
                "county": county,
                "city": city,
                "fiscal_number": fiscal,
                "is_utility": utility,
            }
        log.info("authority cache: %d known before this run", len(self._cache))

    def get(self, authority_id: int | None) -> dict[str, Any]:
        if not authority_id:
            return {}
        if authority_id not in self._cache:
            try:
                raw = self._client.get(f"/Entity/getCAEntityView/{authority_id}")
            except Exception as exc:  # noqa: BLE001 - a missing authority must not kill a run
                log.warning("authority %s lookup failed: %s", authority_id, exc)
                raw = {}
                # Remember the failure so save() does not write it down. A cached empty
                # row would turn one timeout into a permanently county-less authority,
                # because nothing would ever look it up again.
                self._failed.add(authority_id)
            clean = gdpr.scrub(raw or {})
            fiscal = clean.get("fiscalNumber")
            utility = clean.get("isUtility")
            self._cache[authority_id] = {
                "county": clean.get("county"),
                "city": clean.get("city"),
                # Normalised so a value read back from the cache is indistinguishable
                # from a freshly fetched one. Provenance must not change a type.
                "fiscal_number": None if fiscal is None else str(fiscal),
                "is_utility": None if utility is None else bool(utility),
            }
        return self._cache[authority_id]

    def save(self) -> int:
        """Write successfully resolved authorities back to the cache. Returns the count."""
        keep = {k: v for k, v in self._cache.items() if k not in self._failed}
        if not keep:
            return 0
        import duckdb

        self._path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        con.execute(
            """CREATE TABLE a (authority_id BIGINT, county VARCHAR, city VARCHAR,
                               fiscal_number VARCHAR, is_utility BOOLEAN)"""
        )
        con.executemany(
            "INSERT INTO a VALUES (?,?,?,?,?)",
            [
                [k, v["county"], v["city"], v["fiscal_number"], v["is_utility"]]
                for k, v in sorted(keep.items())
            ],
        )
        con.execute(
            f"COPY (SELECT * FROM a ORDER BY authority_id) TO '{self._path}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.close()
        return len(keep)


def archived_ids(day: date) -> set[int]:
    """Acquisition ids already in the price archive for `day`.

    This is what makes a frequent schedule affordable. Without it every run re-fetches
    the whole day so far — by evening that is ~8,300 detail calls to re-learn what we
    already knew, and twenty-four of those a day does not fit in the free minutes. With
    it, a run costs only what has appeared since the last one.

    Missing or unreadable archive means "fetch everything": doing extra work is the
    right failure, silently skipping records is not.
    """
    target = (
        Path(config.ROOT) / "site" / "data" / "preturi"
        / f"an={day.year}" / f"luna={day.month:02d}" / f"{day.isoformat()}.parquet"
    )
    if not target.is_file():
        return set()
    try:
        import duckdb

        con = duckdb.connect()
        rows = con.execute(
            f"SELECT DISTINCT ocid FROM read_parquet('{target}')"
        ).fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the archive for %s (%s); fetching everything", day, exc)
        return set()
    ids: set[int] = set()
    for (ocid,) in rows:
        _, _, tail = str(ocid or "").rpartition("-da-")
        if tail.isdigit():
            ids.add(int(tail))
    return ids


def list_day(
    client: SeapClient, day: date, limit: int | None = None
) -> list[dict[str, Any]]:
    """Every direct acquisition finalised on `day`, gathered category by category.

    The list endpoint hard-caps a query at 2,000 records, sorted by finalisation time
    ascending, and paging stops there — so a single query for a weekday returned the
    morning's acquisitions and silently dropped the afternoon's. Measured on
    2026-06-24: 2,000 returned against 8,940 that actually exist.

    Splitting by CPV category recovers 92.6% of that day (8,272). It is not perfect —
    one category can still exceed 2,000 on its own — so a category that comes back at
    exactly the cap is logged as truncated rather than quietly accepted.
    """
    iso = day.isoformat()
    seen: dict[int, dict[str, Any]] = {}
    truncated: list[int] = []

    for category in config.CPV_CATEGORY_IDS:
        got = 0
        page = 0
        while True:
            payload = client.direct_acquisition_list(
                iso, page_index=page, cpv_category_id=category
            )
            items = payload.get("items") or []
            for it in items:
                da_id = it.get("directAcquisitionId")
                # Categories are disjoint in principle; de-duplicate anyway rather than
                # trust that, because a double-counted acquisition is indistinguishable
                # from a real one downstream.
                if da_id and da_id not in seen:
                    seen[da_id] = it
            got += len(items)
            if len(items) < config.LIST_PAGE_SIZE:
                break
            page += 1
            if got >= config.LIST_CAP:
                break
        if got >= config.LIST_CAP:
            truncated.append(category)
        if limit and len(seen) >= limit:
            break

    if truncated:
        log.warning(
            "%s: CPV categories %s hit the %d-record cap; that part of the day is "
            "incomplete", iso, truncated, config.LIST_CAP,
        )
    log.info("%s: %d direct acquisitions across %d categories",
             iso, len(seen), len(config.CPV_CATEGORY_IDS))

    summaries = list(seen.values())
    return summaries[:limit] if limit else summaries


def fetch_day(
    client: SeapClient, day: date, limit: int | None = None
) -> list[dict[str, Any]]:
    """Acquisitions for `day` with their line items, personal data stripped.

    Details are fetched concurrently. One call per acquisition is unavoidable — the list
    carries no line items and no bulk export of them exists anywhere — so the only lever
    is how many run at once. See config.MAX_RPS for the measurement behind the number.
    """
    summaries = list_day(client, day, limit=limit)

    # Skip what a previous run already archived for this day.
    done = archived_ids(day)
    if done:
        before = len(summaries)
        summaries = [
            s for s in summaries if s.get("directAcquisitionId") not in done
        ]
        log.info(
            "%s: %d already archived, fetching %d new",
            day.isoformat(), before - len(summaries), len(summaries),
        )

    details: list[dict[str, Any]] = []

    def fetch_one(summary: dict[str, Any]) -> dict[str, Any] | None:
        da_id = summary.get("directAcquisitionId")
        if not da_id:
            return None
        try:
            detail = client.direct_acquisition(da_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("detail %s failed: %s", da_id, exc)
            return None
        return gdpr.scrub(_merge_summary(detail, summary))

    with ThreadPoolExecutor(max_workers=config.DETAIL_WORKERS) as pool:
        for result in pool.map(fetch_one, summaries):
            if result is not None:
                details.append(result)

    if len(details) < len(summaries):
        log.info("%s: %d of %d details retrieved", day.isoformat(), len(details), len(summaries))
    return details


# The detail endpoint returns only `contractingAuthorityID` / `supplierId`; the human
# readable "CUI Name" strings exist only on the list response. Carry them across, or
# every record loses its buyer and supplier.
_SUMMARY_FIELDS = (
    "contractingAuthority",
    "supplier",
    "uniqueIdentificationCode",
    "cpvCode",
    "sysDirectAcquisitionState",
    "estimatedValueRon",
)


def _merge_summary(detail: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(detail)
    for field in _SUMMARY_FIELDS:
        if merged.get(field) in (None, "") and summary.get(field) is not None:
            merged[field] = summary[field]
    return merged


def write_raw(details: list[dict[str, Any]], day: date) -> str:
    """Append-only raw archive. Returns the sha256 of the file for the manifest."""
    config.RAW.mkdir(parents=True, exist_ok=True)
    path = config.RAW / f"da-{day.isoformat()}.jsonl.gz"
    blob = "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in details)
    path.write_bytes(gzip.compress(blob.encode("utf-8")))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_ocds(details: list[dict[str, Any]], day: date) -> None:
    config.CORE.mkdir(parents=True, exist_ok=True)
    releases = [ocds.direct_acquisition_to_release(d) for d in details]
    package = ocds.release_package(releases, datetime.now(UTC).isoformat())
    path = config.CORE / f"releases-{day.isoformat()}.json.gz"
    path.write_bytes(gzip.compress(json.dumps(package, ensure_ascii=False).encode("utf-8")))


ITEM_SCHEMA = pa.schema(
    [
        ("ocid", pa.string()),
        ("da_id", pa.int64()),
        ("item_id", pa.string()),
        ("an", pa.int16()),
        ("data_finalizare", pa.timestamp("s")),
        ("judet", pa.string()),
        ("localitate", pa.string()),
        ("autoritate_cui", pa.string()),
        ("autoritate_nume", pa.string()),
        ("furnizor_cui", pa.string()),
        ("furnizor_nume", pa.string()),
        ("cpv", pa.string()),
        ("cpv_divizie", pa.string()),
        ("tip_contract", pa.string()),
        ("denumire", pa.string()),
        ("denumire_key", pa.string()),
        ("cantitate", pa.float64()),
        ("um_brut", pa.string()),
        ("um", pa.string()),
        ("um_uncefact", pa.string()),
        ("dimensiune", pa.string()),
        ("marime_pachet", pa.int32()),
        ("pret_unitar_ron", pa.float64()),
        ("valoare_linie_ron", pa.float64()),
        ("comparabil", pa.bool_()),
        ("motiv_necomparabil", pa.string()),
        ("parse_version", pa.int16()),
    ]
)


def to_rows(
    details: list[dict[str, Any]], authorities: AuthorityResolver
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for detail in details:
        da_id = detail.get("directAcquisitionID") or detail.get("directAcquisitionId")
        authority = authorities.get(detail.get("contractingAuthorityID"))
        ca_cui, ca_name = ocds.split_org(detail.get("contractingAuthority"))
        sup_cui, sup_name = ocds.split_org(detail.get("supplier"))

        contract_type = (detail.get("sysAcquisitionContractType") or {}).get("text")
        finalized_raw = detail.get("finalizationDate") or detail.get("publicationDate") or ""
        try:
            finalized = datetime.fromisoformat(finalized_raw).replace(tzinfo=None)
        except ValueError:
            finalized = None

        for raw_item in detail.get("directAcquisitionItems") or []:
            cpv_code, _ = ocds.parse_cpv(raw_item.get("cpvCode"))
            price = raw_item.get("itemClosingPrice")
            if price is None:
                price = raw_item.get("itemEstimatedPrice")

            norm = normalize_item(
                description=raw_item.get("catalogItemName") or "",
                long_description=raw_item.get("catalogItemDescription"),
                cpv=cpv_code,
                quantity=raw_item.get("itemQuantity"),
                unit_raw=raw_item.get("itemMeasureUnit"),
                unit_price_ron=price,
            )

            rows.append(
                {
                    "ocid": f"{config.OCID_PREFIX}-da-{da_id}",
                    "da_id": int(da_id) if da_id else None,
                    "item_id": str(raw_item.get("directAcquisitionItemID") or ""),
                    "an": finalized.year if finalized else None,
                    "data_finalizare": finalized,
                    "judet": authority.get("county"),
                    "localitate": authority.get("city"),
                    "autoritate_cui": ca_cui,
                    "autoritate_nume": ca_name,
                    "furnizor_cui": sup_cui,
                    "furnizor_nume": sup_name,
                    "cpv": cpv_code,
                    "cpv_divizie": cpv_code[:2] if cpv_code else None,
                    "tip_contract": contract_type,
                    "denumire": norm.description,
                    "denumire_key": norm.description_key,
                    "cantitate": norm.quantity,
                    "um_brut": norm.unit_raw,
                    "um": norm.unit,
                    "um_uncefact": norm.unit_uncefact,
                    "dimensiune": norm.dimension,
                    "marime_pachet": norm.pack_size,
                    "pret_unitar_ron": norm.unit_price_ron,
                    "valoare_linie_ron": norm.line_total_ron,
                    "comparabil": norm.comparable,
                    "motiv_necomparabil": norm.incomparable_reason,
                    "parse_version": PARSE_VERSION,
                }
            )
    return rows


def write_parquet(rows: list[dict[str, Any]], out_dir: str | None = None) -> str:
    """Sorted by (cpv, judet) so Parquet row-group statistics can skip most of the file."""
    target = config.SITE_DATA / "items" if out_dir is None else out_dir
    table = pa.Table.from_pylist(rows, schema=ITEM_SCHEMA)
    table = table.sort_by([("cpv", "ascending"), ("judet", "ascending"), ("data_finalizare", "ascending")])
    pq.write_to_dataset(
        table,
        root_path=str(target),
        partition_cols=["an"],
        compression="zstd",
        compression_level=9,
        row_group_size=100_000,
        use_dictionary=True,
        write_statistics=True,
        existing_data_behavior="overwrite_or_ignore",
    )
    return str(target)


def run(
    start: date, end: date, skip_raw: bool = False, limit: int | None = None
) -> dict[str, Any]:
    """Fetch a date range and produce raw + OCDS + Parquet."""
    all_rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []

    with SeapClient() as client:
        authorities = AuthorityResolver(client)
        for day in daterange(start, end):
            details = fetch_day(client, day, limit=limit)
            if not details:
                continue
            entry: dict[str, Any] = {"date": day.isoformat(), "records": len(details)}
            if not skip_raw:
                entry["raw_sha256"] = write_raw(details, day)
            write_ocds(details, day)
            rows = to_rows(details, authorities)
            all_rows.extend(rows)
            entry["items"] = len(rows)
            manifest.append(entry)
            # Saved per day, not once at the end: the backfill job is expected to stop on
            # its time budget mid-range, and a run that dies must not throw away the
            # authorities it just paid for.
            entry["authorities_cached"] = authorities.save()
            log.info("%s: %d items", day.isoformat(), len(rows))

    out = write_parquet(all_rows) if all_rows else None
    comparable = sum(1 for r in all_rows if r["comparabil"])
    return {
        "days": manifest,
        "items": len(all_rows),
        "comparable_items": comparable,
        "parquet": out,
        "parse_version": PARSE_VERSION,
    }
