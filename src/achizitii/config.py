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
MAX_RPS = 3.0
TIMEOUT = 45.0
RETRIES = 4

# The list endpoint caps `total` at 2000, so windows must stay small enough that a
# single (day, page) sweep does not silently truncate. One day is comfortably safe
# for tenders; direct acquisitions hit the cap and are paged.
LIST_PAGE_SIZE = 500

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
