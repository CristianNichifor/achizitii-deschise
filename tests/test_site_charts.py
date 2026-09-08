"""The site had eight tabs and eight tables, and not one chart.

"Is spending going up?" is the first question anyone asks of a yearly series, and
answering it meant reading eleven numbers off a grid and holding them in your head.
harta-firmelor.ro leads with a 36-year cohort chart and a statistics page of nine chart
sections; we led with a table of forty-one rows.

Charts are drawn from the rows the view has ALREADY fetched, never from a query of their
own. That is the property worth protecting: whatever the reader filters, the chart and
the table underneath it cannot disagree, because they are the same rows drawn twice.
Verified in a browser — every drawn bar height matched the table's own summed values to
the percentage point across all eleven years.

Colours come from a validated categorical palette, checked with the data-visualisation
skill's own validator against THIS page's surfaces (#fff and #0e1216) rather than the
reference ones, all pairs, both modes:

    CVD separation      worst 9.2 light / 9.4 dark   (>= 8 target)
    Normal-vision       worst 24.0 light / 20.9 dark (>= 15 floor)
    Contrast vs surface #1baf7a at 2.82:1 on white — below 3:1

The last is a documented relief, legal only with visible labels or a table view. Both
are present, which is what several of the tests below hold in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 6000) -> str:
    return source.split(decl)[1][:chars]


def test_a_chart_is_drawn_from_the_rows_already_on_screen(source: str) -> None:
    """No query of its own. A chart that fetched separately could disagree with the table
    beneath it — different filters, different limits, two numbers for one fact."""
    assert "function chartFor(" in source
    body = _body(source, "function chartFor(")
    assert "conn.query" not in body, "the chart must never run its own query"
    assert "table.toArray()" in body


def test_the_chart_and_its_table_are_the_same_rows(source: str) -> None:
    """render() draws both from one Arrow table, and the file's sections do the same."""
    assert "const fig = chartFor(VIEWS[current] && VIEWS[current].chart, table, size);" in source
    assert "const fig = chartFor(s.chart, t);" in source


def test_years_are_drawn_in_order(source: str) -> None:
    """The summary's SQL is ordered `an DESC`. Drawn in row order, time would run
    backwards and every trend would read inverted."""
    body = _body(source, "function chartFor(")
    assert "xs.sort((a, z) => (Number(a) - Number(z))" in body


def test_a_single_column_is_not_a_chart(source: str) -> None:
    """Filter the summary to one year and there is one bar. A one-bar bar chart takes
    space to say less than the row below it already does."""
    assert "if (xs.length < 2 || !names.length || names.length > 3) return null;" in source


def test_a_series_with_no_value_is_not_drawn(source: str) -> None:
    """The summary has a fourth `categorie`: 26 rows across eight years whose category is
    NULL and whose n_valori_folosite is 0. Real data, and it stays in the table — but
    drawing it means a legend entry and a colour for a permanently invisible bar, and it
    pushed the chart past the three-series cap so nothing was drawn at all."""
    body = _body(source, "function chartFor(")
    assert ".filter((n) => [...bySeries.get(n).values()].some((v) => Number(v) > 0))" in body


def test_the_year_in_progress_is_marked_incomplete(source: str) -> None:
    """A year still being collected is not comparable with the ten finished ones beside
    it. Shown — hiding it would be worse — but visibly not a finished bar.

    Compared against the CLOCK, not against the data: if the archive stops being updated,
    the last year present is complete and must stop being marked as though it were not.
    """
    assert "const inProgress = String(new Date().getFullYear());" in source
    assert ".viz .col.partial .seg { opacity:.45; }" in source
    assert "an în curs" in source


def test_the_x_labels_live_outside_the_plot(source: str) -> None:
    """The geometry bug this cost. A bar's height is a percentage of its column, so a
    label sharing that column ate into the 100%, flex clamped the tallest bars, and three
    different years drew the same height — 2025, the largest, drew SHORTER than 2018. A
    chart that misreports which year is biggest is worse than no chart at all."""
    assert ".viz .xrow" in source
    assert "xrow.append(lab);" in source
    body = _body(source, "function chartFor(")
    assert "col.append(lab)" not in body, "the label must not sit inside the bar's column"


def test_the_peak_label_cannot_shrink_the_track(source: str) -> None:
    """Same failure, second route: a direct label in the flow would take height from the
    column the bars are measured against."""
    assert ".viz .top { position:absolute;" in source
    assert "box-sizing:content-box; padding-top:1rem;" in source, (
        "the headroom for the peak label must be padding outside the track, not part of it"
    )


def test_exactly_one_value_is_direct_labelled(source: str) -> None:
    """A number on every column is a table with extra steps, and the table is already
    below. There is also no y-axis: the only tick worth drawing is the maximum, and the
    tallest column already carries it — a corner label would be the same number twice."""
    body = _body(source, "function chartFor(")
    assert "if (i === peak)" in body
    assert "className = 'axis'" not in body
    assert ".viz .axis" not in source


def test_a_legend_is_present_for_more_than_one_series(source: str) -> None:
    """Identity must never rest on colour alone — and one of the three hues sits below
    3:1 against white, so this is half of the relief that palette check requires. The
    other half is the table directly beneath."""
    body = _body(source, "function chartFor(")
    assert "if (names.length > 1)" in body
    assert "className = 'legend'" in body


def test_the_legend_text_is_not_the_series_colour(source: str) -> None:
    """A light categorical hue is illegible as label text on white. The swatch beside the
    text carries identity; the text stays in ink."""
    assert ".viz .legend i { width:.7rem;" in source
    body = _body(source, "function chartFor(")
    assert "sw.style.background = `var(--serie-" in body
    assert "li.style.color" not in body


def test_dark_mode_is_selected_not_flipped(source: str) -> None:
    """The same three hues re-stepped for the dark surface and re-validated against it,
    where all three clear 3:1 — not an automatic inversion of the light values."""
    assert "--serie-1: #2a78d6; --serie-2: #eb6834; --serie-3: #1baf7a;" in source
    assert "--serie-1: #3987e5; --serie-2: #d95926; --serie-3: #199e70;" in source


def test_marks_are_separated_by_surface_not_by_a_border(source: str) -> None:
    """A stroke around each segment adds ink that is not data. The gap is the mechanism,
    which is why the bar itself is transparent — what shows through is the page."""
    assert ".viz .seg + .seg { margin-bottom:2px; }" in source
    assert "background:transparent" in source
    assert "max-width:24px" in source, "cap the mark; the band's leftover is air"


def test_the_hover_target_is_the_whole_column(source: str) -> None:
    """A bar's height is its value, so the shortest year would be a 24x33px target you
    have to land on."""
    body = _body(source, "function chartFor(")
    assert "col.title = names" in body
    assert "bar.title" not in body


def test_no_value_is_reachable_only_by_hovering(source: str) -> None:
    """A tooltip enhances; it never gates. Every figure in every chart is also a row in
    the table immediately below it."""
    assert "const fig = chartFor(VIEWS[current] && VIEWS[current].chart, table, size);" in source
    # drawn above the table, and the table is always rendered too
    assert "fillTable($('out'), table" in source
    # The same row window for both, so the chart cannot include a row the table omits.
    # The query fetches one row MORE than the page shows, to answer "is there a next
    # page"; that probe row must reach neither.
    assert "maxRows: size," in source


def test_leaving_a_view_does_not_strand_its_chart(source: str) -> None:
    """The entity file draws charts inside its own sections and never calls render(),
    which is what clears this. Without the line, moving from the summary to a company's
    file left the national spending chart above it, under that company's name."""
    assert "if (isDosar) { $('grafic').textContent = ''; $('pager').hidden = true; }" in source
    assert "$('grafic').textContent = '';" in _body(source, "function render(table) {", 300)


def test_stat_tile_values_use_proportional_figures(source: str) -> None:
    """tabular-nums gives every digit the width of a zero, which reads loose on a
    standalone value. Reserved for numbers that align vertically — table rows and the
    year labels under a chart."""
    body = _body(source, ".fapte dd {", 200)
    assert "tabular-nums" not in body
    assert ".viz .xlab" in source and "tabular-nums" in _body(source, ".viz .xlab {", 200)
