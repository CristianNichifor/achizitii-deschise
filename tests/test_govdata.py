"""Tests for bulk-export format and schema drift handling."""

from __future__ import annotations

import io
import zipfile
from typing import ClassVar

import pytest

from achizitii.govdata import (
    ACHIZITII_DIRECTE,
    CONTRACTE,
    INITIERE,
    MODIFICARI,
    TABLES,
    _header_key,
    _sniff_delimiter,
    map_columns,
    missing_columns,
    read_table,
    sniff,
    to_records,
)


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


class TestSniff:
    def test_xlsx(self) -> None:
        assert sniff(_zip(["[Content_Types].xml", "xl/workbook.xml"])) == "xlsx"

    def test_ods_not_mistaken_for_xlsx(self) -> None:
        """2026 Q2 serves OpenDocument files under .xlsx names — content must win."""
        assert sniff(_zip(["mimetype", "content.xml", "META-INF/manifest.xml"])) == "ods"

    def test_xls_ole2(self) -> None:
        assert sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32) == "xls"

    def test_csv(self) -> None:
        assert sniff(b"a,b,c\n1,2,3\n") == "csv"

    def test_csv_with_bom(self) -> None:
        assert sniff(b"\xef\xbb\xbfa,b\n1,2\n") == "csv"


class TestDelimiter:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Castigator^CastigatorCUI^Valoare", "^"),   # the 2017 exports
            ("a,b,c", ","),
            ("a;b;c", ";"),
        ],
    )
    def test_detection(self, line: str, expected: str) -> None:
        assert _sniff_delimiter(line + "\n1\n") == expected


class TestColumnMapping:
    def test_2026_headers(self) -> None:
        header = [
            "Autoritate contractanta", "CUI autoritate contractanta",
            "Numar achizitie directa", "Data publicare", "Denumire achizitie",
            "Cod CPV", "Denumire CPV", "Tip contract",
            "Finantare prin fonduri comunitare?", "Denumire program",
            "Data finalizare", "Valoare achizitie (RON)",
            "Ofertant castigator", "CUI ofertant castigator",
        ]
        m = map_columns(header, ACHIZITII_DIRECTE)
        assert m["autoritate"] == 0
        assert m["autoritate_cui"] == 1
        assert m["valoare_ron"] == 11
        assert m["furnizor"] == 12
        assert m["furnizor_cui"] == 13

    def test_2017_headers_completely_different(self) -> None:
        """The 2017 export shares no column name with 2026, and is in places richer."""
        header = [
            "Castigator", "CastigatorCUI", "CastigatorTara", "CastigatorLocalitate",
            "CastigatorAdresa", "TipProcedura", "AutoritateContractanta",
            "AutoritateContractantaCUI", "NumarAnunt", "DataAnunt", "Descriere",
            "TipIncheiereContract", "NumarContract", "DataContract", "TitluContract",
            "Valoare", "Moneda", "ValoareRON", "ValoareEUR", "CPVCodeID", "CPVCode",
        ]
        m = map_columns(header, ACHIZITII_DIRECTE)
        assert m["furnizor"] == 0
        assert m["furnizor_cui"] == 1
        assert m["autoritate"] == 6
        assert m["autoritate_cui"] == 7
        assert m["valoare_ron"] == 17          # ValoareRON, not the raw Valoare
        assert m["furnizor_localitate"] == 3   # dropped by later exports

    def test_diacritics_and_punctuation_ignored(self) -> None:
        m = map_columns(["Autoritate contractantă", "Valoare achiziţie (RON)"],
                        ACHIZITII_DIRECTE)
        assert m["autoritate"] == 0
        assert m["valoare_ron"] == 1


class TestToRecords:
    def test_maps_and_reports_unmapped(self) -> None:
        header = ["Autoritate contractanta", "Valoare achizitie (RON)", "Coloana Noua"]
        rows = [["PRIMARIA X", "1234.5", "ceva"]]
        recs, unmapped, malformed = to_records(header, rows, ACHIZITII_DIRECTE, "test.xlsx")
        assert malformed == 0
        assert recs[0]["autoritate"] == "PRIMARIA X"
        assert recs[0]["valoare_ron"] == "1234.5"
        assert recs[0]["sursa"] == "test.xlsx"
        assert recs[0]["furnizor"] is None          # absent column -> None, not missing
        assert unmapped == ["Coloana Noua"]         # surfaced, never silently dropped

    def test_blank_rows_skipped(self) -> None:
        recs, _, _ = to_records(
            ["Autoritate contractanta"], [[""], ["PRIMARIA Y"]], ACHIZITII_DIRECTE, "s"
        )
        assert len(recs) == 1

    def test_short_rows_do_not_crash(self) -> None:
        header = ["Autoritate contractanta", "Valoare achizitie (RON)"]
        recs, _, _ = to_records(header, [["doar-un-camp"]], ACHIZITII_DIRECTE, "s")
        assert recs[0]["valoare_ron"] is None


class TestResourceMatching:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Achiziții directe T I 2024", "achizitii_directe"),
            ("Cumparari directe 2017 - T1", "achizitii_directe"),
            ("Achizitii Directe TII 2026", "achizitii_directe"),
            ("Contracte T1 2025", "contracte"),
            ("Date din modificare contract T1 2025", "modificari"),
            ("Anunturi de atribuire la proceduri fara anunt de initiere TI 2026", "fara_anunt"),
            ("Anunțuri de inițiere publicate T1 2025", "initiere"),
            # Regression: the initiation notice changed name repeatedly. Requiring
            # "anunturi de initiere" silently lost 2017, 2018, 2020 and 2021.
            ("Anunturi participare 2017 - T1", "initiere"),
            ("Anunțuri inițiere 2020 - T3", "initiere"),
            ("Anunturi initiere 2021 - T2", "initiere"),
            ("Anunturi de initiere publicate in SEAP TII 2023", "initiere"),
        ],
    )
    def test_classification(self, name: str, expected: str) -> None:
        matched = [t.key for t in TABLES if t.match.search(name)]
        assert matched and matched[0] == expected, f"{name!r} -> {matched}"

    @pytest.mark.parametrize(
        "name",
        [
            # Invitations to an existing dynamic purchasing system are a different
            # record; "participare" alone must not pull them into `initiere`.
            "Invitatii participare 2017 - T1",
            "Invitatii de depunere oferta la sistemul de achizitii dinamic T1 2024",
            "Invitații de depunere SAD cu anunț aferent T1 2025",
            # Framework call-offs, deliberately excluded from `contracte`.
            "Contracte subsecvente 2017 - T1",
            # The award side of direct purchases — not yet modelled as a table.
            "Notificari de atribuire la cumpararea directa T I 2024",
        ],
    )
    def test_unrelated_resources_stay_unclassified(self, name: str) -> None:
        assert [t.key for t in TABLES if t.match.search(name)] == []

    def test_modificari_not_classified_as_contracte(self) -> None:
        """'Date din modificare contract' contains 'contract' — order must not matter."""
        assert not CONTRACTE.match.search("Date din modificare contract T1 2025")
        assert MODIFICARI.match.search("Date din modificare contract T1 2025")


class TestReadTable:
    def test_caret_csv_roundtrip(self) -> None:
        blob = b"A^B\n1^2\n"
        header, rows = read_table(blob)
        assert header == ["A", "B"]
        assert rows == [["1", "2"]]


class TestHeaderNormalisation:
    """The same column is spelled three ways across the exports."""

    def test_separators_collapse_but_word_order_does_not(self) -> None:
        """Normalisation removes separators; it cannot reorder words.

        "CUI autoritate contractanta" and "AutoritateContractantaCUI" place CUI at
        opposite ends, so they are genuinely different keys. That is why each canonical
        field lists several alias spellings rather than relying on normalisation alone.
        """
        camel = _header_key("AutoritateContractantaCUI")
        snake = _header_key("AUTORITATE_CONTRACTANTA_CUI")
        spaced = _header_key("CUI autoritate contractanta")
        assert camel == snake == "autoritatecontractantacui"
        assert spaced == "cuiautoritatecontractanta"
        assert spaced != camel

    def test_both_orderings_are_declared_as_aliases(self) -> None:
        """Both must therefore resolve — which is what the alias tuples guarantee."""
        for header in ("CUI autoritate contractanta", "AUTORITATE_CONTRACTANTA_CUI"):
            assert "autoritate_cui" in map_columns([header], ACHIZITII_DIRECTE), header

    def test_snake_case_matches_camel_alias(self) -> None:
        """Regression: underscores are word characters.

        A fold that only stripped punctuation left `castigator_cui` unmatched against
        the alias `castigatorcui`, so the 2021 export silently produced four columns of
        NULLs across 1,043,345 rows.
        """
        assert _header_key("CASTIGATOR_CUI") == _header_key("CastigatorCUI")
        assert _header_key("VALOARE_RON") == _header_key("ValoareRON")


class TestSnakeCaseExport:
    """The 2019-2021 exports use SNAKE_CASE headers."""

    HEADER: ClassVar[list[str]] = [
        "CASTIGATOR", "CASTIGATOR_CUI", "CASTIGATOR_TARA", "CASTIGATOR_LOCALITATE",
        "CASTIGATOR_ADRESA", "TIP_PROCEDURA", "AUTORITATE_CONTRACTANTA",
        "AUTORITATE_CONTRACTANTA_CUI", "NUMAR_ANUNT", "DATA_ANUNT", "DESCRIERE",
        "TIP_INCHEIERE_CONTRACT", "NUMAR_CONTRACT", "DATA_CONTRACT", "TITLU_CONTRACT",
        "VALOARE", "MONEDA", "VALOARE_RON", "VALOARE_EUR", "CPV_CODE_ID", "CPV_CODE",
    ]

    def test_key_columns_map(self) -> None:
        m = map_columns(self.HEADER, ACHIZITII_DIRECTE)
        assert m["autoritate_cui"] == 7
        assert m["furnizor_cui"] == 1
        assert m["cpv"] == 20

    def test_prefers_valoare_ron_over_raw_valoare(self) -> None:
        """VALOARE is the contract currency; VALOARE_RON is what we compare."""
        m = map_columns(self.HEADER, ACHIZITII_DIRECTE)
        assert self.HEADER[m["valoare_ron"]] == "VALOARE_RON"


class TestMissingColumns:
    def test_reports_absent_canonical_fields(self) -> None:
        absent = missing_columns(["Autoritate contractanta"], ACHIZITII_DIRECTE)
        assert "valoare_ron" in absent
        assert "autoritate" not in absent

    def test_nothing_absent_for_a_full_header(self) -> None:
        header = [
            "Autoritate contractanta", "CUI autoritate contractanta",
            "Numar achizitie directa", "Data publicare", "Data finalizare",
            "Denumire achizitie", "Cod CPV", "Denumire CPV", "Tip contract",
            "Valoare achizitie (RON)", "Ofertant castigator", "CUI ofertant castigator",
            "Castigator localitate", "Tip procedura",
        ]
        assert missing_columns(header, ACHIZITII_DIRECTE) == []


class TestFrameworkColumns:
    def test_contracte_maps_framework_indicators(self) -> None:
        """Without these, contract values cannot be summed without double-counting."""
        header = ["Tip incheiere contract", "Incheiat prin", "Valoare contract (RON)"]
        m = map_columns(header, CONTRACTE)
        assert m["tip_incheiere"] == 0
        assert m["incheiat_prin"] == 1


class TestMalformedRows:
    """The 2016-2018 exports are caret-delimited and unquoted."""

    def test_overlong_rows_dropped_and_counted(self) -> None:
        """A "^" inside a description splits into an extra field and shifts every
        later column, putting product text in `tip_contract` and CUIs in `cpv`.
        Such a row cannot be realigned, so it is dropped rather than kept as garbage.
        """
        header = ["Autoritate contractanta", "Denumire achizitie", "Cod CPV"]
        rows = [
            ["PRIMARIA X", "produs normal", "30213100-6"],
            ["PRIMARIA Y", "produs cu", "caret", "30213100-6"],  # shifted
        ]
        recs, _, malformed = to_records(header, rows, ACHIZITII_DIRECTE, "s")
        assert malformed == 1
        assert len(recs) == 1
        assert recs[0]["autoritate"] == "PRIMARIA X"

    def test_short_rows_are_not_malformed(self) -> None:
        """Trailing empty fields are routinely omitted and must still be ingested."""
        header = ["Autoritate contractanta", "Denumire achizitie", "Cod CPV"]
        recs, _, malformed = to_records(header, [["PRIMARIA Z"]], ACHIZITII_DIRECTE, "s")
        assert malformed == 0
        assert len(recs) == 1


class TestEarlyEraSchemas:
    """The 2016-2018 exports abbreviate almost every column name."""

    INITIERE_2017: ClassVar[list[str]] = [
        "NumarAnunt", "DataPublicare", "DenumireAC", "CUI", "Judet", "TipContract",
        "Utilitati", "TipProcedura", "CriteriuAtribuire", "ValoareEstimata", "Moneda",
        "ModalitateDesfasurare", "TrimisOJEU", "FonduriComunitare", "MainCPV",
        "MainCPVName",
    ]
    CONTRACTE_2017: ClassVar[list[str]] = [
        "Castigator", "CastigatorCUI", "CastigatorTara", "CastigatorLocalitate",
        "CastigatorAdresa", "Tip", "TipContract", "TipProcedura",
        "AutoritateContractanta", "AutoritateContractantaCUI", "TipAC",
        "TipActivitateAC", "NumarAnuntAtribuire", "DataAnuntAtribuire",
        "TipIncheiereContract", "TipCriteriiAtribuire", "CuLicitatieElectronica",
        "NumarOfertePrimite", "Subcontractat", "NumarContract", "DataContract",
        "TitluContract", "Valoare", "Moneda", "ValoareRON", "ValoareEUR", "CPVCodeID",
        "CPVCode", "NumarAnuntParticipare", "DataAnuntParticipare",
        "ValoareEstimataParticipare", "MonedaValoareEstimataParticipare",
        "FonduriComunitare", "TipFinantare", "TipLegislatieID", "FondEuropean",
        "ContractPeriodic", "DepoziteGarantii", "ModalitatiFinantare",
    ]

    def test_initiere_key_fields(self) -> None:
        """Regression: only 3 of 18 fields matched, so the table was near-empty."""
        m = map_columns(self.INITIERE_2017, INITIERE)
        assert self.INITIERE_2017[m["autoritate"]] == "DenumireAC"
        assert self.INITIERE_2017[m["autoritate_cui"]] == "CUI"
        assert self.INITIERE_2017[m["valoare_estimata_ron"]] == "ValoareEstimata"
        assert self.INITIERE_2017[m["cpv"]] == "MainCPV"

    def test_initiere_carries_county_only_in_early_years(self) -> None:
        """`Judet` exists here and was dropped from later exports."""
        m = map_columns(self.INITIERE_2017, INITIERE)
        assert self.INITIERE_2017[m["judet"]] == "Judet"
        assert "judet" not in map_columns(
            ["Autoritate contractanta", "Cod CPV"], INITIERE
        )

    def test_contracte_exposes_offer_count(self) -> None:
        """Single-bidder rate is the strongest indicator in the literature.

        It is computable for the early years only: `NumarOfertePrimite` was dropped
        from the modern exports.
        """
        m = map_columns(self.CONTRACTE_2017, CONTRACTE)
        assert self.CONTRACTE_2017[m["numar_oferte"]] == "NumarOfertePrimite"
        assert "numar_oferte" not in map_columns(
            ["Autoritate contractanta", "Valoare contract (RON)"], CONTRACTE
        )

    def test_contracte_carries_estimate_inline(self) -> None:
        """No join needed for estimate-versus-award in the early years."""
        m = map_columns(self.CONTRACTE_2017, CONTRACTE)
        assert self.CONTRACTE_2017[m["valoare_estimata_ron"]] == "ValoareEstimataParticipare"

    def test_contracte_prefers_ron_over_contract_currency(self) -> None:
        m = map_columns(self.CONTRACTE_2017, CONTRACTE)
        assert self.CONTRACTE_2017[m["valoare_ron"]] == "ValoareRON"
