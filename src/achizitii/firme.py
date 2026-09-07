"""Company facts for suppliers, from ANAF's free public web service.

The procurement archive names 751,505 distinct suppliers by fiscal code and almost
nothing else. Supplier county is missing on 77% of rows, and nothing in the exports says
whether a company was even trading when it won.

ANAF publishes that, free and without a key:
`webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva` takes **100 CUIs per POST** and
answered a real batch of 100 in 0.8 seconds with 99 matches. Enriching every supplier is
about 7,500 requests.

WHAT THIS GIVES, AND WHAT IT DOES NOT
-------------------------------------

Available and used here: registered name, county, registration status and date, fiscal
**inactive** status, **deregistration** (radiere), VAT history.

NOT available from any open source: **ownership**. Who owns or administers a company is
sold by ONRC, and `beneficiari reali` returns zero datasets on data.gov.ro — public
access to beneficial-ownership registers was restricted across the EU after the 2022
CJEU ruling. So "two bidders share an owner" cannot be built on open data today. Every
other supplier-side check can.

DESIGN
------

Results are cached to `data/firme/anaf.parquet` and the job resumes from it, because a
full pass is a couple of hours and must survive being interrupted. Re-running only
fetches codes that are not already cached.

Personal data is not requested and not stored. The response carries a company address,
which for a sole trader can be a home address; only the county is kept.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .client import RateLimiter
from .config import ROOT, USER_AGENT

log = logging.getLogger(__name__)

ENDPOINT = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"

BATCH = 100
"""ANAF documents 100 CUIs per request as the maximum."""

MAX_RPS = 1.0
"""One request a second. ANAF's guidance is one call per second, and at 100 codes per
call that is still 360,000 companies an hour — fast enough that there is no argument for
pushing it."""

TIMEOUT = 90.0
RETRIES = 3

CACHE = Path(ROOT) / "data" / "firme" / "anaf.parquet"


@dataclass(frozen=True)
class Firma:
    """What we keep about a company. Deliberately narrow."""

    cui: str
    denumire: str | None
    judet: str | None
    cod_judet_auto: str | None
    data_inregistrare: date | None
    stare_inregistrare: str | None
    inactiv: bool
    data_inactivare: date | None
    data_reactivare: date | None
    data_radiere: date | None
    platitor_tva: bool
    verificat_la: date


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


_REGISTERED_PREFIX = "INREGISTRAT din data "


def to_firma(row: dict[str, Any], checked: date) -> Firma:
    """Map one ANAF record.

    `stare_inregistrare` is prose — "INREGISTRAT din data 27.01.1993" — so the date is
    pulled out of it. When the wording changes the date is simply absent rather than
    guessed, which is the difference between a missing field and a wrong one.
    """
    general = row.get("date_generale") or {}
    inactive = row.get("stare_inactiv") or {}
    address = row.get("adresa_sediu_social") or {}
    vat = row.get("inregistrare_scop_Tva") or {}

    registered = None
    state = str(general.get("stare_inregistrare") or "").strip()
    if state.startswith(_REGISTERED_PREFIX):
        raw = state[len(_REGISTERED_PREFIX) :].strip()
        parts = raw.split(".")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            day, month, year = parts
            try:
                registered = date(int(year), int(month), int(day))
            except ValueError:
                registered = None

    return Firma(
        cui=str(general.get("cui") or "").strip(),
        denumire=(general.get("denumire") or "").strip() or None,
        judet=(address.get("sdenumire_Judet") or "").strip() or None,
        cod_judet_auto=(address.get("scod_JudetAuto") or "").strip() or None,
        data_inregistrare=registered,
        stare_inregistrare=state or None,
        inactiv=bool(inactive.get("statusInactivi")),
        data_inactivare=_parse_date(inactive.get("dataInactivare")),
        # Reactivation matters as much as inactivation. Without it a company declared
        # inactive in 2019 and reactivated in 2020 would read as inactive for every
        # award it won afterwards — a false statement about a real business.
        data_reactivare=_parse_date(inactive.get("dataReactivare")),
        data_radiere=_parse_date(inactive.get("dataRadiere")),
        platitor_tva=bool(vat.get("scpTVA")),
        verificat_la=checked,
    )


def normalise_cui(raw: str | None) -> str | None:
    """Digits only, and reject what cannot be a fiscal code.

    Supplier codes in the archive appear as "RO 2816464", "2816464" and worse. ANAF wants
    an integer, and a code outside 2-10 digits is a data-entry error rather than a
    company.
    """
    digits = "".join(c for c in str(raw or "") if c.isdigit()).lstrip("0")
    return digits if 2 <= len(digits) <= 10 else None


def fetch_batch(cuis: list[str], on_date: date, limiter: RateLimiter) -> list[Firma]:
    """One request. Codes ANAF does not know are simply absent from the result."""
    body = json.dumps(
        [{"cui": int(c), "data": on_date.isoformat()} for c in cuis]
    ).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for attempt in range(RETRIES):
        limiter.wait()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.load(response)
            return [to_firma(r, on_date) for r in (payload.get("found") or [])]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            wait = 5 * (attempt + 1)
            log.warning("batch failed (%s); retrying in %ds", exc, wait)
            time.sleep(wait)
    log.error("batch of %d gave up after %d attempts", len(cuis), RETRIES)
    return []


COLUMNS = (
    "cui VARCHAR", "denumire VARCHAR", "judet VARCHAR", "cod_judet_auto VARCHAR",
    "data_inregistrare DATE", "stare_inregistrare VARCHAR", "inactiv BOOLEAN",
    "data_inactivare DATE", "data_reactivare DATE", "data_radiere DATE",
    "platitor_tva BOOLEAN",
    "verificat_la DATE",
)


def cached_cuis(cache: Path | None = None) -> set[str]:
    """Fiscal codes already fetched, so a resumed run does not pay for them twice."""
    import duckdb

    path = Path(cache or CACHE)
    if not path.is_file():
        return set()
    con = duckdb.connect()
    rows = con.execute(f"SELECT cui FROM read_parquet('{path}')").fetchall()
    con.close()
    return {r[0] for r in rows}


def save(records: list[Firma], cache: Path | None = None) -> Path:
    """Append to the cache, replacing any earlier answer for the same code."""
    import duckdb

    path = Path(cache or CACHE)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE f ({', '.join(COLUMNS)})")
    if path.is_file():
        con.execute(f"INSERT INTO f SELECT * FROM read_parquet('{path}')")
    for r in records:
        con.execute(
            "INSERT INTO f VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                r.cui, r.denumire, r.judet, r.cod_judet_auto, r.data_inregistrare,
                r.stare_inregistrare, r.inactiv, r.data_inactivare, r.data_reactivare,
                r.data_radiere, r.platitor_tva, r.verificat_la,
            ],
        )
    tmp = path.with_suffix(".tmp.parquet")
    con.execute(
        # Newest answer per code wins, so a re-check supersedes an older one.
        f"""COPY (SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY cui ORDER BY verificat_la DESC) rn
                FROM f) WHERE rn = 1)
            TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)"""
    )
    con.close()
    tmp.replace(path)
    return path


def supplier_cuis() -> list[str]:
    """Every distinct supplier fiscal code in the archive, normalised."""
    import duckdb

    from .govpipeline import _register

    con = duckdb.connect()
    _register(con, {"achizitii_directe", "contracte"})
    rows = con.execute(
        """SELECT DISTINCT furnizor_cui FROM achizitii_directe WHERE furnizor_cui IS NOT NULL
           UNION SELECT DISTINCT furnizor_cui FROM contracte WHERE furnizor_cui IS NOT NULL"""
    ).fetchall()
    con.close()
    seen: dict[str, None] = {}
    for (raw,) in rows:
        code = normalise_cui(raw)
        if code:
            seen[code] = None
    return list(seen)


def enrich(
    limit: int | None = None, on_date: date | None = None, cache: Path | None = None
) -> dict[str, Any]:
    """Fetch every supplier not already cached. Resumable and safe to interrupt."""
    checked = on_date or datetime.now(UTC).date()
    wanted = supplier_cuis()
    done = cached_cuis(cache)
    todo = [c for c in wanted if c not in done]
    if limit:
        todo = todo[:limit]

    log.info(
        "%s suppliers, %s already cached, fetching %s in %s batches",
        f"{len(wanted):,}", f"{len(done):,}", f"{len(todo):,}",
        f"{(len(todo) + BATCH - 1) // BATCH:,}",
    )

    limiter = RateLimiter(MAX_RPS)
    fetched = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        records = fetch_batch(batch, checked, limiter)
        fetched += len(records)
        # Written every batch, not at the end: a two-hour job that loses everything on
        # interruption is a job nobody dares run.
        if records:
            save(records, cache)
        if start and start % (BATCH * 20) == 0:
            log.info("  %s/%s fetched", f"{fetched:,}", f"{len(todo):,}")

    return summary(cache) | {"cerute": len(todo), "obtinute": fetched}


def summary(cache: Path | None = None) -> dict[str, Any]:
    import duckdb

    path = Path(cache or CACHE)
    if not path.is_file():
        return {"firme": 0}
    con = duckdb.connect()
    row = con.execute(
        f"""SELECT count(*), count(judet), count(data_inregistrare),
                   count(*) FILTER (WHERE inactiv), count(*) FILTER (WHERE data_radiere IS NOT NULL)
            FROM read_parquet('{path}')"""
    ).fetchone()
    con.close()
    return {
        "firme": row[0],
        "cu_judet": row[1],
        "cu_data_inregistrare": row[2],
        "inactive": row[3],
        "radiate": row[4],
        "fisier": str(path),
    }


def inactiv_la(firma: Firma, moment: date) -> bool:
    """Was this company fiscally inactive on a given date?

    ANAF evaluates `statusInactivi` as of the date you ask about — KAMPALAMPI SRL reads
    inactive today and active as of 2022 — but the underlying dates are absolute, so the
    answer is computable from the cache without asking again.

    An award must be judged against the state ON THE AWARD DATE. Reporting a supplier as
    inactive because it became inactive years after the contract would be false, and it
    would be false about a named company.
    """
    if firma.data_inactivare is None or moment < firma.data_inactivare:
        return False
    if firma.data_reactivare is None:
        return True
    return moment < firma.data_reactivare


def radiata_la(firma: Firma, moment: date) -> bool:
    """Had this company been struck off the register by a given date?"""
    return firma.data_radiere is not None and moment >= firma.data_radiere
