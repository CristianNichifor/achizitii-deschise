"""Nobody had ever checked whether this page can be used without a mouse or with a screen
reader — on a public-interest tool, in a country where that is a legal obligation for
public bodies and a courtesy for everyone else.

Audited 2026-09-08 with axe-core 4.13.0 against WCAG 2.0/2.1/2.2 A + AA + best-practice,
over eight views in both colour schemes: the front door, a results table, prices, the
entity file, the entity chooser, the meetings feed, the drill-down and a signal.

Five violations, four of them introduced during this same week's interface work. All
fixed; the audit now returns **no violations across 8 views x 2 schemes**.

    SERIOUS  color-contrast    #run, white on --accent, 2.44:1 in dark mode
    SERIOUS  color-contrast    .meet.anulata dimmed muted text to 3.19:1
    SERIOUS  target-size       front-door signal buttons, 23.2px of safe space
    SERIOUS  scrollable-region-focusable   a wide table scrollable only by mouse
    MODERATE heading-order     the entity chooser jumped h1 -> h3

Keyboard operation, which axe cannot test, was walked separately in Chromium:

    tab order          search -> Caută -> caveat -> 9 tabs -> content, in visual order
    wide table         focusable; 12x ArrowRight scrolls it 110px
    fitting table      no tabindex, so no junk tab stop
    Enter on a price row     opens the drill-down
    Enter on a column header sorts, and writes the sort into the URL
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2000) -> str:
    chunk = source.split(decl)[1][:chars]
    return "\n".join(
        line for line in chunk.splitlines() if not line.lstrip().startswith("//")
    )


def test_text_on_the_accent_has_its_own_colour(source: str) -> None:
    """`--accent` is a dark navy on white and a light blue on the dark surface, because it
    also has to work as link colour against each background. Used as a button FILL, the
    light version left white text at **2.44:1** — the primary action on the page, failing
    AA by a wide margin. Dark text on that same blue is 7.71:1.
    """
    assert "--on-accent: #fff;" in source
    assert "--on-accent:#0e1216;" in source
    # And nothing may hard-code white on an accent fill any more.
    for rule in ("#run {", ".hero button {"):
        block = _body(source, rule, 220)
        assert "color:#fff" not in block, f"{rule} still hard-codes white on the accent"
        assert "var(--on-accent)" in block


def test_a_cancelled_meeting_is_not_marked_by_dimming_it(source: str) -> None:
    """`opacity:.72` took the muted text from 5.84:1 to 3.19:1 — and dimming is signalling
    by appearance alone, for something the "anulată" badge beside it already says in
    words."""
    assert ".meet.anulata { opacity" not in source
    assert ".meet.anulata { border-color:var(--warn); }" in source
    assert "flag.textContent = 'anulată';" in source, "the badge is what carries the fact"


def test_the_signal_list_meets_the_target_size(source: str) -> None:
    """WCAG 2.2 asks for 24px. The front door's signal buttons had 23.2px of safe
    clickable space between them."""
    assert ".pan .legend li button { padding:.3rem 0; min-height:1.5rem; }" in source


def test_a_table_that_scrolls_sideways_can_be_scrolled_by_keyboard(source: str) -> None:
    """`.wrap` is `overflow-x:auto`. A keyboard user could tab to every row of the entity
    file and still never reach the columns past the right edge — which is where the money
    is. Verified: focus the region, twelve ArrowRights, 110px."""
    assert "function makeScrollableRegionsFocusable(" in source
    body = _body(source, "function makeScrollableRegionsFocusable(", 1200)
    assert "w.scrollWidth > w.clientWidth + 1" in body
    assert "w.tabIndex = 0;" in body
    assert "setAttribute('role', 'region')" in body
    assert "aria-label" in body, "a bare region announces as 'region' and says nothing"


def test_a_table_that_fits_adds_no_tab_stop(source: str) -> None:
    """Applying it unconditionally would put a tab stop in front of every table on the
    page, including the phone layout where `.wrap` does not scroll at all — degrading
    keyboard navigation everywhere to fix it in one place."""
    body = _body(source, "function makeScrollableRegionsFocusable(", 1200)
    assert "w.removeAttribute('tabindex');" in body
    assert "const scrolls =" in body


def test_the_focus_helper_runs_after_every_view(source: str) -> None:
    """Overflow cannot be measured before the rows are in the document, and the three
    paths that draw tables — the front door, the entity file, the results view — each end
    somewhere different."""
    assert source.count("makeScrollableRegionsFocusable()") == 3


def test_headings_do_not_skip_a_level(source: str) -> None:
    """The entity file's sections are h3 beneath the h2 in `#dosar-cap`. When the chooser
    is drawn instead, that h2 does not exist — so its heading followed the page h1
    directly."""
    body = _body(source, "  const box = document.createElement('section');", 700)
    assert "document.createElement('h2')" in body
    assert "document.createElement('h3')" not in body
    # And it must not suddenly look like a page title.
    assert ".sect > h3, .sect > h2 {" in source


def test_the_interactive_table_rows_answer_the_keyboard(source: str) -> None:
    """A row that only responds to a mouse is not a control. Both the drillable price rows
    and the sortable headers were verified end to end: Enter opens the drill-down, Enter
    sorts and writes the order into the URL."""
    assert source.count("e.key === 'Enter' || e.key === ' '") >= 2
    assert "th.tabIndex = 0;" in source
    assert "tr.tabIndex = 0;" in source
