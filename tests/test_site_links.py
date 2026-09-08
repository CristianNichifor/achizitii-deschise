"""Every view has an address, and Back goes back.

The site had exactly ONE URL. A tab, a search, a drill-down into the acquisitions behind
a median — all of it looked identical to the address bar, so a finding could not be
linked to. "Look at what this supplier charges for these" meant a screenshot, and browser
Back left the site instead of returning to the previous view. For a page whose stated
purpose is letting other people check the figures, an uncitable figure is barely
published at all.

State now lives in the hash. Not the query string: this is served as static files from
GitHub Pages, where a path or query the server has never heard of returns 404, while
everything after "#" never reaches a server.

Source assertions, like the other site tests. The behaviour was verified in a real
browser against the published data — Chromium, full page loads, not hash-only
navigations, which do not reload the document and hid three bugs on the first pass:

    #v=sumar                      41 rows, tab Sumar, title "Sumar — …"
    #v=preturi&minn=5             195 rows (200 without the filter)
    Back / Forward                returned to Sumar and back to the filtered prices
    #v=detaliu&cpv=03222111&p=banane&um=kg&din=2026-07-01&pana=2026-07-06
                                  42 rows, identical to the rows reached by clicking
    #v=furnizori&q=dedeman        22 rows, Entități tab, Vânzători aspect
    #v=nu-exista                  fell back to Sumar AND corrected the address bar
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2000) -> str:
    return source.split(decl)[1][:chars]


def test_state_is_carried_in_the_hash_not_the_path(source: str) -> None:
    """GitHub Pages has no router. A path or query it does not recognise is a 404, so a
    shared link has to survive without the server understanding any of it."""
    assert "function applyHash(" in source
    assert "location.hash" in source
    body = _body(source, "function applyHash(")
    assert "new URLSearchParams(location.hash" in body


def test_a_view_change_is_a_history_entry_and_a_filter_tweak_is_not(source: str) -> None:
    """Otherwise Back becomes an undo button for typing: nudging the year or the group
    size would each leave an entry and it would take a dozen presses to leave the page."""
    assert "history[push ? 'pushState' : 'replaceState']" in source
    assert "pushNext = true;" in source
    # The pushing places: choosing a view, and searching. Both are navigations.
    assert "function selectTab(" in source
    assert source.count("pushNext = true;") >= 2


def test_back_and_forward_move_between_views(source: str) -> None:
    assert "window.addEventListener('popstate'" in source


def test_a_broken_link_corrects_the_address_bar(source: str) -> None:
    """Landing on the summary while the URL still says `#v=nu-exista` means the reader
    copies and passes on the broken address. Correcting it must not add a history entry,
    or Back lands on the dead link and bounces straight back to where it came from."""
    assert "function goDefault(" in source
    assert "let replaceOnly = false;" in source
    assert "const push = pushNext && !replaceOnly;" in source
    body = _body(source, "function goDefault(", 400)
    assert "replaceOnly = true;" in body
    assert "finally { replaceOnly = false; }" in body


def test_reading_a_link_does_not_immediately_rewrite_it(source: str) -> None:
    """applyHash sets the controls, which fires the query, which writes the URL — so
    without the guard, opening a link rewrites the link that was just opened."""
    assert "let applying = false;" in source
    assert "if (applying) return;" in source


def test_the_url_is_written_before_the_first_await(source: str) -> None:
    """The `applying` guard only holds while applyHash's own stack frame is live. If
    syncUrl ran after an await it would fire once applying had already been cleared, and
    every shared link would be rewritten on arrival."""
    # Comments stripped first: the comment explaining this rule contains the word
    # "await", which truncated the prefix before the call it was asserting on.
    code = "\n".join(
        line for line in _body(source, "async function run() {", 600).splitlines()
        if not line.lstrip().startswith("//")
    )
    prefix = code.split("await")[0]
    assert "syncUrl();" in prefix, "syncUrl must run synchronously at the top of run()"


def test_the_tab_and_the_history_entry_are_named(source: str) -> None:
    """A Back menu of twenty identical lines is not navigation. The title is set ABOVE
    the applying guard, because opening a shared link is the one path that writes no hash
    and is exactly when the name matters most."""
    assert "function titleFor(" in source
    body = _body(source, "function syncUrl() {", 600)
    assert body.index("document.title = titleFor();") < body.index("if (applying) return;"), (
        "the title must be set before the guard, or a shared link keeps the previous "
        "view's name"
    )


def test_a_search_term_is_only_linked_where_it_searches(source: str) -> None:
    """The box keeps its text when the reader moves to the summary, so they can carry it
    to a view that uses it — but publishing `&q=` on a view that ignores it is a link
    that promises a filter and applies none."""
    assert "const searchable = current !== 'indicatori'" in source
    assert "if (f.q && searchable) s.q = f.q;" in source


def test_the_drill_down_group_survives_a_reload(source: str) -> None:
    """A price group is CPV + product + unit + pack size, and ANY of those may legitimately
    be null — "no unit of measure" is a real group, not a missing value. The link has to
    keep null and empty-string apart or a shared drill-down opens the wrong group."""
    body = _body(source, "function applyHash(")
    for key in ("cpv", "p", "um", "mp"):
        assert f"p.has('{key}')" in body, (
            f"'{key}' must distinguish an absent parameter (null) from an empty one"
        )
    assert "Number(p.get('mp'))" in body, (
        "pack size must come back as a number: the drill-down SQL emits a bare literal "
        "for numbers and a quoted one for text, and '6' never matches 6"
    )


def test_the_drill_down_link_carries_its_date_window(source: str) -> None:
    """Without it the shared link opens every file in an archive that grows by one a day
    forever, so the link would get slower every day it existed."""
    body = _body(source, "function currentState() {", 2800)
    assert "s.din = dayOf(drill.din)" in body
    assert "s.pana = dayOf(drill.pana_la)" in body


def test_the_group_size_control_is_connected(source: str) -> None:
    """It had no listener at all. Typing a group size did nothing until the reader
    happened to press Caută, so on the views with no search box — the summary, contracts —
    it simply looked broken. Measured in a browser: prices went 200 rows to 195."""
    # Through rerun(), which resets the page first: changing the group size changes the
    # result set, and page 4 of the old one is not page 4 of the new one.
    assert "$('minn').addEventListener('change', rerun);" in source


def test_the_reader_is_told_the_link_exists(source: str) -> None:
    """A hash that changes silently is an implementation detail. Nobody watches the
    address bar."""
    assert 'id="copiaza"' in source
    assert "navigator.clipboard.writeText" in source
    body = _body(source, "$('copiaza').addEventListener", 600)
    assert "catch" in body, (
        "clipboard needs a secure context; serving the folder over plain HTTP locally "
        "must say where the link is rather than fail silently"
    )
