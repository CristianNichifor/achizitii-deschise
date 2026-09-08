"""Every view had one order and a hard ceiling of 200 rows.

The 201st largest supplier did not exist as far as this page was concerned. "Which
authority buys the least" could not be asked at all — every view was ordered by value
descending and there was no way to turn it round. The status line said "(limitat la 200)",
which was true and was a dead end: the reader was told rows had been withheld and given
no way to reach them.

harta-firmelor.ro paginates its search with numbered pages and a total. This is the same
idea, applied to a page where the rows come out of Parquet over HTTP range requests.

THE RULE THIS TURNS ON:

    SORTING RE-QUERIES. IT DOES NOT REORDER THE PAGE ALREADY FETCHED.

Reordering 200 fetched rows would be cheaper and would answer a different question.
Ascending by value over the top 200 by value gives the smallest of the largest, presented
as the smallest. On a page whose entire purpose is letting people check figures, a control
that quietly answers a different question than the one it appears to ask is worse than no
control at all.

Verified in Chromium against the published data:

    #v=furnizori                      Rândurile 1–200, LIMIT 201 OFFSET 0
    next page                         Rândurile 201–400, OFFSET 200, different rows
    sort by valoare, descending       first row 89.429.511 RON
    sort by valoare, ascending        first row 0 RON
                                      -> below the descending page's LAST row (16.4 mil),
                                         which a client-side flip could never produce
    #v=judete&pag=3                   Rândurile 401–462 (ultimele), next disabled
    #v=furnizori&pag=99999            recovered to page 1 and corrected the address bar
    #v=furnizori&sort=n;%20DROP…      order dropped, default order used, no SQL touched
    #v=indicatori&s=estimare-01       now pages through 9.370 findings, not 200 of them
    Back after two next-presses       returned to Pagina 2
    320 / 390px                       no horizontal bleed
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2000) -> str:
    """A slice of the source with its `//` comments removed.

    Stripping is the default here because the alternative keeps biting. A guard that
    searches for a removed string finds the comment explaining why it was removed, and
    the test fails on its own documentation. That has happened three times in this suite
    now — once on the SEAP client guard, once on entityContext, once here — so the
    helper does it rather than each test remembering to.
    """
    chunk = source.split(decl)[1][:chars]
    return "\n".join(
        line for line in chunk.splitlines() if not line.lstrip().startswith("//")
    )


def test_sorting_reaches_the_database(source: str) -> None:
    """The rule the whole feature turns on. If the chosen order did not reach SQL, the
    control would silently answer a different question than the one it appears to ask."""
    body = _body(source, "    tail(implicit, override) {", 600)
    assert "sortCol" in body and "ORDER BY ${order}" in body, (
        "the reader's order must be built into the query, not applied to the result"
    )


def test_the_sort_column_is_whitelisted_not_escaped(source: str) -> None:
    """It reaches SQL as an identifier, and it arrives from the address bar as well as
    from a click. No quotes, spaces or parentheses survive this pattern, and anything
    that does not match is dropped rather than passed through."""
    assert "const SORTABLE = /^[a-z_][a-z0-9_]{0,63}$/;" in source
    assert "const validSort = (c) => typeof c === 'string' && SORTABLE.test(c);" in source
    # Both routes in: the click and the link.
    assert "if (sortable && validSort(c))" in source
    assert "sortCol = validSort(p.get('sort')) ? p.get('sort') : '';" in source
    # And the SQL builder refuses anything that did not pass.
    body = _body(source, "    tail(implicit, override) {", 600)
    assert "validSort(sortCol)" in body


def test_the_next_page_is_known_without_counting(source: str) -> None:
    """One row more than the page shows. A COUNT over the whole file to draw one arrow
    would be the most expensive query on the page."""
    body = _body(source, "    tail(implicit, override) {", 600)
    assert "LIMIT ${n + 1} OFFSET ${pagina * n}" in body
    assert "const more = table.numRows > size;" in source


def test_the_probe_row_is_never_drawn(source: str) -> None:
    """It exists to answer "is there more", not to be read. Drawn, the page would show 201
    rows on a 200-row page and every page would overlap the next by one."""
    assert "maxRows: size," in source
    assert "table.toArray().slice(0, maxRows)" in source
    assert "const shown = Math.min(table.numRows, size);" in source
    # The chart is built from the same rows and must not see it either.
    assert "chartFor(spec, table, maxRows = Infinity)" in source
    assert "const rows = table.toArray().slice(0, maxRows);" in source


def test_the_dead_end_message_is_gone(source: str) -> None:
    """"(limitat la 200)" told the reader rows had been withheld and offered no way to
    reach them. Now the range says which rows these are and the pager says there are
    more."""
    # Scoped to render(). The entity file's Semnale section still says "(limitat la 200)"
    # and should: it is a section of a file that does not paginate, and saying a ceiling
    # is a ceiling is the honest thing there. What had to go is the version that appeared
    # on a view where turning the page was the obvious next move and there was none.
    body = _body(source, "function render(table) {", 3000)
    assert "(limitat la 200)" not in body
    assert "Rândurile ${from.toLocaleString('ro-RO')}" in body


def test_a_page_number_belongs_to_its_result_set(source: str) -> None:
    """Page 4 of the old result is not page 4 of the new one. Every change that produces
    a different set of rows starts again at the first page."""
    assert "function rerun() {" in source
    for control in ("an", "baza", "minn"):
        assert f"$('{control}').addEventListener('change', rerun);" in source
    # A new sort order, a new search, and a new view.
    assert "pagina = 0;   // a different order makes the old page number meaningless" in source
    assert "if (!applying && key !== current) { pagina = 0; sortCol = ''; sortDir = 'desc'; }" in source


def test_a_link_still_decides_where_it_points(source: str) -> None:
    """selectTab resets the order and the page when the view changes — which would undo
    the ones applyHash had just read out of the hash. The link wins."""
    assert "if (!applying && key !== current)" in source


def test_a_stale_page_number_recovers(source: str) -> None:
    """Page 4 of a result that just shrank to two pages is empty, and "relax your filters"
    is the wrong advice: the filters are fine, the page number is stale."""
    body = _body(source, "  if (!table.numRows) {", 500)
    assert "if (pagina > 0)" in body
    assert "Pagina aceasta nu mai există" in body
    assert "pushNext = false;" in body, "recovering must not leave a dead entry in history"


def test_where_in_the_results_is_part_of_the_link(source: str) -> None:
    """A link to "the cheapest unit prices, page 3" is the kind of thing worth sending
    someone. 1-based in the link because that is what the pager shows."""
    assert "'sort', 'dir', 'pag'," in source
    assert "if (validSort(sortCol)) { s.sort = sortCol; s.dir = sortDir; }" in source
    assert "if (pagina > 0) s.pag = String(pagina + 1);" in source


def test_the_pager_is_absent_when_everything_fits(source: str) -> None:
    """A disabled pager under a 41-row summary is furniture."""
    body = _body(source, "function showPager(", 500)
    assert "nav.hidden = !(pagina > 0 || more);" in body
    # And the entity file never paginates, so it hides it outright.
    assert "if (isDosar) { $('grafic').textContent = ''; $('pager').hidden = true; }" in source


def test_the_drill_down_is_not_paginated_away(source: str) -> None:
    """It is the evidence behind a single median — the one thing on this site a reader is
    meant to read whole. Paging it 200 at a time would break that up."""
    assert "return key === 'detaliu' ? 500 : PAGE;" in source
    assert "f.tail('pret_unitar_ron DESC NULLS LAST', 500)" in source


def test_turning_a_page_moves_the_reader_to_the_top(source: str) -> None:
    """They press this at the bottom of a table. Without it they land at the bottom of the
    NEXT one, having seen none of it."""
    body = _body(source, "function turnPage(", 500)
    assert "scrollIntoView" in body


def test_the_sort_state_is_announced_not_only_drawn(source: str) -> None:
    """An arrow glyph is not a state a screen reader can report."""
    assert "th.setAttribute('aria-sort'" in source
    assert "th.tabIndex = 0;" in source
    assert "e.key === 'Enter' || e.key === ' '" in source


def test_a_new_column_starts_at_the_interesting_end(source: str) -> None:
    """On this page the interesting end of almost every column is the large one. Clicking
    the column already sorted flips it."""
    assert "sortDir = active && sortDir === 'desc' ? 'asc' : 'desc';" in source


def test_every_paginated_view_uses_the_shared_tail(source: str) -> None:
    """A view that kept its own `LIMIT 200` would silently opt out of paging, and the
    reader would meet the old dead end on that tab alone."""
    assert source.count("f.tail(") >= 11
    # No view may hard-code the page ceiling any more. Scoped to the VIEWS object: the
    # entity file's own queries are separate, are not paginated, and keep their caps.
    views = source.split("const VIEWS = {")[1].split("\n// One institution,")[0]
    assert "LIMIT 200" not in views, "a view is opting out of paging with its own LIMIT"
    assert views.count("f.tail(") == 10, "every view in VIEWS must use the shared tail"
