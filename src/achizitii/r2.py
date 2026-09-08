"""Publishing the bundle to Cloudflare R2 instead of committing it to git.

WHY

The published bundle outgrew where it lives. GitHub Pages caps a site at 1 GB and the
full line-item history is about 2.3 GB; worse, every day committed to git stays in the
history forever, so the repository grows ~115 MB a year and never shrinks. R2 gives 10 GB
free, charges nothing for egress, and decouples data volume from repository size.

The site itself stays on Pages. Only the data moves. If R2 is misconfigured or down, the
page still loads and says so, rather than the whole thing disappearing.

MONTHLY FILES, NOT DAILY — THIS IS THE COST DECISION

DuckDB-Wasm reads Parquet over HTTP range requests, and every request is a Class B
operation against a 10 million per month allowance. File COUNT therefore drives cost far
more than file size:

    one file per day    4,015 files  ~12,000 ops per full scan     830 scans/month
    one file per month    132 files      ~400 ops per full scan  25,000 scans/month

Daily files would exhaust the free tier at under a thousand queries. The archive is still
COLLECTED daily — that is the unit a run produces, and the never-shrink rule depends on
it — but it is CONSOLIDATED to one Parquet per month before upload.

CREDENTIALS

Read from the environment, never from a file in the repository:

    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET

Absent credentials are not an error. `achizitii publish` keeps writing locally and simply
does not upload, so a contributor without access can still build the whole site.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ROOT

log = logging.getLogger(__name__)

BUDGET_GB = float(os.environ.get("R2_MAX_GB", "4"))
"""Refuse to upload beyond this many gigabytes.

The 10 GB free tier belongs to the whole Cloudflare account, not to this project — other
projects draw on the same allowance. A guard that fails loudly is better than discovering
the overage on an invoice, so publishing stops rather than silently pushing the account
into billing. Raise it deliberately via R2_MAX_GB once you know what the account has
spare."""

PRICES_DIR = "preturi"
MONTHLY_DIR = "preturi-lunar"
"""Where consolidated monthly files are written before upload."""


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @classmethod
    def from_env(cls) -> R2Config | None:
        """Configuration from the environment, or None when it is not set up.

        None is a normal state, not a failure: publishing locally must work for anyone
        who has cloned the repository without access to the bucket.
        """
        keys = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
        values = [os.environ.get(k, "").strip() for k in keys]
        if not all(values):
            missing = [k for k, v in zip(keys, values, strict=True) if not v]
            log.info("R2 not configured (missing %s); publishing locally only", missing)
            return None
        return cls(*values)


def consolidate_months(out: Path | None = None) -> list[Path]:
    """Rewrite the daily archive as one Parquet per month.

    Collection stays daily because that is the unit a run produces and what the
    never-shrink rule protects. Serving is monthly because the browser pays one range
    request per file per query, and four thousand daily files would spend the free tier
    on metadata rather than data.
    """
    import duckdb

    base = Path(out or Path(ROOT) / "site" / "data")
    daily = base / PRICES_DIR
    if not daily.is_dir():
        return []

    target_dir = base / MONTHLY_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    months = con.execute(
        f"""SELECT DISTINCT strftime(CAST(data_finalizare AS DATE), '%Y-%m') AS luna
            FROM read_parquet('{daily}/**/*.parquet')
            WHERE data_finalizare IS NOT NULL ORDER BY 1"""
    ).fetchall()

    written: list[Path] = []
    for (month,) in months:
        target = target_dir / f"{month}.parquet"
        con.execute(
            f"""COPY (
                    SELECT * FROM read_parquet('{daily}/**/*.parquet')
                    WHERE strftime(CAST(data_finalizare AS DATE), '%Y-%m') = '{month}'
                    ORDER BY cpv, denumire_key
                ) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"""
        )
        written.append(target)
        log.info("consolidated %s: %.2f MB", month, target.stat().st_size / 1e6)
    con.close()
    return written


def _mutable(path: Path) -> bool:
    """Whether this object can change without changing size.

    The upload skips anything already present at the same byte length, which is a cheap
    and adequate test for a month that has closed — the never-shrink rule only ever adds
    days to the CURRENT month, so a closed month is frozen.

    It is NOT adequate for anything still being written. Caught in the act: the first two
    real uploads left a manifest in the bucket that was 11,560 bytes both times and a
    different file each time, because a date moving from 2026-09-06 to 2026-09-07 is
    exactly the same length. The index describing the data was stale while every checksum
    said it was fine.

    So the two things that still change are always re-uploaded. They are small — the
    manifest is 11 KB — and being wrong about them is expensive in a way 11 KB is not.
    """
    if path.name == "manifest.json":
        return True
    return path.stem == datetime.now(UTC).strftime("%Y-%m")


def _cache_control(path: Path) -> str:
    """How long an object may be cached at the edge.

    This is the single biggest lever on Class B operations, which are shared with every
    other project on the account. A cached read never reaches R2 and never bills.

    A month that has closed cannot change — the never-shrink rule only ever adds days to
    the CURRENT month — so past months are immutable and can be cached for a year. The
    current month and the manifest change daily and must not be.
    """
    if path.name == "manifest.json":
        return "public, max-age=300"
    current = datetime.now(UTC).strftime("%Y-%m")
    if path.stem == current:
        return "public, max-age=3600"
    return "public, max-age=31536000, immutable"


def upload(paths: list[Path], base: Path, config: R2Config | None = None) -> dict[str, Any]:
    """Upload files to R2, skipping anything already there with the same size.

    Skipping matters: re-uploading the whole archive on every run would burn Class A
    operations and, more to the point, waste the runner's time re-sending months that
    have not changed since the quarter closed.
    """
    cfg = config or R2Config.from_env()
    if cfg is None:
        return {"uploaded": 0, "skipped": 0, "configured": False}

    try:
        import boto3
    except ImportError:
        log.warning("boto3 is not installed; skipping upload")
        return {"uploaded": 0, "skipped": 0, "configured": False, "error": "boto3 missing"}

    client = boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
    )

    planned_gb = sum(p.stat().st_size for p in paths) / 1e9
    if planned_gb > BUDGET_GB:
        raise RuntimeError(
            f"upload would place {planned_gb:.2f} GB in R2, over the {BUDGET_GB:.1f} GB "
            "budget for this project. The free tier is shared with the rest of the "
            "account; raise R2_MAX_GB deliberately if there is room."
        )

    existing: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg.bucket):
        for obj in page.get("Contents", []):
            existing[obj["Key"]] = obj["Size"]

    uploaded = skipped = 0
    for path in paths:
        key = str(path.relative_to(base)).replace(os.sep, "/")
        size = path.stat().st_size
        if existing.get(key) == size and not _mutable(path):
            skipped += 1
            continue
        client.upload_file(
            str(path),
            cfg.bucket,
            key,
            ExtraArgs={
                "ContentType": (
                    "application/json" if path.suffix == ".json" else "application/octet-stream"
                ),
                # A month that has closed never changes; a browser should not re-fetch it.
                # The manifest must not be cached that way, or the site pins itself to a
                # stale view of what exists.
                "CacheControl": _cache_control(path),
            },
        )
        uploaded += 1
        log.info("uploaded %s (%.2f MB)", key, size / 1e6)

    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "configured": True,
        "bucket": cfg.bucket,
        "gb": round(planned_gb, 3),
        "buget_gb": BUDGET_GB,
    }
