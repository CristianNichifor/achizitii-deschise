"""RUTI — the register of meetings between public decision-makers and third parties.

`ruti.gov.ro` publishes, in advance, meetings that ministers, secretaries of state,
deputies and senators hold with interest groups. It is Romania's answer to the EU
Transparency Register. The site is a single-page app over an open JSON backend:
`/api/registry/meetings` needs no key and no headers beyond a polite User-Agent, and
`robots.txt` is `User-agent: * / Disallow:` — everything permitted.

The whole register is **666 meetings** and fetches in **7 requests** at `per_page=100`.
That is the entire cost of this module.

WHY THIS IS NOT JOINED TO PROCUREMENT
-------------------------------------

The obvious idea — "which suppliers met the officials who bought from them" — does not
survive contact with the data, and it is worth writing down why so nobody rebuilds it:

* **No company identifier.** There is no CUI, CIF or organisation field anywhere in the
  payload. The third party exists only inside free-text `name` and `description`. Any
  join would be fuzzy string matching against 751,505 supplier names.
* **Almost no overlap.** The institutions here are ministries, the Chamber of Deputies,
  the Senate and the Government. Those buyers are **1.70%** of our archive (452,922 of
  26,672,772 acquisitions); the actual top spenders are Romsilva, Institutul Clinic
  Fundeni and RAJA Constanța, none of which appear.
* **666 records over eleven months.** Even a perfect join would fire almost never.

So an indicator built on this would produce close to nothing, and each time it did fire
it would name a real individual on the strength of a fuzzy match against free text. That
is the worst risk-to-value ratio available in this project.

What the register IS good for is being readable at all. Nobody currently publishes it as
machine-readable open data, so it ships here as its own table, with the standing caveat
that **a meeting is lawful and the register exists to make it visible** — its presence
here says nothing about any contract.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .client import RateLimiter
from .config import ROOT, USER_AGENT

log = logging.getLogger(__name__)

BASE_URL = "https://ruti.gov.ro/api/registry/meetings"

PAGE_SIZE = 100
"""The API accepts this; the site itself asks for 15. Seven requests covers the register."""

MAX_RPS = 2.0
"""Deliberately slower than the SEAP client. This is a small government service with a
few hundred records, and there is no reason to be brisk with it."""

MAX_PAGES = 200
"""Backstop. At PAGE_SIZE this allows 20,000 records — far beyond the register's size,
so it can only stop a pagination bug looping forever."""

TIMEOUT = 45.0


@dataclass(frozen=True)
class Meeting:
    """One published meeting.

    Field names are kept in Romanian where the source uses Romanian, matching the
    convention in `govdata.py`: a reader comparing our table against the register should
    not have to translate.
    """

    id: str
    titlu: str
    descriere: str
    concluzii: str | None
    data_intalnirii: datetime | None
    data_publicarii: datetime | None
    locul_intalnirii: str | None
    decident: str | None
    functie: str | None
    institutie: str | None
    anulat: bool
    justificare_anulare: str | None


def _parse_dt(value: Any) -> datetime | None:
    """Parse a source timestamp into naive UTC.

    The register sends offset-bearing strings, and `fromisoformat` faithfully returns an
    AWARE datetime for those. That is where reproducibility broke: the Parquet column is
    a naive TIMESTAMP, and DuckDB fills a naive column from an aware value by shifting it
    into the session's timezone. The same meeting therefore landed at midnight when the
    ingest ran on a UTC runner and at 02:00 when it ran on a CEST laptop — the published
    file depended on where it was built, which the project's reproducibility rule forbids.

    Normalising here means the column means one thing everywhere. A value that arrives
    without an offset is left alone, which is what it was already treated as.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        log.warning("unparsable date %r", value)
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def to_meeting(row: dict[str, Any]) -> Meeting:
    """Map one API record onto our schema.

    `anulat` arrives as the Romanian string "nu"/"da" rather than a boolean. Anything
    that is not recognisably "da" is treated as not cancelled, because inventing a
    cancellation is worse than missing one.
    """
    return Meeting(
        id=str(row.get("id") or "").strip(),
        titlu=(row.get("name") or "").strip(),
        descriere=(row.get("description") or "").strip(),
        concluzii=(row.get("concluzii") or "").strip() or None,
        data_intalnirii=_parse_dt(row.get("data_intalnirii")),
        data_publicarii=_parse_dt(row.get("date_entered")),
        locul_intalnirii=(row.get("locul_intalnirii") or "").strip() or None,
        # `decident_name` and `creator_name` are the same person on every record
        # sampled, but they are distinct fields upstream and could diverge; the decision
        # maker is the one that matters, so that is the one kept.
        decident=(row.get("decident_name") or "").strip() or None,
        functie=(row.get("function_name") or "").strip() or None,
        institutie=(row.get("institutie") or "").strip() or None,
        anulat=str(row.get("anulat") or "").strip().lower() == "da",
        justificare_anulare=(row.get("justificare_anulare") or "").strip() or None,
    )


def fetch(raw_dir: Path | None = None) -> list[dict[str, Any]]:
    """Download every page, writing each one to an immutable raw archive.

    The raw payloads are kept for the same reason `data/gov_raw/` is: if the mapping
    below turns out to be wrong, the fix must not require re-downloading from a service
    that may have changed underneath us.
    """
    out = Path(raw_dir or Path(ROOT) / "data" / "ruti_raw")
    out.mkdir(parents=True, exist_ok=True)

    limiter = RateLimiter(MAX_RPS)
    rows: list[dict[str, Any]] = []
    stamp = datetime.now(UTC).strftime("%Y%m%d")

    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
        follow_redirects=True,
    ) as client:
        page = 1
        while page <= MAX_PAGES:
            limiter.wait()
            response = client.get(BASE_URL, params={"per_page": PAGE_SIZE, "page": page})
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("data") or []
            (out / f"meetings-{stamp}-p{page:03d}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            rows.extend(batch)

            meta = payload.get("meta") or {}
            total = meta.get("total")
            log.info("page %d: %d records (%d/%s)", page, len(batch), len(rows), total)
            if not batch or (total is not None and len(rows) >= int(total)):
                break
            page += 1
        else:
            log.warning("stopped at the %d-page backstop; pagination may be broken", MAX_PAGES)

    return rows


def to_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise, de-duplicate on id, and drop anything without one.

    Paging a live register can return the same record twice if a new meeting is
    published between requests, which would otherwise silently inflate every count.
    """
    seen: dict[str, Meeting] = {}
    skipped = 0
    for row in rows:
        meeting = to_meeting(row)
        if not meeting.id:
            skipped += 1
            continue
        seen[meeting.id] = meeting
    if skipped:
        log.warning("%d records had no id and were dropped", skipped)
    if len(seen) != len(rows) - skipped:
        log.info("de-duplicated %d repeated records", len(rows) - skipped - len(seen))
    return [m.__dict__ for m in seen.values()]


def write(records: list[dict[str, Any]], out_path: Path | None = None) -> Path:
    """Write the normalised register to Parquet.

    Both TIMESTAMP columns are naive UTC — `_parse_dt` guarantees it. That guarantee is
    what makes this file byte-reproducible: DuckDB fills a naive column from an *aware*
    value by shifting it into the session timezone, so an aware input here would make the
    output depend on the machine that built it.
    """
    import duckdb

    target = Path(out_path or Path(ROOT) / "data" / "ruti" / "meetings.parquet")
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE m (
             id VARCHAR, titlu VARCHAR, descriere VARCHAR, concluzii VARCHAR,
             data_intalnirii TIMESTAMP, data_publicarii TIMESTAMP,
             locul_intalnirii VARCHAR, decident VARCHAR, functie VARCHAR,
             institutie VARCHAR, anulat BOOLEAN, justificare_anulare VARCHAR)"""
    )
    for r in records:
        con.execute(
            "INSERT INTO m VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [r[k] for k in (
                "id", "titlu", "descriere", "concluzii", "data_intalnirii",
                "data_publicarii", "locul_intalnirii", "decident", "functie",
                "institutie", "anulat", "justificare_anulare",
            )],
        )
    con.execute(
        f"COPY (SELECT * FROM m ORDER BY data_intalnirii DESC) TO '{target}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    con.close()
    return target


def ingest() -> dict[str, Any]:
    """Fetch, normalise and write. Returns a summary for the CLI."""
    rows = fetch()
    records = to_records(rows)
    target = write(records)

    dates = [r["data_intalnirii"] for r in records if r["data_intalnirii"]]
    with_conclusions = sum(1 for r in records if r["concluzii"])
    return {
        "sursa": BASE_URL,
        "intalniri": len(records),
        "fisier": str(target),
        "din": min(dates).date().isoformat() if dates else None,
        "pana_la": max(dates).date().isoformat() if dates else None,
        "anulate": sum(1 for r in records if r["anulat"]),
        # Surfaced because it is the register's biggest weakness: what was actually
        # discussed is optional, and usually left blank.
        "cu_concluzii_publicate": with_conclusions,
        "institutii": len({r["institutie"] for r in records if r["institutie"]}),
        "decidenti": len({r["decident"] for r in records if r["decident"]}),
    }
