"""Tests for the rules that decide whether a price is comparable."""

from __future__ import annotations

import pytest

from achizitii.normalize import (
    extract_pack_size,
    fold,
    normalize_item,
    normalize_unit,
    parse_ro_number,
)


class TestUnitNormalisation:
    @pytest.mark.parametrize(
        "raw", ["buc", "Buc", "BUC", "bucata", "bucată", "bucati", "bucăți", " buc. "]
    )
    def test_count_variants_collapse(self, raw: str) -> None:
        """The single most common failure: 'bucata' and 'bucată' counted separately."""
        assert normalize_unit(raw).canonical == "buc"
        assert normalize_unit(raw).comparable is True

    @pytest.mark.parametrize("raw", ["set", "pachet", "PACHET", "cutie", "lot", "kit"])
    def test_bundle_units_are_not_comparable(self, raw: str) -> None:
        unit = normalize_unit(raw)
        assert unit.dimension == "bundle"
        assert unit.comparable is False

    def test_unknown_units_are_not_guessed(self) -> None:
        unit = normalize_unit("Role")
        assert unit.canonical == "necunoscut"
        assert unit.comparable is False

    def test_empty_unit(self) -> None:
        assert normalize_unit(None).comparable is False
        assert normalize_unit("").comparable is False


class TestFold:
    def test_diacritics_and_cedilla_fold_together(self) -> None:
        # ş (cedilla, legacy Windows) and ș (comma-below, correct) must match.
        assert fold("Bănci parc") == fold("banci parc")
        assert fold("bucăţi") == fold("bucati")


class TestPackSize:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Almacor 10 mg x 30 cpr", 30),
            ("Hartie copiator A4 500 coli", 500),
            ("set 12 buc", 12),
            ("Laptop Lenovo V15", None),
            ("", None),
        ],
    )
    def test_extraction(self, text: str, expected: int | None) -> None:
        assert extract_pack_size(text) == expected


class TestRomanianNumbers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.234,56", 1234.56),
            ("1234,56", 1234.56),
            ("1,234.56", 1234.56),
            ("3853.72", 3853.72),
            (3637.0, 3637.0),
            ("", None),
            (None, None),
        ],
    )
    def test_parse(self, raw: object, expected: float | None) -> None:
        assert parse_ro_number(raw) == expected  # type: ignore[arg-type]


class TestNormalizeItem:
    def test_unit_price_is_taken_as_given_not_divided(self) -> None:
        """Regression guard for the project's core empirical finding.

        `itemClosingPrice` is the price of ONE unit. A laptop at quantity 15 costs
        3637 RON each and 54555 RON in total — never 242 RON each.
        """
        item = normalize_item(
            description="Laptop Business Lenovo V15 G4 AMN",
            long_description=None,
            cpv="30213100",
            quantity=15.0,
            unit_raw="bucata",
            unit_price_ron=3637.0,
        )
        assert item.unit_price_ron == 3637.0
        assert item.line_total_ron == pytest.approx(54555.0)
        assert item.comparable is True
        assert item.unit == "buc"

    def test_bundle_without_pack_size_is_excluded(self) -> None:
        item = normalize_item(
            description="PACHET CARTUSE DE TONER",
            long_description=None,
            cpv="30125100",
            quantity=1.0,
            unit_raw="PACHET",
            unit_price_ron=3853.72,
        )
        assert item.comparable is False
        assert item.incomparable_reason == "unitate_de_tip_pachet_fara_marime_cunoscuta"

    def test_bundle_with_recoverable_pack_size_is_kept(self) -> None:
        item = normalize_item(
            description="Almacor 10 mg x 30 cpr",
            long_description=None,
            cpv="33600000",
            quantity=7.0,
            unit_raw="Cutie",
            unit_price_ron=6.0,
        )
        assert item.pack_size == 30
        assert item.comparable is True

    def test_unknown_unit_is_excluded(self) -> None:
        item = normalize_item(
            description="Cearceaf examinare rola hartie",
            long_description=None,
            cpv=None,
            quantity=2.0,
            unit_raw="Role",
            unit_price_ron=25.0,
        )
        assert item.comparable is False
        assert item.incomparable_reason == "unitate_nerecunoscuta"

    @pytest.mark.parametrize("price", [None, 0.0, -5.0])
    def test_missing_or_nonpositive_price_excluded(self, price: float | None) -> None:
        item = normalize_item(
            description="Ceva",
            long_description=None,
            cpv=None,
            quantity=1.0,
            unit_raw="buc",
            unit_price_ron=price,
        )
        assert item.comparable is False
        assert item.incomparable_reason == "pret_unitar_lipsa_sau_nepozitiv"
