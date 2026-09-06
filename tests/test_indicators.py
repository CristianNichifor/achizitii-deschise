"""Tests for indicator metadata and the safety rules around thresholds."""

from __future__ import annotations

from datetime import date

import pytest

from achizitii.indicators import (
    INDICATORS,
    INDICATORS_BY_ID,
    THRESHOLDS,
    threshold_for,
)


class TestMetadata:
    """Every indicator must be publishable: a finding without a legal basis is not."""

    @pytest.mark.parametrize("ind", INDICATORS, ids=lambda i: i.identifier)
    def test_declares_legal_basis(self, ind) -> None:
        assert ind.legal_basis.strip()
        assert "98/2016" in ind.legal_basis

    @pytest.mark.parametrize("ind", INDICATORS, ids=lambda i: i.identifier)
    def test_declares_scope_and_rationale(self, ind) -> None:
        assert ind.applies_to, "an indicator must declare the tables it reads"
        assert ind.rationale.strip()
        assert ind.description_ro.strip()

    @pytest.mark.parametrize("ind", INDICATORS, ids=lambda i: i.identifier)
    def test_has_sql(self, ind) -> None:
        assert "SELECT" in ind.sql.upper()

    def test_identifiers_unique(self) -> None:
        ids = [i.identifier for i in INDICATORS]
        assert len(ids) == len(set(ids))
        assert set(INDICATORS_BY_ID) == set(ids)

    @pytest.mark.parametrize("ind", INDICATORS, ids=lambda i: i.identifier)
    def test_output_language_is_neutral(self, ind) -> None:
        """Wording must describe a pattern, not allege wrongdoing."""
        text = f"{ind.name_ro} {ind.description_ro}".lower()
        for accusation in ("fraud", "frauda", "coruptie", "corupție", "ilegal", "infractiune"):
            assert accusation not in text, f"{ind.identifier} uses accusatory wording"


class TestValidityWindow:
    def test_inactive_before_valid_from(self) -> None:
        ind = INDICATORS_BY_ID["prag-01"]
        assert not ind.active_on(date(2018, 6, 1))
        assert ind.active_on(date(2026, 1, 1))

    def test_active_on_boundary(self) -> None:
        ind = INDICATORS_BY_ID["prag-01"]
        assert ind.active_on(ind.valid_from)


class TestThresholds:
    def test_verified_threshold_returned(self) -> None:
        assert threshold_for(date(2026, 1, 1), "goods_services") == 270_120.0
        assert threshold_for(date(2026, 1, 1), "works") == 900_000.0

    def test_unverified_period_returns_none(self) -> None:
        """A wrong threshold invents findings — refusing to answer is the safe failure."""
        assert threshold_for(date(2018, 1, 1), "goods_services") is None

    def test_all_declared_thresholds_carry_a_verified_flag(self) -> None:
        for row in THRESHOLDS:
            assert "verified" in row
            assert isinstance(row["verified"], bool)
            assert isinstance(row["from"], date)
