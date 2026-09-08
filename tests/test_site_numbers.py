"""Money has to be readable, and the page has to fit a phone.

Two problems, both visible in a screenshot of the shipped page:

* Totals rendered as `3.854.469.918`. You have to count dot groups to learn whether that
  is millions or billions — which is precisely the comparison the table exists to make.
* On a 390px phone the eleven tabs wrapped onto FOUR rows, and together with the hero and
  the caveat that pushed the table entirely below the fold. Zero figures visible without
  scrolling, on a page whose only purpose is the figures.

Source-text assertions, like `test_site_boot`. The behaviour itself was verified by
driving a real browser at 390px and 320px; these exist so the fixes are not quietly
undone. Each was checked to fail when the thing it guards is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_large_sums_are_abbreviated(source: str) -> None:
    assert "function ron(" in source, "money needs its own formatter"
    body = source.split("function ron(")[1][:600]
    assert "mld" in body and "mil" in body, (
        "Romanian scale words, not SI suffixes: 'B' is ambiguous between billion and miliard"
    )
    assert "1e9" in body and "1e6" in body, "the thresholds must be explicit"


def test_the_exact_figure_survives_abbreviation(source: str) -> None:
    """Rounding for display must never be rounding for the record.

    `2,96 mld` is a reading aid; the real number stays reachable on hover, and the SQL box
    below the table still shows the query that produced it.
    """
    assert "td.title = `${Number(v).toLocaleString('ro-RO')} RON`" in source, (
        "an abbreviated cell must carry its exact value"
    )


def test_only_money_columns_are_abbreviated(source: str) -> None:
    """A year, a CUI or a CPV code is a number but not a quantity.

    Abbreviating a row count to "1,7 mil" would be fine; abbreviating an identifier would
    corrupt it. The rule keys off the `_ron` suffix the schema already uses.
    """
    assert "const money = (c) => c.endsWith('_ron');" in source


def test_the_tab_strip_does_not_wrap_on_a_phone(source: str) -> None:
    """Four rows of tabs pushed every figure off a 390px screen."""
    assert "max-width: 640px" in source, "there must be a phone breakpoint"
    phone = source.split("max-width: 640px")[1][:700]
    assert "flex-wrap:nowrap" in phone.replace(" ", "")
    assert "overflow-x:auto" in phone.replace(" ", "")


@pytest.mark.parametrize("selector", [".hero input", ".controls > div"])
def test_flex_children_can_shrink(source: str, selector: str) -> None:
    """`min-width:0`, twice, for the same reason both times.

    A flex item will not shrink below the intrinsic width of its content, so a text input
    or a select keeps its natural size and pushes the page sideways. Measured at 320px:
    the search button sat at x=350 in a 320px viewport until this was added.
    """
    block = source.split(selector)[1][:220]
    assert "min-width:0" in block.replace(" ", ""), (
        f"{selector} must be allowed to shrink, or narrow screens scroll horizontally"
    )
