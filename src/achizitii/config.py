"""Central configuration. No secrets — every source used here is public."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
CORE = DATA / "core"
MARTS = DATA / "marts"
STATE = ROOT / "state"
SITE_DATA = ROOT / "site" / "data"

BASE = "https://www.e-licitatie.ro"
API = f"{BASE}/api-pub"

# The portal rejects requests without a Referer with
#   403 {"message":"Access Denied: Referrer cannot be null."}
# That is the entire "session trap" — no cookies or JS execution are required.
REFERER = f"{BASE}/pub"

CONTACT = "https://github.com/CristianNichifor/achizitii-deschise"
USER_AGENT = f"achizitii-deschise/0.1 (+{CONTACT})"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Accept": "application/json",
    "Accept-Language": "ro-RO,ro;q=0.9",
}

# Not politeness any more. SEAP published its thresholds on 2026-07-15.
MAX_RPS = 1.5
"""Requests per second, per client. DO NOT RAISE WITHOUT READING THIS.

THE LIMIT IS PUBLISHED, AND WE WERE OVER IT

SEAP states the thresholds that trigger a block on anonymous access:

    500 accesses in 5 minutes from one IP   ->  1.67 requests/second sustained
     50 accesses in 1 second                ->  burst ceiling

4.0 rps is 1,200 requests per 5 minutes: 2.4x the published limit. That was not a
cautious setting that happened to get unlucky, it was over the line the whole time, and
this comment used to describe it as "far below what drew the block" — true, and beside
the point. 1.5 leaves headroom under 1.67 for retries and the odd burst.

The same announcement says that parallel queries, repeated requests at very short
intervals, and continuous automated processes over long periods are all discouraged, and
that anyone needing high-volume or recurring programmatic access should ask their support
team rather than arrange it themselves. A multi-month crawl is exactly the thing that
paragraph is about, whatever rate it runs at. Ask; do not tune.

This was briefly set to 40, with 16 concurrent workers, because a burst test showed 37
records a second with zero errors and flat latency. That reasoning was wrong. "The
server answers quickly" is not "the server is willing to serve this much", and SICAP
settled the question itself a few hours later:

    HTTP 403, server: SICAP
    "Accesul de la adresa dumneavoastra IP a fost restrictionat. Sistemul a detectat
     un volum de trafic automat care depaseste limitele de utilizare normala."

An IP-level block, lifted only by contacting their support. The cost of being wrong here
is not a slow job — it is losing the only source of line-item prices that exists, for
everyone sharing that address.

If more throughput is genuinely needed, ask SEAP for it rather than measuring how much
they tolerate before objecting. Measuring is what produced the block: a burst test showed
37 records a second with flat latency, which said only that the server answers quickly,
never that it consented."""
TIMEOUT = 45.0
RETRIES = 4

# The list endpoint caps `total` at 2000, so windows must stay small enough that a
# single (day, page) sweep does not silently truncate. One day is comfortably safe
# for tenders; direct acquisitions hit the cap and are paged.
LIST_PAGE_SIZE = 500

DETAIL_WORKERS = 2
"""Concurrent detail fetches. Deliberately small; see MAX_RPS for what happened at 16."""

# The list endpoint returns at most 2,000 records for a finalisation date, sorted by
# finalisation time ascending, and paging stops dead at that point — pageIndex 20 with a
# page size of 100 returns nothing. On a weekday with ~8,900 acquisitions that silently
# captured the earliest 22% of the day and discarded the rest.
#
# Splitting the day by CPV category is the fix: ids 1-12 are valid, and the same day
# yields 8,272 records against a ground truth of 8,940 (92.6%) instead of 2,000 (22%).
# One category can still cap out; that is reported rather than hidden.
CPV_CATEGORY_IDS = tuple(range(1, 13))
LIST_CAP = 2000

CURRENCY = "RON"

# OCDS. Registering a real prefix with the Open Contracting Partnership is a Stage-2
# task; until then we use a clearly-provisional local prefix so nothing published
# can be mistaken for a registered publisher's identifier.
OCID_PREFIX = "ocds-x0xx00"  # PROVISIONAL — replace once OCP assigns a prefix
PUBLISHER = {
    "name": "achizitii-deschise",
    "scheme": "RO-SEAP",
    "uri": CONTACT,
}
