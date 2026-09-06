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
from datetime import UTC, date, datetime, timedelta
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


class AuthorityResolver:
    """Resolve contracting authority -> county. Cached; authorities repeat heavily."""

    def __init__(self, client: SeapClient) -> None:
        self._client = client
        self._cache: dict[int, dict[str, Any]] = {}

    def get(self, authority_id: int | None) -> dict[str, Any]:
        if not authority_id:
            return {}
        if authority_id not in self._cache:
            try:
                raw = self._client.get(f"/Entity/getCAEntityView/{authority_id}")
            except Exception as exc:  # noqa: BLE001 - a missing authority must not kill a run
                log.warning("authority %s lookup failed: %s", authority_id, exc)
                raw = {}
            clean = gdpr.scrub(raw or {})
            self._cache[authority_id] = {
                "county": clean.get("county"),
                "city": clean.get("city"),
                "fiscal_number": clean.get("fiscalNumber"),
                "is_utility": clean.get("isUtility"),
            }
        return self._cache[authority_id]


def fetch_day(
    client: SeapClient, day: date, limit: int | None = None
) -> list[dict[str, Any]]:
    """All direct acquisitions finalized on `day`, with line items, personal data stripped.

    The list endpoint caps `total` at 2000, so we page until a short page comes back
    rather than trusting the reported total. `limit` truncates for smoke tests.
    """
    iso = day.isoformat()
    summaries: list[dict[str, Any]] = []
    page = 0
    while True:
        payload = client.direct_acquisition_list(iso, page_index=page)
        items = payload.get("items") or []
        summaries.extend(items)
        if limit and len(summaries) >= limit:
            summaries = summaries[:limit]
            break
        if len(items) < config.LIST_PAGE_SIZE:
            break
        page += 1
        if page > 200:  # backstop; 100k records in one day would be anomalous
            log.warning("%s: stopped paging at page %d", iso, page)
            break

    log.info("%s: %d direct acquisitions", iso, len(summaries))

    details: list[dict[str, Any]] = []
    for summary in summaries:
        da_id = summary.get("directAcquisitionId")
        if not da_id:
            continue
        try:
            detail = client.direct_acquisition(da_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("detail %s failed: %s", da_id, exc)
            continue
        details.append(gdpr.scrub(_merge_summary(detail, summary)))
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
