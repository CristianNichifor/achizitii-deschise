"""Strip personal data at the staging boundary.

SEAP responses carry contact details of named individuals (buyer-side and
supplier-side staff). Publishing those would be an unnecessary and unlawful
re-publication of personal data: none of it is needed to analyse prices.

This runs before anything is written to `core/`, so personal data exists only in
the immutable `raw/` archive, which is never published.
"""

from __future__ import annotations

import re
from typing import Any

# Exact keys observed in SEAP payloads that carry personal data.
PERSONAL_KEYS = frozenset(
    {
        "assignedUserEmail",
        "assignedCAUser",
        "assignedSupplierUser",
        "assignedUserId",
        "contactPerson",
        "personName",
        "email",
        "phone",
        "fax",
        "userName",
    }
)

# Defensive: any key whose name looks like a contact field.
PERSONAL_KEY_RE = re.compile(
    r"(?i)(email|e_mail|phone|telefon|mobil|fax|contactperson|persoana|assigneduser)"
)


def is_personal_key(key: str) -> bool:
    return key in PERSONAL_KEYS or bool(PERSONAL_KEY_RE.search(key))


def scrub(obj: Any) -> Any:
    """Recursively drop personal-data keys from a decoded JSON structure."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not is_personal_key(k)}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj
