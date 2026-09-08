"""One organisation, every source, on one screen — and only ever ONE organisation.

harta-firmelor.ro gives each company a file: status, accounts, gazette entries and public
contracts together. We had the same material split across four aspects of a dropdown that
a reader had to choose between before knowing what any of them held — the page even grew
a "your search matches something over there" hint to cover for it. The file asks all four
at once, so there is nothing to choose.

The correctness rule this screen turns on, and the reason half the code here exists:

    A FILE ABOUT ONE ORGANISATION MUST NOT SUM SEVERAL.

The first working version broke it. Searching "romsilva" matched 5 ANAF records and 200
rows of contracting authorities — Regia Națională plus every Direcție Silvică carrying
the word — put one name in the heading and 563 million lei under it, as though a single
body had spent it. A name is now resolved to a list of distinct fiscal codes first: one
match opens the file, several ask which.

Verified in Chromium against the published data:

    #v=dosar&q=dedeman     -> 4 organizații to choose between
    click a row            -> #v=dosar&q=2816464, DEDEMAN SRL, BACĂU
                              a vândut 685,0 mil RON / 576.117 achiziții, 12 semnale,
                              "nu apare ca autoritate contractantă"
    #v=dosar&q=4340536     -> GRADINITA NR. 187, 6,9 mil RON bought, sells nothing
    #v=dosar&q=romsilva    -> 39 de organizații, no file drawn
    #v=dosar&q=zzzz…       -> four sections each saying separately that it found nothing
    320 / 390 / 1280px     -> no horizontal bleed; every non-empty cell on screen
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2500) -> str:
    return source.split(decl)[1][:chars]


def test_the_file_asks_every_source(source: str) -> None:
    """Identity, purchases, sales, signals. The point is that the reader chooses none of
    them."""
    assert "const DOSAR = [" in source
    for section in ("identitate", "cumparator", "furnizor", "semnale"):
        assert f"id: '{section}'," in source, f"the {section} section is missing"


def test_a_name_is_resolved_to_one_organisation_first(source: str) -> None:
    """The rule the whole screen turns on. Without this the heading names one body and
    the figures belong to dozens."""
    assert "function candidatesSql(" in source
    assert "async function disambiguate(" in source
    body = _body(source, "async function disambiguate(")
    assert "return null" in body, "several matches must draw a chooser, not a file"
    assert "GROUP BY cui" in source, "candidates are distinct organisations, not rows"


def test_a_fiscal_code_skips_the_chooser(source: str) -> None:
    """It already identifies exactly one organisation; asking would be noise."""
    body = _body(source, "async function disambiguate(", 600)
    assert "/^[0-9]{2,10}$/" in body


def test_a_fiscal_code_is_matched_exactly_in_the_file(source: str) -> None:
    """Everywhere else on the page a CUI is matched with ILIKE '%…%', which is fine for
    filtering a ranked list and wrong here: 4340536 would also pull in 14340536, and a
    file claiming to be about one organisation must not quietly merge two."""
    body = _body(source, "function matcher(")
    assert "if (cuiCol) parts.push(`${cuiCol} = ${lit(digits)}`);" in body
    assert "return parts.length ? parts.join(' OR ') : 'false';" in body, (
        "a table with nothing to match on must return nothing, not everything"
    )


def test_choosing_an_organisation_produces_an_exact_link(source: str) -> None:
    """The chooser sets the fiscal code as the search term, so the link that results
    names one organisation rather than a word that matched thirty-nine."""
    body = _body(source, "fillTable(tbl, cands,", 700)
    assert "$('q').value = String(row.cui);" in body
    assert "pushNext = true;" in body, "choosing an organisation is a navigation"


def test_an_empty_section_still_says_so(source: str) -> None:
    """"This company has never sold anything to the state" is an answer a reader came
    for. A section that simply vanishes cannot be told apart from one never asked."""
    assert "gol:" in source
    for phrase in (
        "Nu apare ca autoritate contractantă",
        "Nu apare ca furnizor",
        "Niciun semnal. Absența unui semnal nu este o atestare.",
    ):
        assert phrase in source


def test_a_total_miss_does_not_name_the_search_term_as_an_organisation(source: str) -> None:
    """The sections each report nothing separately, which is four answers — but putting
    the typed text in an <h2> above a strip of zeros presents a misspelling as a body."""
    body = _body(source, "  if (!found) {", 400)
    assert "Nimic despre" in body


def test_a_capped_count_is_not_reported_as_a_total(source: str) -> None:
    """200 is the query ceiling. Printing it bare states a number the page does not know,
    and the sums in the facts strip are computed from those same rows."""
    assert "nSem === 200 ? '200+'" in source
    assert "t.numRows === 200 ? ' (limitat la 200)' : ''" in source


def test_the_signal_count_is_never_shown_as_a_verdict(source: str) -> None:
    """A count of signals with no denominator is a smear. The card carries the caveat
    where the number is, not three paragraphs away, and the spend beside it is the
    denominator — a large hospital generates more findings because it buys more."""
    assert "'de verificat, nu de condamnat'" in source
    assert "fapt('A cumpărat'" in source
    assert "fapt('A vândut'" in source


def test_the_hero_search_lands_on_the_file(source: str) -> None:
    """Typing a company name and being shown a table of contracting authorities was the
    single most confusing thing the page did."""
    assert "const SEARCHABLE_FALLBACK = 'dosar';" in source


def test_the_sections_are_queried_concurrently(source: str) -> None:
    """In sequence the file assembles a section at a time and the reader watches it
    arrive in stages."""
    body = _body(source, "async function runDosar(")
    assert "await Promise.all(" in body
    assert ".catch(() => null)" in body, (
        "one failing section must not take the whole file down; the others still answer"
    )


def test_every_query_the_file_ran_is_shown(source: str) -> None:
    """The page's standing promise is that any figure can be traced. A view that runs
    four queries has to show four."""
    body = _body(source, "async function runDosar(")
    assert "$('sql').textContent = secs" in body
    assert "`-- ${s.titlu}" in body


def test_the_redundant_aspect_is_gone(source: str) -> None:
    """The old `entitate` aspect ran exactly the query the file's Semnale section runs.
    Offering "Fișă completă" and a strict subset of it in the same selector is the
    confusion this change exists to remove."""
    assert "aspect: 'Semnale — ce s-a marcat despre o entitate'" not in source
    # Comments stripped first. The source explains where entityContext went and why, and
    # a bare substring search flags its own explanation — the same way an earlier guard
    # in this suite flagged the docstring describing the endpoint it had removed.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )
    assert "entityContext(" not in code, "its dead helper must go with it"
    assert source.count("group: ENTITY_GROUP") == 4


def test_the_mobile_card_layout_covers_every_data_table(source: str) -> None:
    """These rules were written against #out, the single results table. The file draws
    four more, and without this each section would be a 1816px table in a 390px viewport —
    the exact fault the card layout was added to fix."""
    assert "#out td::before" not in source, "the card rules must not be keyed on one table"
    assert ".tbl td::before" in source
    assert 'id="out" class="tbl"' in source


def test_one_renderer_draws_every_table(source: str) -> None:
    """Money abbreviated, dates decoded from epoch milliseconds, a year without thousands
    separators, the header carried into each cell for the phone layout. Two copies of
    those rules would drift within a week."""
    assert "function fillTable(" in source
    assert source.count("function fillTable(") == 1
