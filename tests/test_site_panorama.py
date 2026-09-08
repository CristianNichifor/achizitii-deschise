"""The site had no front door.

It opened on a 41-row table of year × category — a question nobody arrives with. Every
other tab was a search box waiting for a term a first-time visitor had no reason to guess.
harta-firmelor.ro leads with a claim and a chart; we led with a grid.

Four blocks, and the order is the argument: unit prices first because they are the only
thing here nobody else publishes; signals second because they are what a visitor expects
from a procurement site and the easiest place to leave a wrong impression; the largest
buyers third; and what the data actually covers last, because it qualifies everything
above it and has to be read after rather than skipped before.

Every figure on the screen is a link into a view that already existed. The front door
makes no claim the rest of the site does not already make.

Verified in Chromium against the published data:

    cold boot, no hash        -> #v=panorama, 4 blocks, table and pager hidden
    click a price row         -> #v=detaliu&cpv=64200000&p=servicii+de+telecomunicatii…
    click a buyer             -> #v=dosar&q=1590120, REGIA NATIONALA A PADURILOR
    click a signal            -> #v=indicatori&s=dependenta-01, 33 rows
    leaving the tab           -> blocks cleared, Sumar's 41 rows back
    an older link             -> still opens its own view, not the front door
    320 / 390 / 768px         -> no horizontal bleed, all four blocks
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 3000) -> str:
    chunk = source.split(decl)[1][:chars]
    return "\n".join(
        line for line in chunk.splitlines() if not line.lstrip().startswith("//")
    )


def test_the_front_door_is_what_opens(source: str) -> None:
    assert "panorama: {" in source
    assert "try { selectTab('panorama'); } finally { replaceOnly = false; }" in source
    assert "let manifest = null, conn = null, current = 'panorama'" in source


def test_a_link_still_beats_the_front_door(source: str) -> None:
    """Every address shared before this existed must keep opening what it named."""
    assert "if (!applyHash()) goDefault();" in source


def test_it_makes_no_claim_of_its_own(source: str) -> None:
    """Each block is a doorway into a view that already existed. A summary that computed
    its rows differently from the tab it links to would be a second source of truth."""
    body = _body(source, "async function runPanorama(", 9000)
    for target in ("selectTab('preturi')", "selectTab('detaliu')",
                   "selectTab('dosar')", "selectTab('indicatori', ind)"):
        assert target in body, f"no doorway to {target}"


def test_the_blocks_arrive_together(source: str) -> None:
    """Awaited in place, one query per block, the page assembled in stages — a screenshot
    caught it half-drawn. The entity file had the same fault."""
    body = _body(source, "async function runPanorama(", 3000)
    assert "Promise.all([" in body
    assert "const [preturi, cumparatori] = await cereri;" in body
    assert ".catch(() => null)" in body, "one failed query is one doorway fewer"


def test_the_signal_counts_cost_nothing(source: str) -> None:
    """The manifest already carries them. Eight COUNT(*) queries to fill a summary screen
    would be the most expensive thing on the fastest page."""
    body = _body(source, "const semnale = manifest.indicatori || [];", 1200)
    assert "ind.rows" in body
    assert "conn.query" not in body


def test_a_wide_price_spread_is_not_presented_as_wrongdoing(source: str) -> None:
    """The block most likely to be read as an accusation says otherwise in its own lede,
    not three paragraphs away."""
    assert "O diferență mare nu înseamnă că cineva a greșit" in source
    assert "niciodată „este ilegal”" in source


def test_the_coverage_block_says_which_figure_rests_on_what(source: str) -> None:
    """The header says 26,7 million acquisitions — true of the bulk exports. The unit
    prices rest on seven days. A reader could not tell which was which, and that is a
    correctness problem rather than a polish one."""
    assert "Ce acoperă datele" in source
    assert "zile colectate" in source
    assert "nu conțin cantități" in source, "and why it cannot be backfilled"


def test_hidden_columns_are_hidden_not_unfetched(source: str) -> None:
    """The price rows carry cpv, pack size and the date window because opening the right
    group needs all four — and showing a reader a pack size to explain a price does not."""
    assert "hide = []," in source
    assert ".filter((c) => !hide.includes(c))" in source
    assert "hide: ['cpv', 'marime_pachet', 'din', 'pana_la']," in source


def test_a_sum_keeps_its_thousands_separators(source: str) -> None:
    """sum() over BIGINT returns HUGEINT, and Arrow hands INT128 back as something that is
    neither a number nor a bigint — so the formatter fell through to String(v) and printed
    "17267" in a column where every other row read "17.267". Fixed at the source AND in
    the renderer, so the next sum() someone adds cannot reintroduce it."""
    assert "sum(n)::BIGINT AS n" in source
    assert "const wholeNumberText = typeof v === 'string' && /^-?\\d{1,30}$/.test(v);" in source
    assert "if (numeric) td.className = 'num';" in source, "and it aligns like a number"


def test_the_front_door_shares_the_results_slot(source: str) -> None:
    assert "$('wrap-out').hidden = isDosar || isFeed || isPan;" in source
    assert "if (!isPan) $('panorama').textContent = '';" in source
    assert "if (isPan) { $('grafic').textContent = ''; $('pager').hidden = true; }" in source


def test_the_hint_does_not_call_the_page_a_table(source: str) -> None:
    """It said "Acest tabel nu se filtrează după text" on a page with no table, and on the
    meetings register, which has a feed."""
    assert "Acest tabel nu se filtrează" not in source
    assert "Această pagină nu se filtrează după text" in source
