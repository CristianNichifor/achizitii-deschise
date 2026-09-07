"""The published docs must name every indicator that exists.

README.md and METHODOLOGY.md both carry a table of indicators, and both had fallen
behind the code: seven indicators existed while the README documented five and called
them "Five deterministic indicators". An indicator that runs and produces findings but
is described nowhere is worse than one that does not exist — it names real contracting
authorities with no published statement of what it checks or under which article.

The legal basis is checked too, because that is the part a named authority would
challenge, and a docs table that cites a different article from the code is a defect
even when both are individually defensible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from achizitii.indicators import INDICATORS

DOCS = [Path("README.md"), Path("METHODOLOGY.md")]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_indicator_is_documented(doc: Path) -> None:
    text = doc.read_text()
    missing = [i.identifier for i in INDICATORS if f"`{i.identifier}`" not in text]
    assert not missing, (
        f"{doc.name} does not mention {missing}. Every indicator that can name a real "
        "authority must be described where the findings are published."
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_documented_indicators_all_exist(doc: Path) -> None:
    """The reverse: a docs table promising an indicator we do not ship."""
    known = {i.identifier for i in INDICATORS}
    cited = set(re.findall(r"`([a-z-]+-\d{2})`", doc.read_text()))
    assert not (cited - known), f"{doc.name} documents indicators that do not exist: {cited - known}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_article_numbers_match_the_code(doc: Path) -> None:
    """Each indicator's row must cite the same articles the indicator declares."""
    text = doc.read_text()
    for indicator in INDICATORS:
        rows = [ln for ln in text.splitlines() if f"`{indicator.identifier}`" in ln and "|" in ln]
        if not rows:
            continue  # prose mention, not a table row
        declared = set(re.findall(r"art\.\s*(\d+)", indicator.legal_basis))
        for row in rows:
            cited = set(re.findall(r"(?:art\.\s*|&\s*)(\d+)", row))
            assert cited <= declared, (
                f"{doc.name} cites art. {sorted(cited - declared)} for {indicator.identifier}, "
                f"which declares only art. {sorted(declared)}"
            )


def test_indicator_count_claim_is_current() -> None:
    """The README states the count in words; a stale number reads as a missing feature."""
    words = {5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
    expected = words[len(INDICATORS)]
    readme = Path("README.md").read_text()
    assert f"{expected} deterministic indicators" in readme, (
        f"README should say '{expected} deterministic indicators' "
        f"({len(INDICATORS)} are implemented)"
    )
