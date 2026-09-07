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

# Politeness. We are an unauthenticated guest on an undocumented endpoint.
MAX_RPS = 40.0
"""Requests per second, per client.

Measured against the live endpoint rather than guessed. Throughput rises to about 37
records a second at 16 concurrent workers and then PLATEAUS while p95 latency doubles
(0.38s -> 0.76s at 24 workers) — past that we add load without gaining anything, which
is the point at which pushing harder is just rude. A sustained 400-record run at 16
workers held 36.9 rec/s with zero errors and flat latency.

The old value was 3.0. That was a guess of mine, not a limit SEAP imposes, and it made a
weekday of line items take 46 minutes instead of four."""
TIMEOUT = 45.0
RETRIES = 4

# The list endpoint caps `total` at 2000, so windows must stay small enough that a
# single (day, page) sweep does not silently truncate. One day is comfortably safe
# for tenders; direct acquisitions hit the cap and are paged.
LIST_PAGE_SIZE = 500

DETAIL_WORKERS = 16
"""Concurrent detail fetches. See MAX_RPS for why this number and not a larger one."""

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
