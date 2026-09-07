"""RUTI mapping, and the boundary it must not cross.

The register is 666 meetings fetched in 7 requests, which makes it cheap to mirror and
tempting to join to procurement. It must not be joined, and the reasons are recorded in
`src/achizitii/ruti.py`. The last test here guards that decision, because the temptation
will recur: the whole point of the register is that it names who met whom.

Everything else is offline mapping — no network in the test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from achizitii.ruti import to_meeting, to_records

RECORD = {
    "id": "5f7b7a1e-a835-e811-cb0a-6a9c31360e74",
    "name": "Întâlnire - Director executiv, Confederația Patronală Concordia",
    "date_entered": "2026-09-05T15:13:00+00:00",
    "description": "Discuții pe baza propunerilor CP Concordia",
    "concluzii": "",
    "data_intalnirii": "2026-09-09T00:00:00+00:00",
    "locul_intalnirii": "Birou Președinte, Senat, Grupul AUR",
    "anulat": "nu",
    "justificare_anulare": "",
    "creator_name": "Ramona-Ioana Bruynseels",
    "function_name": "Deputat",
    "institutie": "Camera Deputaților",
    "decident_name": "Ramona-Ioana Bruynseels",
}


def test_maps_the_documented_fields() -> None:
    m = to_meeting(RECORD)
    assert m.id == RECORD["id"]
    assert m.decident == "Ramona-Ioana Bruynseels"
    assert m.functie == "Deputat"
    assert m.institutie == "Camera Deputaților"
    assert m.data_intalnirii == datetime(2026, 9, 9, tzinfo=UTC)
    assert m.data_publicarii == datetime(2026, 9, 5, 15, 13, tzinfo=UTC)


def test_empty_strings_become_null_not_empty() -> None:
    """`concluzii` is blank on 85% of records; blank and absent must not differ."""
    m = to_meeting(RECORD)
    assert m.concluzii is None
    assert m.justificare_anulare is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("da", True), ("DA", True), ("nu", False), ("", False), (None, False), ("x", False)],
)
def test_cancelled_flag_is_conservative(raw, expected) -> None:
    """Only a recognisable "da" cancels.

    Inventing a cancellation is worse than missing one: it would wrongly suggest an
    official withdrew from a meeting they actually held.
    """
    assert to_meeting({**RECORD, "anulat": raw}).anulat is expected


def test_unparsable_dates_are_dropped_not_guessed() -> None:
    m = to_meeting({**RECORD, "data_intalnirii": "not a date"})
    assert m.data_intalnirii is None


def test_repeated_records_are_deduplicated() -> None:
    """Paging a live register can return the same meeting twice.

    A new entry published between requests shifts the window, which would otherwise
    inflate every count silently.
    """
    records = to_records([RECORD, RECORD, {**RECORD, "id": "other"}])
    assert len(records) == 2


def test_records_without_an_id_are_dropped() -> None:
    assert to_records([{**RECORD, "id": ""}]) == []


def test_ruti_is_not_joined_to_procurement() -> None:
    """Guard on a deliberate omission.

    The register carries no CUI, CIF or organisation field — the third party exists only
    inside free-text `name` and `description`. Its institutions are ministries and
    parliament, which are 1.70% of our archive, and it holds 666 records over eleven
    months. A supplier<->meeting indicator would therefore fire almost never and, when
    it did, would name a real individual on the strength of a fuzzy string match.

    If a future version of the register publishes an organisation identifier, delete
    this test along with the restriction — but not before.
    """
    from pathlib import Path

    from achizitii import indicators

    source = Path(indicators.__file__).read_text()
    assert "ruti" not in source.lower(), (
        "an indicator now references RUTI. The register cannot support a join: no "
        "organisation identifier, 1.70% institutional overlap, 666 records. See the "
        "module docstring in src/achizitii/ruti.py."
    )
    # And the mapped schema must not have grown a company identifier without review.
    fields = set(to_meeting(RECORD).__dict__)
    assert not (fields & {"cui", "cif", "furnizor_cui", "organizatie_cui"})
