"""Tests for product grouping and the art. 156 brand check."""

from __future__ import annotations

import pytest

from achizitii.produs import (
    brand_without_equivalent,
    find_brands,
    mentions_equivalent,
    product_key,
)


class TestBrandDetection:
    def test_finds_brands(self) -> None:
        assert "dell" in find_brands("Laptop Dell Inspiron 3567")
        assert "konica minolta" in find_brands("Multifunctionala Konica Minolta Bizhub")

    def test_standards_are_not_brands(self) -> None:
        """Flagging ISO or EN as a brand manufactures art. 156 findings against
        entirely lawful specifications."""
        assert find_brands("Cartus compatibil ISO 9001, certificat EN 14001, SR 13") == ()

    def test_word_boundaries(self) -> None:
        """'hp' must not fire inside another word."""
        assert "hp" not in find_brands("Servicii de shpe si transport")
        assert "hp" in find_brands("Imprimanta HP LaserJet")

    def test_ordered_by_position_not_length(self) -> None:
        """Regression: the gazetteer was scanned longest-first, so "Laptop Dell
        Inspiron ... cu procesor Intel Core i5" reported Intel — the component
        vendor — as the product brand instead of Dell."""
        brands = find_brands("Laptop Dell Inspiron 3567 cu procesor Intel Core i5")
        assert brands[0] == "dell"
        assert "intel" in brands

    def test_longer_brand_wins_over_substring(self) -> None:
        assert find_brands("Seringi B Braun Omnifix") == ("b braun",)


class TestArticle156:
    def test_equivalent_phrase_detected(self) -> None:
        assert mentions_equivalent("Imprimanta HP sau echivalent")
        assert not mentions_equivalent("Imprimanta HP")

    def test_brand_without_equivalent_is_flagged(self) -> None:
        assert brand_without_equivalent("Multifunctionala Konica Minolta 5021i")

    def test_brand_with_equivalent_is_not_flagged(self) -> None:
        assert brand_without_equivalent("Multifunctionala Konica Minolta sau echivalent") == ()

    def test_no_brand_is_not_flagged(self) -> None:
        assert brand_without_equivalent("Hartie copiator A4 80g") == ()


class TestProductKey:
    @pytest.mark.parametrize("text", ["Laptop", "laptop", "LAPTOP",
                                      "Achizitie laptop conform caiet de sarcini"])
    def test_casing_and_boilerplate_collapse(self, text: str) -> None:
        """A CPV alone cannot group these; without normalisation each is its own group."""
        assert product_key(text, "30213100-6").key == "30213100|laptop"

    def test_brand_splits_a_cpv_into_comparable_groups(self) -> None:
        """30213100 covers a budget netbook and a mobile workstation alike."""
        dell = product_key("Laptop Dell Inspiron 3567 cu procesor Intel", "30213100-6")
        hp = product_key("Laptop HP 250 G6, 15.6 inch LED", "30213100-6")
        lenovo = product_key("Laptop Lenovo IdeaPad 100-15IBD", "30213100-6")
        assert dell.key == "30213100|laptop|dell"
        assert hp.key == "30213100|laptop|hp"
        assert len({dell.key, hp.key, lenovo.key}) == 3

    def test_model_noise_does_not_fragment_groups(self) -> None:
        """Two Dell laptops of different models must share a key."""
        a = product_key("Laptop Dell Inspiron 3567 cu procesor i5", "30213100-6")
        b = product_key("Laptop Dell Latitude 5490, 8GB RAM", "30213100-6")
        assert a.key == b.key

    def test_cpv_separates_identical_wording(self) -> None:
        assert product_key("Set", "30213100-6").key != product_key("Set", "45310000-3").key

    def test_empty_description(self) -> None:
        assert product_key(None).key == ""
        assert product_key("", "30213100-6").key == "30213100"
