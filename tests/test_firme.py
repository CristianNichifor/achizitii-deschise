"""Supplier company facts from ANAF, mapped offline.

No network in the suite: every test here works on a recorded response. The live service
is free and keyless, but a test that depends on it fails when ANAF is down rather than
when this code is wrong.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from achizitii.firme import (
    Firma,
    cached_cuis,
    inactiv_la,
    normalise_cui,
    radiata_la,
    save,
    to_firma,
)

# A real response, trimmed to the sections this module reads.
RESPONSE = {
    "date_generale": {
        "cui": 2816464,
        "denumire": "DEDEMAN SRL",
        "adresa": "JUD. BACĂU, MUN. BACĂU, STR. ALEXEI TOLSTOI, NR.8",
        "stare_inregistrare": "INREGISTRAT din data 27.01.1993",
    },
    "inregistrare_scop_Tva": {"scpTVA": True},
    "stare_inactiv": {
        "dataInactivare": "",
        "dataReactivare": "",
        "dataRadiere": "",
        "statusInactivi": False,
    },
    "adresa_sediu_social": {
        "sdenumire_Judet": "BACĂU",
        "scod_JudetAuto": "BC",
        "sdenumire_Localitate": "Mun. Bacău",
    },
}

CHECKED = date(2026, 9, 7)


def test_maps_the_fields_we_keep() -> None:
    f = to_firma(RESPONSE, CHECKED)
    assert f.cui == "2816464"
    assert f.denumire == "DEDEMAN SRL"
    assert f.judet == "BACĂU"
    assert f.cod_judet_auto == "BC"
    assert f.platitor_tva is True
    assert f.inactiv is False
    assert f.verificat_la == CHECKED


def test_registration_date_is_parsed_out_of_prose() -> None:
    """ANAF gives "INREGISTRAT din data 27.01.1993", not a date field."""
    assert to_firma(RESPONSE, CHECKED).data_inregistrare == date(1993, 1, 27)


def test_unrecognised_wording_leaves_the_date_absent() -> None:
    """A missing field is recoverable; a wrong one is not.

    If ANAF changes the phrasing, the date must go missing rather than be guessed at
    from whatever numbers happen to appear.
    """
    for state in ("RADIAT", "INREGISTRAT din 27 ianuarie 1993", "", "INREGISTRAT din data 99.99.9999"):
        row = {**RESPONSE, "date_generale": {**RESPONSE["date_generale"], "stare_inregistrare": state}}
        assert to_firma(row, CHECKED).data_inregistrare is None


def test_no_personal_address_is_retained() -> None:
    """For a sole trader the registered address can be a home address.

    Only the county is kept, and this test fails if a future change starts storing the
    street.
    """
    kept = set(to_firma(RESPONSE, CHECKED).__dict__)
    assert not (kept & {"adresa", "strada", "numar_strada", "cod_postal", "telefon"})


def test_inactive_and_struck_off_are_read() -> None:
    row = {
        **RESPONSE,
        "stare_inactiv": {
            "statusInactivi": True,
            "dataInactivare": "2019-03-15",
            "dataRadiere": "2021-07-01",
            "dataReactivare": "",
        },
    }
    f = to_firma(row, CHECKED)
    assert f.inactiv is True
    assert f.data_inactivare == date(2019, 3, 15)
    assert f.data_radiere == date(2021, 7, 1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2816464", "2816464"),
        ("RO 2816464", "2816464"),      # the archive's most common form
        ("RO2816464", "2816464"),
        ("  2816464  ", "2816464"),
        ("0002816464", "2816464"),      # leading zeros are padding, not the code
        ("1", None),                     # too short to be a fiscal code
        ("12345678901234", None),        # too long
        ("", None),
        (None, None),
        ("RO", None),
    ],
)
def test_cui_normalisation(raw, expected) -> None:
    assert normalise_cui(raw) == expected


def _firma(cui: str, judet: str, checked: date) -> Firma:
    return Firma(
        cui=cui, denumire="X", judet=judet, cod_judet_auto=None,
        data_inregistrare=None, stare_inregistrare=None, inactiv=False,
        data_inactivare=None, data_reactivare=None, data_radiere=None,
        platitor_tva=False,
        verificat_la=checked,
    )


def test_cache_keeps_the_newest_answer_per_company(tmp_path: Path) -> None:
    """A company's status changes; a re-check must supersede, not duplicate."""
    cache = tmp_path / "anaf.parquet"
    save([_firma("111", "CLUJ", date(2026, 1, 1))], cache)
    save([_firma("111", "ILFOV", date(2026, 9, 7))], cache)

    import duckdb

    con = duckdb.connect()
    rows = con.execute(f"SELECT cui, judet FROM read_parquet('{cache}')").fetchall()
    con.close()
    assert rows == [("111", "ILFOV")], "the cache should hold one current row per code"


def test_cache_reports_what_it_holds_so_a_run_can_resume(tmp_path: Path) -> None:
    """A two-hour job that restarts from zero is a job nobody runs twice."""
    cache = tmp_path / "anaf.parquet"
    assert cached_cuis(cache) == set()
    save([_firma("111", "CLUJ", CHECKED), _firma("222", "ILFOV", CHECKED)], cache)
    assert cached_cuis(cache) == {"111", "222"}


def test_status_is_judged_at_the_award_date_not_today() -> None:
    """The single most important rule in this module.

    ANAF answers "is it inactive?" relative to the date you ask about. KAMPALAMPI SRL
    reads inactive today and active as of 2022. An award in 2022 must therefore be
    judged against 2022, or the finding is simply false about a named company.
    """
    f = to_firma(
        {
            **RESPONSE,
            "stare_inactiv": {
                "statusInactivi": True,
                "dataInactivare": "2026-02-13",
                "dataReactivare": "",
                "dataRadiere": "",
            },
        },
        CHECKED,
    )
    assert inactiv_la(f, date(2026, 9, 7)) is True
    assert inactiv_la(f, date(2026, 2, 13)) is True   # the day it took effect
    assert inactiv_la(f, date(2022, 6, 15)) is False  # years before
    assert inactiv_la(f, date(2026, 2, 12)) is False  # the day before


def test_reactivation_ends_the_inactive_period() -> None:
    """Otherwise one bad year taints every award a company ever wins afterwards."""
    f = to_firma(
        {
            **RESPONSE,
            "stare_inactiv": {
                "statusInactivi": False,
                "dataInactivare": "2019-03-01",
                "dataReactivare": "2020-05-01",
                "dataRadiere": "",
            },
        },
        CHECKED,
    )
    assert inactiv_la(f, date(2019, 6, 1)) is True    # inside the gap
    assert inactiv_la(f, date(2020, 5, 1)) is False   # reactivated
    assert inactiv_la(f, date(2024, 1, 1)) is False   # long after
    assert inactiv_la(f, date(2018, 1, 1)) is False   # before it ever happened


def test_never_inactive_is_never_flagged() -> None:
    assert inactiv_la(to_firma(RESPONSE, CHECKED), date(2020, 1, 1)) is False


def test_struck_off_is_also_point_in_time() -> None:
    f = to_firma(
        {**RESPONSE, "stare_inactiv": {"statusInactivi": False, "dataInactivare": "",
                                        "dataReactivare": "", "dataRadiere": "2021-07-01"}},
        CHECKED,
    )
    assert radiata_la(f, date(2021, 7, 1)) is True
    assert radiata_la(f, date(2021, 6, 30)) is False
    assert radiata_la(to_firma(RESPONSE, CHECKED), date(2030, 1, 1)) is False
