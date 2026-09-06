"""Tests for indicator metadata and the safety rules around thresholds."""

from __future__ import annotations

from datetime import date

import pytest

from achizitii.indicators import (
    CEILING_MIN_BELOW,
    INDICATORS,
    INDICATORS_BY_ID,
    THRESHOLDS,
    corroborate_ceiling_sql,
    detect_ceiling_sql,
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

    def test_works_ceiling_is_900_400_not_900_000(self) -> None:
        """Sources disagreed; the data settled it.

        H1 2026 works acquisitions: 20 records in (900,000, 900,400] and ZERO in
        (900,400, 910,000]. That excludes 900,000 outright and pins 900,400.
        """
        assert threshold_for(date(2026, 1, 1), "works") == 900_400.0

    def test_unknown_category_falls_back_to_goods_services(self) -> None:
        assert threshold_for(date(2026, 1, 1), "produse") == 270_120.0

    def test_unverified_period_returns_none(self) -> None:
        """A wrong threshold invents findings — refusing to answer is the safe failure."""
        assert threshold_for(date(2018, 1, 1), "goods_services") is None

    def test_all_declared_thresholds_carry_a_verified_flag(self) -> None:
        for row in THRESHOLDS:
            assert "verified" in row
            assert isinstance(row["verified"], bool)
            assert isinstance(row["from"], date)

    def test_verified_thresholds_cite_their_evidence(self) -> None:
        """A threshold marked verified must say what verified it."""
        for row in THRESHOLDS:
            if row["verified"]:
                assert row.get("source", "").strip(), row


class TestCeilingDetection:
    def test_sql_is_parameterised_and_scoped(self) -> None:
        sql = detect_ceiling_sql()
        assert "achizitii_directe" in sql
        # Works has a different ceiling, so mixing categories would blur the cliff.
        assert "furnizare" in sql and "servicii" in sql
        assert str(int(CEILING_MIN_BELOW)) in sql

    def test_window_is_configurable(self) -> None:
        assert "5000" in detect_ceiling_sql(window=5000.0)


class TestCorroboration:
    def test_scopes_by_category(self) -> None:
        assert "'lucrari'" in corroborate_ceiling_sql("works")
        gs = corroborate_ceiling_sql("goods_services")
        assert "'furnizare','servicii'" in gs and "lucrari" not in gs

    def test_uses_declared_threshold_parameter(self) -> None:
        """Corroboration must test a specific declared value, not rediscover a cliff."""
        assert "$prag" in corroborate_ceiling_sql()

    def test_reports_exceedance_share(self) -> None:
        assert "pct_peste" in corroborate_ceiling_sql()


class TestFailureSignalling:
    """A pipeline that produces nothing must not report success.

    Per-year and per-resource errors are swallowed so one bad file cannot abort a
    decade-long backfill. That tolerance turned a total failure into a green CI build
    once already: data.gov.ro refused every connection, the job exited 0, and all five
    indicators silently reported "missing data".
    """

    def test_empty_ingest_with_errors_exits_nonzero(self, monkeypatch) -> None:
        from achizitii import cli

        monkeypatch.setattr(
            "achizitii.govpipeline.ingest_years",
            lambda *a, **k: {
                "ingest_version": 1,
                "total_rows": 0,
                "files": [{"year": 2026, "rows": 0, "error": "discovery: timed out"}],
                "unmapped_columns": {},
            },
        )
        assert cli.main(["gov", "--years", "2026"]) == 1

    def test_successful_ingest_exits_zero(self, monkeypatch) -> None:
        from achizitii import cli

        monkeypatch.setattr(
            "achizitii.govpipeline.ingest_years",
            lambda *a, **k: {
                "ingest_version": 1,
                "total_rows": 1000,
                "files": [{"year": 2026, "rows": 1000}],
                "unmapped_columns": {},
            },
        )
        assert cli.main(["gov", "--years", "2026"]) == 0

    def test_partial_failure_still_succeeds(self, monkeypatch) -> None:
        """One bad quarter among good ones is tolerated — that is the point."""
        from achizitii import cli

        monkeypatch.setattr(
            "achizitii.govpipeline.ingest_years",
            lambda *a, **k: {
                "ingest_version": 1,
                "total_rows": 500,
                "files": [
                    {"year": 2026, "rows": 500},
                    {"year": 2025, "rows": 0, "error": "corrupt file"},
                ],
                "unmapped_columns": {},
            },
        )
        assert cli.main(["gov", "--years", "2025-2026"]) == 0

    def test_all_indicators_skipped_exits_nonzero(self, monkeypatch) -> None:
        from achizitii import cli

        monkeypatch.setattr(
            "achizitii.govpipeline.run_indicators",
            lambda **k: {
                "date": "2026-09-06",
                "detected_ceilings": [],
                "results": [
                    {"indicator": "prag-01", "skipped": "missing data: ['achizitii_directe']"},
                    {"indicator": "divizare-01", "skipped": "missing data: ['achizitii_directe']"},
                ],
            },
        )
        assert cli.main(["indicators"]) == 1

    def test_indicators_with_findings_exit_zero(self, monkeypatch) -> None:
        from achizitii import cli

        monkeypatch.setattr(
            "achizitii.govpipeline.run_indicators",
            lambda **k: {
                "date": "2026-09-06",
                "detected_ceilings": [],
                "results": [
                    {"indicator": "prag-01", "findings": 1241, "legal_basis": "art. 7",
                     "name": "n", "output": "o", "sample": []},
                    {"indicator": "divizare-01", "skipped": "missing data"},
                ],
            },
        )
        assert cli.main(["indicators"]) == 0


class TestDataAvailabilityRequirements:
    """Some columns exist only in part of the archive."""

    def test_offer_count_indicator_declares_its_requirement(self) -> None:
        """`numar_oferte` was dropped from post-2018 exports.

        Expressed as a data requirement rather than a hardcoded year window, so the
        rule resumes automatically if the publisher restores the column — and never
        reports "no findings" when the truth is "no data".
        """
        ind = INDICATORS_BY_ID["ofertant-unic-01"]
        assert ind.requires_columns == ("numar_oferte",)

    def test_estimate_indicator_declares_its_requirement(self) -> None:
        assert INDICATORS_BY_ID["estimare-01"].requires_columns == ("valoare_estimata_ron",)

    def test_indicators_without_requirements_declare_none(self) -> None:
        assert INDICATORS_BY_ID["prag-01"].requires_columns == ()


class TestEstimateIndicatorGuards:
    def test_excludes_framework_agreements(self) -> None:
        """Frameworks accumulate many contracts under one estimate.

        The joined version of this comparison produced ratios of 1,605% for exactly
        that reason.
        """
        assert "acord-cadru" in INDICATORS_BY_ID["estimare-01"].sql

    def test_has_upper_plausibility_bound(self) -> None:
        """Awards many times the estimate are source-data errors, not decisions.

        A sampled case recorded a commune at 1.18 bn RON against a 2.1 M estimate with
        a NULL contract number — a row whose fields shifted left because one was
        missing, which the malformed-row check cannot see.
        """
        ind = INDICATORS_BY_ID["estimare-01"]
        assert "raport_maxim" in ind.params
        assert ind.params["raport_maxim"] == 5.0
        assert "$raport_maxim" in ind.sql


class TestCeilingReportSeparation:
    """Detection and corroboration answer different questions."""

    def test_detection_does_not_reference_a_declared_value(self) -> None:
        """The cliff scan must locate the ceiling without assuming one."""
        sql = detect_ceiling_sql()
        assert "$prag" not in sql
        assert "270120" not in sql.replace(",", "")

    def test_corroboration_requires_a_declared_value(self) -> None:
        """Corroboration tests a specific figure; that is what validates a legal value."""
        assert "$prag" in corroborate_ceiling_sql()

    def test_exceedance_tolerance_is_declared(self) -> None:
        from achizitii.indicators import CORROBORATION_MAX_EXCEEDANCE_PCT

        assert 0 < CORROBORATION_MAX_EXCEEDANCE_PCT < 5
