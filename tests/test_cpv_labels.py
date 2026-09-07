"""A populated column is not necessarily a meaningful one.

The pre-2021 exports wrote the internal CPV_CODE_ID into the CPV *name* column, so
39831240 read "15113" rather than "Produse de curatenie". Null-rate checks are blind to
this by construction — the column is full — and it reached the published site, where a
quarter of the CPV table showed a number where the product name belongs. Anyone
searching "detergent" for 2016-2018 found nothing.

The narrow regex is the trap worth pinning: `^[0-9]+$` matches 7.4M of the 14.1M
affected rows and misses every value written "11728.0", which is exactly half the
problem and reads as a fix that worked.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from achizitii.govpipeline import KNOWN_LABEL_DEFECTS, LABEL_COLUMNS, NUMERIC_LABEL_RE
from achizitii.publish import NUMERIC_LABEL

NUMERIC = [
    "15113",
    "11728",
    "11728.0",  # the half the narrow regex misses
    "11728,0",  # Romanian decimal comma
    "0",
]
LABELS = [
    "Produse de curatenie (Rev.2)",
    "Cartuse de toner (Rev.2)",
    "Hartie A4",  # contains digits but is a name
    "30125100 Cartuse de toner",  # starts with digits, still a name
    "A4",
    "Servicii IT 24/7",
]


@pytest.mark.parametrize("value", NUMERIC)
def test_numeric_ids_are_detected(value: str) -> None:
    assert re.match(NUMERIC_LABEL, value), f"{value!r} should be seen as an id, not a label"
    assert re.match(NUMERIC_LABEL_RE, value)


@pytest.mark.parametrize("value", LABELS)
def test_real_labels_are_not_flagged(value: str) -> None:
    """Discarding a genuine name would be worse than the defect it fixes."""
    assert not re.match(NUMERIC_LABEL, value), f"{value!r} is a real label"
    assert not re.match(NUMERIC_LABEL_RE, value)


def test_the_two_patterns_agree() -> None:
    """publish.py and govpipeline.py must not disagree about what counts as junk."""
    assert NUMERIC_LABEL == NUMERIC_LABEL_RE


def test_decimal_ids_are_covered() -> None:
    """Pin the specific near-miss: a narrower pattern would silently halve the fix."""
    narrow = r"^[0-9]+$"
    assert not re.match(narrow, "11728.0")
    assert re.match(NUMERIC_LABEL, "11728.0")


def test_known_defect_years_are_declared_for_a_checked_column() -> None:
    for (table, column), years in KNOWN_LABEL_DEFECTS.items():
        assert column in LABEL_COLUMNS.get(table, ()), (
            f"{table}.{column} is declared defective but is not among the checked "
            "label columns, so nothing would ever compare against it"
        )
        assert years, f"{table}.{column} declares no years"


@pytest.mark.skipif(
    not Path("site/data/cpv_an.parquet").is_file(), reason="bundle not built"
)
class TestPublishedLabels:
    def test_no_numeric_labels_are_published(self) -> None:
        con = duckdb.connect()
        rows = con.execute(
            f"""SELECT an, cpv, cpv_denumire FROM 'site/data/cpv_an.parquet'
                WHERE cpv_denumire IS NOT NULL
                  AND regexp_matches(cpv_denumire, '{NUMERIC_LABEL}') LIMIT 5"""
        ).fetchall()
        con.close()
        assert not rows, f"numeric labels reached the published table: {rows}"

    def test_early_years_recovered_a_real_label(self) -> None:
        """2016-2020 have no usable label of their own; they borrow from 2021+."""
        con = duckdb.connect()
        labelled, total = con.execute(
            """SELECT count(cpv_denumire), count(*) FROM 'site/data/cpv_an.parquet'
               WHERE an <= 2020"""
        ).fetchone()
        con.close()
        assert labelled / total > 0.95, (
            f"only {labelled}/{total} pre-2021 CPV rows carry a label; the repair "
            "should recover almost all of them"
        )
