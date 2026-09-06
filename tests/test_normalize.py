"""Tests for the rules that decide whether a price is comparable."""

from __future__ import annotations

import pytest

from achizitii.normalize import (
    contract_category,
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


class TestUncefactCodes:
    """Units carry UN/CEFACT Rec 20 codes so OCDS `unit.id` is standards-based."""

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("bucata", "H87"),   # piece
            ("bucăți", "H87"),
            ("kg", "KGM"),
            ("ora", "HUR"),
            ("mp", "MTK"),       # square metre
            ("mc", "MTQ"),       # cubic metre
            ("set", "SET"),
            ("cutie", "BX"),
        ],
    )
    def test_code_assigned(self, raw: str, code: str) -> None:
        assert normalize_unit(raw).uncefact == code

    def test_unknown_unit_has_no_code(self) -> None:
        """Never emit a guessed code — an absent unit.id is honest, a wrong one is not."""
        assert normalize_unit("Role").uncefact is None

    def test_every_declared_code_is_a_real_uncefact_code(self) -> None:
        """Guard against typos in um_map.yml.

        Codes are validated against the UN/CEFACT Recommendation 20 common codes. The
        subset asserted here is the full set the map currently uses; adding a unit means
        adding its code here too.
        """
        from achizitii.normalize import _unit_index

        known = {
            "H87", "KGM", "GRM", "TNE", "LTR", "MLT", "MTQ", "MTR", "KMT",
            "MTK", "HUR", "DAY", "MON", "ANN", "KWH", "SET", "PK", "BX", "LS", "E48",
        }
        used = {u.uncefact for u in _unit_index().values() if u.uncefact}
        assert used <= known, f"unknown UN/CEFACT codes in um_map.yml: {used - known}"


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

    def test_bundle_named_in_description_despite_count_unit(self) -> None:
        """Real record: a food package booked as qty 1 'bucata'.

        The declared unit says piece; the item is a package of unknown contents. Its
        102.96 RON is not comparable with another buyer's differently-filled package.
        """
        item = normalize_item(
            description="PACHET ALIMENTAR",
            long_description="CONTINE DIFERITE PRODUSE ALIMENTARE, MEZELURI, LACTATE",
            cpv="15897300",
            quantity=1.0,
            unit_raw="bucata",
            unit_price_ron=102.96,
        )
        assert item.comparable is False
        assert item.incomparable_reason == "descriere_de_tip_pachet_fara_marime_cunoscuta"

    def test_ordinary_goods_not_misflagged_as_bundles(self) -> None:
        """The bundle heuristic must not swallow normal items."""
        for desc in ["Laptop Business Lenovo V15", "Cutie de viteze", "Monitor Dell 24"]:
            item = normalize_item(
                description=desc,
                long_description=None,
                cpv=None,
                quantity=2.0,
                unit_raw="bucata",
                unit_price_ron=100.0,
            )
            assert item.comparable is True, f"{desc!r} wrongly flagged as a bundle"

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


class TestContractCategory:
    """Category must work across eras, not just 2022+.

    2016-2018 record "Cumparare directa" (the procedure) in the contract-type column,
    so any indicator filtering on Furnizare/Servicii silently returned nothing for
    those years. Falling back to the CPV division makes the archive usable.
    """

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [("Furnizare", "furnizare"), ("Servicii", "servicii"),
         ("Lucrari", "lucrari"), ("lucrări", "lucrari"), ("PRODUSE", "furnizare")],
    )
    def test_declared_type_wins(self, declared: str, expected: str) -> None:
        assert contract_category(declared, "30213100-6") == expected

    @pytest.mark.parametrize(
        ("cpv", "expected"),
        [
            ("45310000-3", "lucrari"),    # construction work
            ("30213100-6", "furnizare"),  # portable computers
            ("15897300-5", "furnizare"),  # food packages
            ("79311100-8", "servicii"),   # survey services
            ("50610000-4", "servicii"),   # repair services
            ("48000000-8", "furnizare"),  # software packages are supplies
        ],
    )
    def test_falls_back_to_cpv_division(self, cpv: str, expected: str) -> None:
        assert contract_category("Cumparare directa", cpv) == expected

    def test_unusable_declared_type_does_not_block_cpv(self) -> None:
        """2016-2018 put the procedure here; it must not be mistaken for a category."""
        assert contract_category("Cumparare directa", "45000000-7") == "lucrari"

    def test_none_when_nothing_usable(self) -> None:
        assert contract_category(None, None) is None
        assert contract_category("Cumparare directa", None) is None

    def test_malformed_cpv_is_not_guessed(self) -> None:
        assert contract_category(None, "abc") is None
