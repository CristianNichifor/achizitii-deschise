"""Tests for publication-date normalisation.

The format changes almost every year, and a plain cast silently yields NULL for the
ones it cannot read. That is how divizare-01 came to compute date_diff over four
usable years out of eleven without anything failing.
"""

from __future__ import annotations

import duckdb
import pytest

from achizitii.govpipeline import _date_expr


def parse(value: str | None) -> object:
    con = duckdb.connect()
    expr = _date_expr("x", "ts")
    return con.execute(
        f"SELECT {expr} FROM (SELECT ? AS x)", [value]
    ).fetchone()[0]


class TestFormatsSeenInTheArchive:
    @pytest.mark.parametrize(
        ("raw", "year", "month", "day"),
        [
            ("2016-07-01 04:43:37.333000000", 2016, 7, 1),   # 2016 ISO, 9-digit frac
            ("2017-04-12 17:25:02.763", 2017, 4, 12),        # 2017 ISO
            ("01-10-2018 09:12:24", 2018, 10, 1),            # 2018 DD-MM-YYYY
            ("12.12.2019 12:31:42", 2019, 12, 12),           # 2019 DD.MM.YYYY
            ("13.07.2020 11:34:52", 2020, 7, 13),            # 2020 DD.MM.YYYY
            ("30/07/2021", 2021, 7, 30),                     # 2021 DD/MM/YYYY
            ("03/29/2022", 2022, 3, 29),                     # 2022 MM/DD/YYYY (US)
            ("7/1/2023 6:41:01 AM", 2023, 7, 1),             # 2023 Q3 US with AM/PM
            ("2025-04-01 01:04:55", 2025, 4, 1),             # 2025 ISO
        ],
    )
    def test_parses(self, raw: str, year: int, month: int, day: int) -> None:
        ts = parse(raw)
        assert ts is not None, raw
        assert (ts.year, ts.month, ts.day) == (year, month, day)

    def test_excel_serial(self) -> None:
        """2023 Q1 stores dates as days since the 1900 epoch."""
        ts = parse("44927.810960648145")
        assert ts is not None
        assert (ts.year, ts.month, ts.day) == (2023, 1, 1)


class TestAmbiguityRule:
    def test_day_first_preferred(self) -> None:
        """Romanian order wins when both readings are possible."""
        ts = parse("03/04/2022")
        assert (ts.month, ts.day) == (4, 3)

    def test_us_order_used_only_when_day_first_impossible(self) -> None:
        ts = parse("03/29/2022")
        assert (ts.month, ts.day) == (3, 29)


class TestNonDates:
    @pytest.mark.parametrize("raw", [None, "", "nu se aplica", "-", "abc"])
    def test_unparseable_yields_null_not_a_wrong_date(self, raw: str | None) -> None:
        assert parse(raw) is None

    def test_short_number_is_not_read_as_a_serial(self) -> None:
        """A bare '42' is far more likely a stray value than 1900-02-10."""
        assert parse("42") is None
