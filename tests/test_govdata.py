"""Tests for bulk-export format and schema drift handling."""

from __future__ import annotations

import io
import zipfile

import pytest

from achizitii.govdata import (
    ACHIZITII_DIRECTE,
    CONTRACTE,
    MODIFICARI,
    TABLES,
    _sniff_delimiter,
    map_columns,
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
        recs, unmapped = to_records(header, rows, ACHIZITII_DIRECTE, "test.xlsx")
        assert recs[0]["autoritate"] == "PRIMARIA X"
        assert recs[0]["valoare_ron"] == "1234.5"
        assert recs[0]["sursa"] == "test.xlsx"
        assert recs[0]["furnizor"] is None          # absent column -> None, not missing
        assert unmapped == ["Coloana Noua"]         # surfaced, never silently dropped

    def test_blank_rows_skipped(self) -> None:
        recs, _ = to_records(
            ["Autoritate contractanta"], [[""], ["PRIMARIA Y"]], ACHIZITII_DIRECTE, "s"
        )
        assert len(recs) == 1

    def test_short_rows_do_not_crash(self) -> None:
        header = ["Autoritate contractanta", "Valoare achizitie (RON)"]
        recs, _ = to_records(header, [["doar-un-camp"]], ACHIZITII_DIRECTE, "s")
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
        ],
    )
    def test_classification(self, name: str, expected: str) -> None:
        matched = [t.key for t in TABLES if t.match.search(name)]
        assert matched and matched[0] == expected, f"{name!r} -> {matched}"

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
