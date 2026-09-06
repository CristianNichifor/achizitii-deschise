"""Tests for SEAP -> OCDS mapping."""

from __future__ import annotations

import pytest

from achizitii.ocds import direct_acquisition_to_release, parse_cpv, split_org


class TestSplitOrg:
    @pytest.mark.parametrize(
        ("raw", "cui", "name"),
        [
            ("9626572 - FUNDATIA DE SPRIJIN COMUNITAR", "9626572", "FUNDATIA DE SPRIJIN COMUNITAR"),
            ("33203265 Expert Business Center SRL", "33203265", "Expert Business Center SRL"),
            # VAT-registered suppliers carry an "RO" prefix. Regression: this used to
            # drop the CUI entirely for ~80% of suppliers, destroying the join key.
            ("RO 15437993 ROMSYSTEMS", "15437993", "ROMSYSTEMS"),
            ("RO15169122 MEDISERV", "15169122", "MEDISERV"),
            ("RO 6779113 AS COMPUTER", "6779113", "AS COMPUTER"),
        ],
    )
    def test_cui_and_name(self, raw: str, cui: str, name: str) -> None:
        assert split_org(raw) == (cui, name)

    def test_no_cui(self) -> None:
        assert split_org("Primaria Fara CUI") == (None, "Primaria Fara CUI")

    def test_empty(self) -> None:
        assert split_org(None) == (None, None)


class TestParseCpv:
    def test_from_header_string(self) -> None:
        code, label = parse_cpv("79311100-8 - Servicii de elaborare de studii (Rev.2)")
        assert code == "79311100"
        assert "Servicii de elaborare" in (label or "")

    def test_from_item_object(self) -> None:
        code, label = parse_cpv(
            {"id": 18751, "text": "Servicii de elaborare de studii", "localeKey": "79311100-8"}
        )
        assert code == "79311100"
        assert label == "Servicii de elaborare de studii"

    def test_none(self) -> None:
        assert parse_cpv(None) == (None, None)


DETAIL = {
    "directAcquisitionID": 122898410,
    "directAcquisitionName": "Achizitie laptopuri",
    "contractingAuthority": "4426212 JUDETUL TIMIS",
    "supplier": "RO 6779113 AS COMPUTER",
    "cpvCode": "30213100-6 - Computere portabile (Rev.2)",
    "publicationDate": "2026-09-01T00:03:04+03:00",
    "finalizationDate": "2026-09-01T00:06:29+03:00",
    "estimatedValue": 54555.0,
    "closingValue": 54555.0,
    "sysAcquisitionContractType": {"id": 1, "text": "Furnizare"},
    "sysDirectAcquisitionState": {"id": 7, "text": "Oferta acceptata"},
    "directAcquisitionItems": [
        {
            "directAcquisitionItemID": 133491484,
            "catalogItemName": "Laptop Business Lenovo V15 G4 AMN",
            "itemQuantity": 15.0,
            "itemMeasureUnit": "bucata",
            "itemClosingPrice": 3637.0,
            "cpvCode": {"id": 1, "text": "Computere portabile", "localeKey": "30213100-6"},
        }
    ],
}


class TestRelease:
    def test_structure(self) -> None:
        r = direct_acquisition_to_release(DETAIL)
        assert r["ocid"].endswith("-da-122898410")
        assert r["tag"] == ["tender", "award"]
        assert r["language"] == "ro"
        assert r["tender"]["procurementMethod"] == "direct"
        assert r["tender"]["mainProcurementCategory"] == "goods"

    def test_parties_have_identifiers(self) -> None:
        r = direct_acquisition_to_release(DETAIL)
        roles = {p["roles"][0]: p for p in r["parties"]}
        assert roles["buyer"]["identifier"]["id"] == "4426212"
        assert roles["supplier"]["identifier"]["id"] == "6779113"

    def test_item_unit_value_is_the_unit_price(self) -> None:
        """OCDS `unit.value` means price per single unit — matching SEAP semantics."""
        r = direct_acquisition_to_release(DETAIL)
        item = r["tender"]["items"][0]
        assert item["quantity"] == 15.0
        assert item["unit"]["value"]["amount"] == 3637.0
        assert item["unit"]["value"]["currency"] == "RON"
        assert item["classification"]["id"] == "30213100"

    def test_award_present_and_active(self) -> None:
        r = direct_acquisition_to_release(DETAIL)
        award = r["awards"][0]
        assert award["status"] == "active"
        assert award["value"]["amount"] == 54555.0
        assert award["suppliers"][0]["name"] == "AS COMPUTER"

    def test_header_total_equals_sum_of_line_totals(self) -> None:
        """The invariant that proved itemClosingPrice is a unit price."""
        r = direct_acquisition_to_release(DETAIL)
        items = r["tender"]["items"]
        computed = sum(i["quantity"] * i["unit"]["value"]["amount"] for i in items)
        assert computed == pytest.approx(DETAIL["closingValue"])
