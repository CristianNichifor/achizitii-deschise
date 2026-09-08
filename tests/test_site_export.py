"""A finding you cannot save is a finding you cannot cite.

Until this, the only ways out of the page were a screenshot and the SQL panel. A
journalist who had narrowed 26,7 million acquisitions down to thirty-three rows had no
way to put them in a spreadsheet, and a researcher had to clone a repository to read a
table.

Two rules carry the whole feature, and both are about not lying in a file:

1. RAW VALUES, NEVER THE DISPLAYED ONES. The table abbreviates 3.854.469.918 to
   "3,85 mld" and localises decimals, because that is what a person reads. A file
   carrying "3,85 mld" into a spreadsheet would be worse than no file: it looks like
   data and is not.

2. THE WHOLE RESULT, NOT THE PAGE. The reader filtered to something; the 200-row window
   is an artefact of reading it on a screen.

Verified in Chromium, downloading real files:

    #v=furnizori&q=dedeman   19 on screen, 19 in the file
    #v=furnizori            200 on screen, 100.000 in the file, and the page says so
    #v=sumar                 41 rows, 1.848 bytes
    #v=ruti&q=digitalizarii  33 meetings, commas and newlines inside quoted fields
    #v=detaliu               167 ms; the 100.000-row export 4,5 MB in 1.050 ms
    money column             89429511 — no "mld" or "mil" anywhere in any file
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(source: str, decl: str, chars: int = 2500) -> str:
    chunk = source.split(decl)[1][:chars]
    return "\n".join(
        line for line in chunk.splitlines() if not line.lstrip().startswith("//")
    )


def test_the_file_carries_raw_values(source: str) -> None:
    """`ron()` turns 3.854.469.918 into "3,85 mld" for the screen. In a CSV that is not
    a number, it is a caption."""
    body = _body(source, "function csvCell(", 900)
    assert "ron(" not in body, "the display formatter must never touch an exported cell"
    assert "toLocaleString" not in body, "and neither may Romanian digit grouping"
    assert "String(v)" in body, "numbers go out as numbers"
    assert "toISOString().slice(0, 10)" in body, "dates go out ISO, not localised"
    assert "v ? 'true' : 'false'" in body, "booleans go out as booleans, not da/nu"


def test_the_file_is_the_whole_result_not_the_page(source: str) -> None:
    """A file of exactly the 200 rows that happened to be visible would be a trap."""
    assert "function exportFilters(" in source
    body = _body(source, "function exportFilters(", 700)
    assert "LIMIT ${EXPORT_MAX + 1}" in body
    assert "OFFSET" not in body, "the page offset must not reach the export"
    # It keeps the reader's chosen ORDER BY, because that is part of what they asked for.
    assert "validSort(sortCol)" in body


def test_a_truncated_file_says_so(source: str) -> None:
    """Discovering the cap by counting rows in a spreadsheet is how a wrong total ends
    up in print."""
    assert "const EXPORT_MAX = 100000;" in source
    body = _body(source, "async function exportCsv(", 2000)
    assert "const capped = table.numRows > EXPORT_MAX;" in body
    assert "rezultatul are mai multe" in body
    assert "Parquet" in body, "and it points at where the whole set actually lives"


def test_values_that_could_break_a_row_are_quoted(source: str) -> None:
    """Romanian institution names carry commas — "Ministerul Economiei, Digitalizarii,
    Antreprenoriatului si Turismului" — and RUTI descriptions carry newlines."""
    body = _body(source, "function csvCell(", 900)
    assert r'/[",\n\r]/.test(t)' in body
    assert 't.replace(/"/g, \'""\')' in body


def test_the_file_opens_in_excel_with_its_diacritics(source: str) -> None:
    """Without a byte-order mark Excel reads UTF-8 as Latin-1 and every diacritic in
    every institution name arrives mangled."""
    body = _body(source, "async function exportCsv(", 2000)
    assert "﻿" in body, "a BOM must lead the file"


def test_the_header_names_the_schema_not_the_page(source: str) -> None:
    """A file is read by a program more often than by a person, and the names have to
    match the schema the repository documents — `valoare_totala_ron`, not
    "valoare totală (RON)"."""
    body = _body(source, "function toCsv(", 1200)
    assert "table.schema.fields.map((f) => f.name)" in body
    assert "label(" not in body


def test_the_inflation_base_is_in_the_filename(source: str) -> None:
    """The export carries whatever the view shows, which is right. But with adjustment
    on, the summary's 2025 services line reads 5.303.499.883,74 where the nominal file
    reads 7.670.355.032 — two files, one name, distinguished only by a dropdown the
    reader set minutes ago."""
    body = _body(source, "function exportName(", 900)
    assert "lei-${$('baza').value}" in body


def test_the_button_is_absent_where_there_is_no_single_result(source: str) -> None:
    """The front door has four blocks and the entity file four sections. A button that
    exported whichever one it picked would misdescribe its own file."""
    assert "$('descarca').hidden = isPan || isDosar;" in source


def test_the_researcher_is_pointed_at_the_files(source: str) -> None:
    """A CSV of the current view is the journalist's path. Somebody who wants the whole
    thing should not be cloning a repository to find it."""
    assert "manifest.json" in source
    assert "Fișierele sunt servite direct" in source
