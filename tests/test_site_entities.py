"""One tab for organisations, and the search that has to work across it.

Four views answered the same question from four tabs — Autorități, Furnizori, Profil firme,
Fișă entitate. All are keyed on an organisation, and none of the labels tells you which to
pick if you have simply typed a CUI. They are now one "Entități" tab and a selector, which
takes the strip from eleven tabs to eight.

Grouping alone would have made things worse. The hero search falls back to the buyers view,
so searching DEDEMAN — a supplier — landed there and reported "Niciun rezultat", which
reads as "not in the data" rather than "wrong aspect". Hiding four tabs behind a dropdown
without fixing that just buries the confusion. So an empty result inside the group now
probes the siblings and says where the name actually appears.

Source assertions, like the other site tests. The behaviour was verified in a browser:
searching DEDEMAN reports "apare la Vânzători (22 de rânduri)" in ~1.2s.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_the_four_organisation_views_share_one_tab(source: str) -> None:
    assert "const ENTITY_GROUP" in source
    assert source.count("group: ENTITY_GROUP") == 4, (
        "exactly four views belong behind the Entități tab"
    )


def test_the_group_tab_is_emitted_once(source: str) -> None:
    """Four grouped views, one tab. Without the guard it is four identical tabs."""
    assert "if (entityTabPlaced) continue;" in source
    assert "entityTabPlaced = true;" in source


def test_the_shared_tab_stays_selected_across_the_group(source: str) -> None:
    """Moving the selector must not leave the strip with nothing highlighted."""
    assert "b.dataset.key === ENTITY_GROUP" in source


def test_an_empty_result_looks_for_the_name_elsewhere(source: str) -> None:
    """The point of the grouping. A supplier searched from the buyers view must be found."""
    assert "function suggestOtherAspect(" in source
    assert "ASPECT_PROBES" in source
    body = source.split("async function suggestOtherAspect(")[1][:1400]
    assert "Promise.all" in body, (
        "probes must run concurrently; in sequence the reader watches 'no results' for "
        "seconds before it changes"
    )
    assert "found.sort" in body, (
        "the biggest match must win, or a supplier is answered with its one-row ANAF entry "
        "while its actual sales sit in the next view along"
    )


def test_the_hint_names_a_tab_that_exists(source: str) -> None:
    """"Autorități" stopped being a tab; sending a reader to look for it is worse than
    saying nothing."""
    assert "vă duce la Entități" in source
    assert "vă duce la Autorități" not in source


def test_romanian_row_counts_agree_with_the_number(source: str) -> None:
    """1 rând, 2 rânduri, 20 DE rânduri. The page was saying "1 rânduri"."""
    assert "function randuri(" in source
    body = source.split("function randuri(")[1][:400]
    assert "'1 rând'" in body or '"1 rând"' in body
    assert "last >= 20" in body, "the 'de' form above twenty is part of the rule"
    # And it must actually be used, not merely defined.
    assert "randuri(table.numRows)" in source
