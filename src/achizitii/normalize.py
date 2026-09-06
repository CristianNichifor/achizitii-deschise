"""Text, unit and price normalisation.

This module carries most of the project's analytical weight. Extraction is easy —
SEAP hands us structured line items. Making those items *comparable* is the hard part
and is where a naive pipeline silently produces wrong answers.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT

# Romanian text uses ș/ț (comma below). Windows-era software emits ş/ţ (cedilla).
# Both appear in SEAP. Fold cedilla -> comma so they compare equal.
_CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})

# For matching only, we additionally strip diacritics entirely, so "bucată" == "bucata".
_STRIP_DIACRITICS = str.maketrans({"ă": "a", "â": "a", "î": "i", "ș": "s", "ț": "t"})


def fix_diacritics(s: str) -> str:
    """Canonical display form: NFC, cedilla folded to comma-below."""
    return unicodedata.normalize("NFC", s).translate(_CEDILLA)


def fold(s: str) -> str:
    """Aggressive match key: lowercase, no diacritics, no punctuation, single spaces."""
    s = fix_diacritics(s).lower().translate(_STRIP_DIACRITICS)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class Unit:
    canonical: str
    dimension: str
    comparable: bool


@functools.lru_cache(maxsize=1)
def _unit_index() -> dict[str, Unit]:
    path = Path(ROOT) / "data" / "um_map.yml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    index: dict[str, Unit] = {}
    for canonical, body in spec["units"].items():
        unit = Unit(canonical, body["dimension"], bool(body["comparable"]))
        index[fold(canonical)] = unit
        for alias in body.get("aliases", []):
            index[fold(str(alias))] = unit
    return index


UNKNOWN = Unit("necunoscut", "unknown", False)


def normalize_unit(raw: str | None) -> Unit:
    """Map a free-text measure unit onto a canonical unit.

    Unrecognised units are *not* guessed. They return UNKNOWN with
    comparable=False so they cannot silently pollute a benchmark.
    """
    if not raw:
        return UNKNOWN
    return _unit_index().get(fold(raw), UNKNOWN)


# Pack size embedded in the item name, e.g.
#   "Almacor 10 mg x 30 cpr"  -> 30
#   "Hartie A4 500 coli"      -> 500
#   "set 12 buc"              -> 12
_PACK_PATTERNS = (
    re.compile(r"(?i)\bx\s*(\d{1,5})\s*(?:cpr|comprimate|buc|bucati|bucăți|file|coli|ml|g|gr)\b"),
    re.compile(r"(?i)\b(\d{1,5})\s*(?:cpr|comprimate|coli|file|bucati|bucăți|buc)\b"),
    re.compile(r"(?i)\b(?:set|cutie|pachet|bax)\s*(?:de|cu)?\s*(\d{1,5})\b"),
)


def extract_pack_size(*texts: str | None) -> int | None:
    """Best-effort pack size from item name/description. None when not confidently found."""
    for text in texts:
        if not text:
            continue
        for pattern in _PACK_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    n = int(m.group(1))
                except ValueError:
                    continue
                if 1 < n <= 100_000:
                    return n
    return None


def parse_ro_number(value: str | float | None) -> float | None:
    """Parse a Romanian-formatted number: '1.234,56' -> 1234.56.

    Numbers arriving from the JSON API are already floats; this exists for the
    data.gov.ro CSV/XLSX ingest path where they arrive as localised strings.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        # Whichever separator is last is the decimal separator.
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass(frozen=True)
class NormalizedItem:
    """A line item reduced to a comparable form (or explicitly marked incomparable)."""

    description: str
    description_key: str
    cpv: str | None
    quantity: float | None
    unit_raw: str | None
    unit: str
    dimension: str
    pack_size: int | None
    unit_price_ron: float | None
    line_total_ron: float | None
    comparable: bool
    incomparable_reason: str | None


def normalize_item(
    *,
    description: str,
    long_description: str | None,
    cpv: str | None,
    quantity: float | None,
    unit_raw: str | None,
    unit_price_ron: float | None,
) -> NormalizedItem:
    """Normalise one SEAP line item.

    `unit_price_ron` must already be a UNIT price. For SEAP direct acquisitions this is
    `itemClosingPrice`, verified empirically: across sampled records
    `closingValue == sum(itemClosingPrice * itemQuantity)`. See docs/ocds-mapping.md.
    """
    unit = normalize_unit(unit_raw)
    pack = extract_pack_size(description, long_description)
    description = fix_diacritics(description or "").strip()

    line_total = (
        unit_price_ron * quantity
        if unit_price_ron is not None and quantity is not None
        else None
    )

    reason: str | None = None
    comparable = True
    if unit_price_ron is None or unit_price_ron <= 0:
        comparable, reason = False, "pret_unitar_lipsa_sau_nepozitiv"
    elif quantity is None or quantity <= 0:
        comparable, reason = False, "cantitate_lipsa_sau_nepozitiva"
    elif unit is UNKNOWN:
        comparable, reason = False, "unitate_nerecunoscuta"
    elif not unit.comparable and pack is None:
        # A bundle of unknown size. Real spending, but its unit price means nothing
        # next to another buyer's differently-sized bundle.
        comparable, reason = False, "unitate_de_tip_pachet_fara_marime_cunoscuta"

    return NormalizedItem(
        description=description,
        description_key=fold(description),
        cpv=(cpv or "").split("-")[0].strip() or None,
        quantity=quantity,
        unit_raw=unit_raw,
        unit=unit.canonical,
        dimension=unit.dimension,
        pack_size=pack,
        unit_price_ron=unit_price_ron,
        line_total_ron=line_total,
        comparable=comparable,
        incomparable_reason=reason,
    )
