"""The results on a phone, where they were effectively invisible.

Measured at 390px before this change, with the table scrolling sideways inside its box:

    Sumar              3 of 7 columns visible
    Prețuri unitare    1 of 12   (table 1816px wide in a 390px viewport)
    Semnale            1 of 9    (table 1893px)

The one visible column was the CPV code — an identifier and not a single figure, on a page
whose entire purpose is the figures. Each row becomes a card below 640px: every field on
its own line, its column header beside it, nothing to scroll horizontally.

Source assertions. The behaviour and every measurement above come from driving a browser
at 390, 800 and 1280px.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_rows_become_cards_on_a_phone(source: str) -> None:
    phone = source.split("max-width: 640px")[1][:2600]
    assert "#out thead { display:none; }" in phone, "the header row moves into the cells"
    assert "#out td::before" in phone, "each cell must carry its own label"
    assert "attr(data-label)" in phone


def test_every_cell_carries_its_column_header(source: str) -> None:
    """Without this the cards are unlabelled numbers."""
    assert "td.dataset.label = label(c);" in source


def test_empty_cells_are_dropped_from_cards(source: str) -> None:
    """A dash costs a whole line on a card, and says nothing."""
    assert "td.classList.add('empty')" in source
    assert "#out td.empty { display:none; }" in source


def test_the_caveat_headline_is_never_hidden(source: str) -> None:
    """Only the elaboration folds on a phone; the warning itself is the summary.

    Measured: the banner is 45px closed and 164px open, so the fold is real — but what it
    folds is explanation, not the caveat.
    """
    banner = source.split('<details class="banner"', 1)[1][:600]
    assert "<summary><strong>Cifrele arată tipare, nu concluzii.</strong></summary>" in banner
    assert "open>" in source.split("<details class=\"banner\"", 1)[1][:40], (
        "it must default to open, so a reader without JavaScript gets the whole caveat"
    )


def test_the_collapsed_caveat_says_that_it_opens(source: str) -> None:
    """The default triangle is hidden, so something else has to signal it is interactive.

    Otherwise the collapsed banner is text that silently swallows taps.
    """
    assert ".banner:not([open]) > summary::after" in source
    assert "cursor:pointer" in source.split(".banner > summary {", 1)[1][:120]


def test_an_empty_hint_does_not_reserve_a_line_on_a_phone(source: str) -> None:
    """min-height guards against layout shift on desktop, where the space is free.

    On a phone it was 32px of nothing above the data.
    """
    phone = source.split("max-width: 640px")[1][:2600]
    assert ".hint:empty { min-height:0; margin:0; }" in phone
