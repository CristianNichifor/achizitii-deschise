"""The gap registry must not silently lose an entry.

`KNOWN_COLUMN_GAPS` is a dict literal keyed by (table, column). Writing the same key
twice is legal Python: the later entry wins and the earlier one vanishes without a
warning. That happened once here — two windows were added for columns that already had
an entry further down the file, the additions were discarded, and `validate` went on
reporting the columns as problems while the source read as though they were documented.

Runtime inspection cannot catch this, because by the time the dict exists the duplicate
is already gone. So the source is parsed instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

from achizitii import govdata
from achizitii.govdata import KNOWN_COLUMN_GAPS, TABLES_BY_KEY


def _gap_keys_in_source() -> list[tuple[str, str]]:
    tree = ast.parse(Path(govdata.__file__).read_text())
    for node in ast.walk(tree):
        # The declaration is annotated (`NAME: dict[...] = {...}`), which parses as
        # AnnAssign rather than Assign. Both are accepted so the test survives the
        # annotation being added or dropped.
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if (
            any(isinstance(t, ast.Name) and t.id == "KNOWN_COLUMN_GAPS" for t in targets)
            and isinstance(node.value, ast.Dict)
        ):
            return [
                tuple(ast.literal_eval(k) for k in key.elts)  # type: ignore[misc]
                for key in node.value.keys
                if isinstance(key, ast.Tuple)
            ]
    raise AssertionError("KNOWN_COLUMN_GAPS literal not found")


def test_no_duplicate_gap_keys() -> None:
    keys = _gap_keys_in_source()
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, (
        f"duplicate keys in KNOWN_COLUMN_GAPS: {sorted(duplicates)}. "
        "The later entry silently replaces the earlier one — merge the windows into a "
        "single key, which accepts several window dicts."
    )
    assert len(keys) == len(KNOWN_COLUMN_GAPS)


def test_gaps_reference_real_columns() -> None:
    """A gap for a column that no longer exists documents nothing."""
    for table, column in KNOWN_COLUMN_GAPS:
        assert table in TABLES_BY_KEY, f"unknown table {table!r} in gap registry"
        assert column in TABLES_BY_KEY[table].columns, (
            f"gap declared for {table}.{column}, which is not a column of that table"
        )


def test_every_gap_states_a_reason() -> None:
    """A year list without a reason is an assertion nobody can check."""
    for (table, column), windows in KNOWN_COLUMN_GAPS.items():
        assert windows, f"{table}.{column} has no windows"
        for window in windows:
            assert window.get("years"), f"{table}.{column} has a window with no years"
            reason = window.get("reason", "")
            assert len(reason) > 40, (
                f"{table}.{column} has a window whose reason is too thin to verify: "
                f"{reason!r}"
            )
