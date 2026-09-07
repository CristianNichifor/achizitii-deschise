"""Ambiguous brand tokens must not fire on ordinary Romanian words.

Several real brands are spelled like common words, abbreviations or place names. On a
600,000-row sample of direct-acquisition descriptions, bare word-boundary matching was
right only 24.5% of the time for `man` and 43.7% for `lg`:

    "DET.MAN.20KG"          detergent *manual*, not MAN trucks
    "LEUSTEAN RO. LG. C.I"  *legume*, the produce-catalogue abbreviation, not LG
    "PROSOP ISABEL BRAUN"   a *brown* towel
    "MEDICINA MUNCII-TESA"  the staff category, not the adhesive-tape brand
    "Transport la Vatra Dorna"  the town, not the water

These feed price-comparison groups, so a wrong brand splits or merges groups that should
not be. Each ambiguous token therefore counts only when the text also names something
the brand actually makes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from achizitii.produs import _gazetteer, find_brands

# (description, expected brands) — every case is a real string from the archive,
# or a minimal reduction of one.
CASES = [
    ("DET.MAN.20KG detergent", ()),
    ("GAR.MAN.+SILD CH 72MM", ()),
    ("Autogunoiera MAN TGL12.180, 4X2BB", ("man",)),
    ("SET REPARATIE ETRIER COMPLET MAN A74", ("man",)),
    ("LEUSTEAN RO. LG. C.I ROMANIA", ()),
    ("MARAR RO LG CI", ()),
    ("BLU-RAY/DVD Writer LG, 16x, BH16NS55R", ("lg",)),
    ("PROSOP ISABEL BRAUN 70X100CM", ()),
    ("METRONIDAZOL BRAUN 5mg/ml solutie", ("braun",)),
    ("MEDICINA MUNCII-TESA", ()),
    ("PACHET PENTRU PERSONAL ASIMILAT TESA", ()),
    ("TESA banda adeziva 50mm", ("tesa",)),
    ("Transport a 10 persoane la Vatra Dorna si retur", ()),
    ("APA MINERALA DORNA 0.5L", ("dorna",)),
    ("Servicii de promovare pe postul de televiziune Bucovina TV", ()),
    ("CASCAVAL BUCOVINA", ("bucovina",)),
]


@pytest.mark.parametrize(("text", "expected"), CASES, ids=lambda v: str(v)[:40])
def test_ambiguous_brands_need_context(text: str, expected: tuple[str, ...]) -> None:
    assert find_brands(text) == expected


def test_unambiguous_brands_still_match_on_their_own() -> None:
    """The context rule must not leak onto ordinary brands."""
    assert find_brands("Toner HP CF380X") == ("hp",)
    assert find_brands("Cartus toner Canon CRG-737") == ("canon",)
    assert find_brands("monitor 19 dell-e1912-lux") == ("dell",)


def test_ambiguous_tokens_are_not_also_plain_brands() -> None:
    """A token in both lists would match unconditionally and defeat the context rule."""
    brands, _, ambiguous = _gazetteer()
    spec = {
        b for b in ambiguous
    }
    plain = [b for b in brands if b in spec]
    # every ambiguous token appears in `brands` exactly once, contributed by the
    # ambiguous section itself — never duplicated from the plain list
    assert len(plain) == len(spec), (
        "ambiguous tokens must be declared only under `ambiguous:` in branduri.yml"
    )


def test_every_ambiguous_token_declares_context_terms() -> None:
    _, _, ambiguous = _gazetteer()
    assert ambiguous, "the ambiguous section should not be empty"
    for brand, terms in ambiguous.items():
        assert terms, f"{brand} is declared ambiguous but lists no context terms"


def test_no_indicator_uses_brand_without_equivalent() -> None:
    """Guard on a deliberate omission, so it is not reversed by accident.

    Applied to acquisition titles the art. 156 check degenerates into "mentions a
    brand" — 6.2% of the archive, roughly 1.66 million rows naming real contracting
    authorities, of which 44.7% are consumables or repairs where identifying the make
    is the lawful option. The qualifier it looks for appears in 0.04% of them, because
    a title is not a specification. METHODOLOGY.md carries the full measurement.

    If tender documents are ingested, delete this test along with the restriction.
    """
    from achizitii import indicators

    source = Path(indicators.__file__).read_text()
    assert "brand_without_equivalent" not in source, (
        "an indicator now calls brand_without_equivalent. That check is only meaningful "
        "on tender documents; on acquisition titles it flags ~1.66M lawful rows. See "
        "METHODOLOGY.md, 'Why there is no brand indicator'."
    )
    assert not any(i.identifier.startswith("marca") for i in indicators.INDICATORS)
