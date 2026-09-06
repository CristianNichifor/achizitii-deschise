"""Product-level grouping and brand detection.

A CPV code is far too coarse to compare prices within. `30213100` ("computere
portabile") covers a budget netbook and a mobile workstation alike, and the archive
holds "Laptop", "laptop", "LAPTOP" and "Laptop Dell Inspiron 3567 cu procesor Intel
Core i5..." all under that single code. Averaging them yields a number that describes
nothing in particular.

The original brief framed this as mapping descriptions onto CPV codes. In the SEAP data
that turned out to be unnecessary — every line item already carries a CPV. The real gap
is the opposite direction: splitting one CPV into groups of genuinely comparable items,
and joining items that describe the same thing under different CPVs.

This module provides the grouping key. The brand gazetteer it loads does double duty as
the Legea 98/2016 art. 156 check ("sau echivalent").
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT
from .normalize import fold

# Words that carry no distinguishing information about WHAT was bought. Dropping them
# stops "Achizitie laptop" and "Laptop" from forming separate groups.
_BOILERPLATE = frozenset(
    ["achizitie", "achizitii", "achizitionare", "achizitionat", "cumparare", "cumparari", "furnizare", "livrare", "prestare", "servicii", "serviciu", "produs", "produse", "produsul", "bunuri", "materiale", "conform", "caiet", "caietul", "sarcini", "specificatii", "cerinte", "oferta", "contract", "comanda", "necesar", "necesare", "diverse", "diferite", "various", "pentru", "catre", "despre", "de", "la", "cu", "din", "in", "pe", "si", "sau", "un", "o", "a", "al", "ale", "ai", "lui", "cel", "cea", "acest", "aceasta", "buc", "bucata", "bucati", "set", "seturi", "kit", "pachet", "lot", "cod", "nr", "numar"]
)

# A description usually opens with the product and then drifts into specification. Cut
# at the first marker so "Laptop Dell Inspiron 3567 cu procesor Intel Core i5, 8GB RAM"
# groups with other Dell laptops rather than forming its own singleton.
_SPEC_TAIL = re.compile(
    r"(?i)\s*(?:,|;|\(|\bcu procesor\b|\bcu \d|\bavand\b|\bcaracteristici\b"
    r"|\bspecificatii\b|\bconform\b|\bdim\.|\bdimensiuni\b|\bmodel\b\s*:|:)"
)

_MAX_HEAD_TOKENS = 3
"""Beyond this the tail is specification, not identity."""

# Descriptions that name no product. Counts measured across 2016-2021 direct
# acquisitions (15.7M rows); the largest are "conform descriere" (19,281), "-" (12,277),
# "." (8,217), "achizitie directa" (7,711) and "conform oferta" (6,845) — roughly 66,800
# rows in total.
#
# These rows are real spending and are kept, but they cannot support a price comparison:
# nothing distinguishes one "conform descriere" from another, so grouping them would
# average unrelated products. Short descriptions are NOT uninformative by default —
# "banane", "oua" and "cartofi" are among the most common values in the archive and are
# perfectly good product names.
_UNINFORMATIVE = re.compile(
    r"""(?ix)^\s*(?:
        -+ | \.+ | _+ | x+ | n/?a | null | none | test |
        conform\s+(?:descriere\w*|oferta|ofertei|caiet\w*(?:\s+de\s+sarcini)?
                   |documenta\w+|specificat\w+(?:\s+\w+)*) |
        achizit(?:ie|ii)\s+direct[ae]? | cumparare\s+directa |
        diverse | divers | produse | produs | materiale | bunuri | altele
    )\s*$"""
)


def uninformative_reason(
    description: str | None, autoritate: str | None = None
) -> str | None:
    """Why a description cannot identify a product, or None if it can."""
    if not description or not description.strip():
        return "descriere_lipsa"
    text = description.strip()
    if _UNINFORMATIVE.match(text):
        return "descriere_generica"
    # Some buyers put their own name in the description field. Rare overall (0.47% in
    # 2016, near zero since), but it identifies no product at all.
    if autoritate and fold(text) == fold(autoritate):
        return "descriere_egala_cu_autoritatea"
    if len(fold(text).replace(" ", "")) < 3:
        return "descriere_prea_scurta"
    return None


@dataclass(frozen=True)
class Product:
    """A description reduced to a comparable identity."""

    head: str
    """Leading content words — what the thing is."""

    brand: str | None
    """Detected commercial brand, if any."""

    key: str
    """Grouping key. Items sharing a key and a unit are price-comparable."""

    brands_found: tuple[str, ...]
    """All brands mentioned, for the art. 156 check."""

    informative: bool = True
    """False when the description names no product; excluded from price grouping."""

    uninformative_reason: str | None = None


@functools.lru_cache(maxsize=1)
def _gazetteer() -> tuple[tuple[str, ...], frozenset[str]]:
    path = Path(ROOT) / "data" / "branduri.yml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    brands = tuple(
        sorted((fold(str(b)) for b in spec.get("brands", [])), key=len, reverse=True)
    )
    not_brands = frozenset(fold(str(b)) for b in spec.get("not_brands", []))
    return brands, not_brands


def find_brands(text: str | None) -> tuple[str, ...]:
    """Commercial brands mentioned, matched on word boundaries.

    Word-boundary matching matters: "hp" must not fire inside "shpe", and standards
    bodies listed in `not_brands` (ISO, EN, SR) must never be treated as brands — doing
    so would manufacture art. 156 findings against entirely lawful specifications.
    """
    if not text:
        return ()
    folded = fold(text)
    brands, not_brands = _gazetteer()
    hits: list[tuple[int, str]] = []
    for brand in brands:
        if brand in not_brands:
            continue
        m = re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", folded)
        if not m:
            continue
        # Skip a brand already covered by a longer match ("braun" inside "b braun").
        if any(brand in seen and brand != seen for _, seen in hits):
            continue
        hits.append((m.start(), brand))
    # Ordered by position in the text, NOT by gazetteer order. A description reads
    # "Laptop Dell Inspiron ... cu procesor Intel Core i5": the manufacturer comes
    # first and the component vendor later, so position identifies the product brand.
    # Ordering by length instead picked Intel over Dell.
    return tuple(brand for _, brand in sorted(hits))


_EQUIVALENT = re.compile(r"(?i)\bsau\s+echivalent")


def mentions_equivalent(text: str | None) -> bool:
    """True when the text carries the 'sau echivalent' qualifier."""
    return bool(text and _EQUIVALENT.search(text))


def brand_without_equivalent(text: str | None) -> tuple[str, ...]:
    """Brands named without 'sau echivalent' — the Legea 98/2016 art. 156 signal.

    Returns the offending brands, or empty when none apply. This is deliberately
    deterministic: a regex and a curated list are auditable by the authority named in a
    finding, which a model's judgement is not.
    """
    if mentions_equivalent(text):
        return ()
    return find_brands(text)


def product_key(
    description: str | None, cpv: str | None = None, autoritate: str | None = None
) -> Product:
    """Reduce a description to a grouping key.

    The key combines the leading content words with the brand when one is present, so
    that Dell and HP laptops form distinct groups while "Laptop", "laptop" and "LAPTOP"
    collapse into one. CPV, when supplied, prefixes the key: identical wording under
    different CPV codes describes different things often enough that merging them would
    be wrong.
    """
    raw = description or ""
    reason = uninformative_reason(raw, autoritate)
    if reason:
        # Keyed on CPV alone: the spending is still counted, but these rows cannot
        # form a product group, and must not be silently merged into a real one.
        return Product(
            head="",
            brand=None,
            key=re.sub(r"\D", "", cpv or "")[:8],
            brands_found=find_brands(raw),
            informative=False,
            uninformative_reason=reason,
        )

    brands = find_brands(raw)
    brand = brands[0] if brands else None

    folded = fold(raw)
    head_text = _SPEC_TAIL.split(folded, maxsplit=1)[0]
    # Everything before the brand is what the thing IS; everything after is model and
    # specification, which is too sparse to group on. "Laptop Dell Inspiron 3567" thus
    # yields head "laptop", brand "dell" — grouping all Dell laptops together.
    if brand and brand in head_text:
        head_text = head_text.split(brand, 1)[0]
    tokens = [
        t for t in head_text.split()
        if t not in _BOILERPLATE and not t.isdigit() and len(t) > 1
    ]
    head = " ".join(tokens[:_MAX_HEAD_TOKENS])

    cpv_digits = re.sub(r"\D", "", cpv or "")[:8]
    parts = [p for p in (cpv_digits or None, head or None, brand) if p]
    return Product(
        head=head,
        brand=brand,
        key="|".join(parts) if parts else "",
        brands_found=brands,
    )
