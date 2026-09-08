"""The meetings register is a chronological log, and a grid was the wrong shape for it.

Measured at a 760px viewport, the RUTI table was **6.992px wide** — nine screens. Every
cell is nowrap and one column holds free text averaging 210 characters. Worse, `concluzii`
took 3.312px of that width to render a single dash, because it is empty in 569 of 666
rows; while `descriere` — the only field that is never empty, and the one that says what
the meeting was actually about — was not in the table at all.

One meeting, one card, newest first. Long text folds into a `<details>`, so an 11.999
character description is one click away rather than absent or ruinous.

DATE ONLY, NEVER A TIME. The stored timestamps cluster on 02:00 (136 rows) and 01:00 (34)
— those are date-only records carrying a timezone offset, not meetings held at two in the
morning — while 10:00–15:00 look like genuine times. Nothing distinguishes them row by
row, so printing any of them would be inventing a fact.

Verified in Chromium against the published data:

    #v=ruti                     200 cards, Întâlnirile 1–200, table hidden
    320 / 390 / 760px           no horizontal bleed; cards 291 / 361 / 720px wide
    institution facet           36 options, counted and ordered by count
    picking one                 112 meetings, #v=ruti&inst=…
    that link, opened cold      112 meetings — the bug this found is below
    #v=ruti&q=digitalizarii     33 meetings
    leaving the tab             feed cleared, table back, facet hidden
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2500) -> str:
    """A slice of the source with `//` comments removed — see test_site_paging."""
    chunk = source.split(decl)[1][:chars]
    return "\n".join(
        line for line in chunk.splitlines() if not line.lstrip().startswith("//")
    )


def test_the_register_is_a_feed_not_a_table(source: str) -> None:
    assert "feed: true," in source
    assert "function renderFeed(" in source
    body = _body(source, "function render(table) {", 2500)
    assert "if (feed) {" in body
    assert "renderFeed(table, size);" in body


def test_the_table_and_the_feed_are_alternatives(source: str) -> None:
    """Both visible would be the same 666 meetings twice, one of them unreadable."""
    assert "$('wrap-out').hidden = isDosar || isFeed;" in source
    assert "if (!isFeed) $('flux').textContent = '';" in source


def test_the_field_that_says_what_happened_is_shown(source: str) -> None:
    """`descriere` is never empty in 666 rows and was not in the table at all, while
    `concluzii` — empty in 569 of them — had a column 3.312px wide."""
    assert "descriere, locul_intalnirii, concluzii, anulat, justificare_anulare" in source
    body = _body(source, "function renderFeed(", 3000)
    assert "camp(dl, 'Descriere', row.descriere);" in body


def test_long_text_folds_rather_than_being_dropped_or_ruinous(source: str) -> None:
    """One description runs to 11.999 characters. In the card body it would bury the
    register; omitted, the reader loses the only field that explains the meeting."""
    body = _body(source, "function renderFeed(", 3000)
    assert "document.createElement('details')" in body
    assert "sum.textContent = 'Detalii';" in body


def test_only_the_date_is_shown(source: str) -> None:
    """Half the times are timezone artefacts of date-only records and half look real.
    Nothing tells them apart per row, so a time is never printed."""
    body = _body(source, "function renderFeed(", 3000)
    assert "dayOf(row.data_intalnirii)" in body, "dayOf truncates to YYYY-MM-DD"
    assert "toLocaleTimeString" not in source
    assert "%H:%M" not in source


def test_a_cancelled_meeting_is_marked_not_hidden(source: str) -> None:
    """That it was cancelled is itself the fact the register records."""
    assert "row.anulat ? ' anulata' : ''" in source
    assert "flag.textContent = 'anulată';" in source
    assert "camp(dl, 'Motivul anulării', row.justificare_anulare);" in source


def test_the_register_has_a_facet_to_navigate_by(source: str) -> None:
    """92 people across 35 institution names. A search box alone means guessing a name
    before you can look anything up."""
    assert "facet: {" in source
    assert "col: 'institutie'," in source
    assert "function fillFacet(" in source
    # Declared on the view, not hard-coded, so a second facet costs a block and nothing else.
    assert "const facet = VIEWS[key] && VIEWS[key].facet;" in source


def test_the_facet_is_ordered_and_counted(source: str) -> None:
    """35 institution names sorted alphabetically say nothing about where to look. Sorted
    by count, with the count shown, the first few entries ARE the answer to "who does this
    register mostly cover"."""
    assert "ORDER BY n DESC" in source
    body = _body(source, "async function fillFacet(", 2000)
    assert "Number(row.n).toLocaleString('ro-RO')" in body


def test_the_chosen_facet_is_a_variable_not_the_control(source: str) -> None:
    """The bug this found. applyHash() assigns the value before fillFacet() has created
    any <option>, and assigning a value that matches no option is silently dropped — so a
    shared link ran unfiltered, the options arrived a moment later, and nothing re-ran.
    Measured: a link to one ministry's 112 meetings opened all 666.
    """
    assert "let facetValue = '';" in source
    assert "facet: facetValue," in source, "the query must read the variable"
    assert "facetValue = p.get('inst') || '';" in source, "and the link must set it"
    assert "sel.value = facetValue;" in source, "the control catches up once it has options"


def test_the_facet_does_not_follow_the_reader_to_another_view(source: str) -> None:
    """It would filter by an institution that view has never heard of."""
    reset = _body(source, "if (!applying && key !== current) {", 200)
    assert "facetValue = ''" in reset


def test_choosing_a_facet_is_a_filter_not_a_page_move(source: str) -> None:
    assert "$('facet').addEventListener('change', () => { facetValue = $('facet').value; rerun(); });" in source


def test_a_facet_that_fails_to_load_is_not_a_broken_page(source: str) -> None:
    """The search box still reaches the same rows."""
    body = _body(source, "async function fillFacet(", 2000)
    assert "$('subnav-facet').hidden = true;" in body


def test_selects_leave_room_for_their_arrow(source: str) -> None:
    """The native dropdown arrow is drawn inside the control's padding box and takes
    about 16-20px on Chromium. At .6rem (9.6px) the longest option — "Fișă completă — tot
    ce știm despre o entitate" — ran under the chevron with about two pixels of daylight.
    """
    assert "select { padding-right: 1.8rem; }" in source
    # The shared rule stays put for inputs and buttons, which have no arrow to clear.
    assert "select, input, button { font:inherit; padding:.45rem .6rem;" in source


def test_there_is_still_exactly_one_phone_breakpoint(source: str) -> None:
    """Second time a new component arrived carrying its own @media block. Two blocks with
    the same query are a place for rules to drift apart, and three tests in this suite
    slice the source on that string and quietly measure the wrong one."""
    assert source.count("@media (max-width: 640px)") == 1
    phone = source.split("max-width: 640px")[1]
    assert ".meet { grid-template-columns:1fr;" in phone, (
        "the card's 7.5rem date gutter is a quarter of a 320px screen"
    )
