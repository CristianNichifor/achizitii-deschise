"""Resolving a contracting authority's county, and the requests that buys.

The county used to cost one SEAP request per authority — 2,992 on a real weekday, against
a service that has already IP-blocked this project once and publishes a ceiling of 500
requests per 5 minutes. It now costs about 30 requests to ANAF, which answers 100 fiscal
codes at a time, and none at all to SEAP.

Two properties carry that saving, and both are easy to lose by accident:

* the fetch must be BATCHED, which means resolving before the row loop rather than inside
  it — a lazy per-row lookup would quietly restore the old request count against a
  different service;
* the cache must PERSIST across runs, so a backfill walking month after month converges
  on paying nothing.

Everything here is offline: `firme.fetch_batch` is replaced, so no test touches ANAF.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from achizitii import firme
from achizitii.pipeline import AuthorityResolver


def _firma(cui: str, judet: str | None) -> firme.Firma:
    return firme.Firma(
        cui=cui, denumire=f"INSTITUTIA {cui}", judet=judet, cod_judet_auto=None,
        data_inregistrare=None, stare_inregistrare=None, inactiv=False,
        data_inactivare=None, data_reactivare=None, data_radiere=None,
        platitor_tva=True, verificat_la=date(2026, 9, 8),
    )


class FakeAnaf:
    """Stands in for ANAF, and records the shape of what was asked."""

    def __init__(self, known: dict[str, str | None]):
        self.known = known
        self.batches: list[list[str]] = []

    def __call__(self, cuis: list[str], on_date: date, limiter: Any) -> list[firme.Firma]:
        self.batches.append(list(cuis))
        # A code ANAF does not know is simply absent from the reply.
        return [_firma(c, self.known[c]) for c in cuis if c in self.known]

    @property
    def requests(self) -> int:
        return len(self.batches)

    @property
    def asked(self) -> list[str]:
        return [c for b in self.batches for c in b]


@pytest.fixture
def anaf(monkeypatch) -> FakeAnaf:
    fake = FakeAnaf({"4340536": "IAŞI", "5001864": "DOLJ", "2816464": "BACĂU"})
    monkeypatch.setattr(firme, "fetch_batch", fake)
    return fake


def test_counties_are_fetched_in_batches_not_one_by_one(tmp_path, anaf) -> None:
    """The whole point: 250 authorities must cost 3 requests, not 250."""
    many = {str(1_000_000 + i): "CLUJ" for i in range(250)}
    anaf.known.update(many)
    resolver = AuthorityResolver(cache_path=tmp_path / "a.parquet")
    resolver.prime(many)
    assert anaf.requests == 3, f"expected 3 batches of <={firme.BATCH}, got {anaf.requests}"
    assert all(len(b) <= firme.BATCH for b in anaf.batches)


def test_a_known_county_is_returned(tmp_path, anaf) -> None:
    resolver = AuthorityResolver(cache_path=tmp_path / "a.parquet")
    resolver.prime(["4340536"])
    assert resolver.get("4340536")["county"] == "IAŞI"


def test_the_cache_survives_the_run(tmp_path, anaf, monkeypatch) -> None:
    """A warm run must ask ANAF nothing it already knows."""
    path = tmp_path / "a.parquet"
    cold = AuthorityResolver(cache_path=path)
    cold.prime(["4340536", "5001864"])
    assert cold.save() == 2

    second = FakeAnaf(dict(anaf.known))
    monkeypatch.setattr(firme, "fetch_batch", second)
    warm = AuthorityResolver(cache_path=path)
    warm.prime(["4340536", "5001864", "2816464"])

    assert second.asked == ["2816464"], f"re-fetched known authorities: {second.asked}"
    assert warm.get("4340536")["county"] == "IAŞI"


def test_an_unknown_code_is_not_cached_as_absent(tmp_path, anaf) -> None:
    """A code ANAF does not return must be retried, not remembered as county-less.

    Caching the absence would make one bad batch permanent: nothing would look it up
    again and the authority would stay without a county for good. Retrying costs part of
    a single request.
    """
    path = tmp_path / "a.parquet"
    resolver = AuthorityResolver(cache_path=path)
    resolver.prime(["4340536", "9999999999"])
    assert resolver.get("9999999999") == {}
    resolver.save()

    again = AuthorityResolver(cache_path=path)
    assert "9999999999" not in again._cache, "an unknown code was written to the cache"


def test_a_missing_cache_is_not_an_error(tmp_path, anaf) -> None:
    """Deleting the file must only ever mean "the next run is cold"."""
    resolver = AuthorityResolver(cache_path=tmp_path / "absent.parquet")
    resolver.prime(["5001864"])
    assert resolver.get("5001864")["county"] == "DOLJ"


def test_a_cache_in_the_old_shape_is_ignored(tmp_path, anaf) -> None:
    """The previous cache was keyed by SEAP's internal id and holds no CUI.

    It cannot be converted, so it must read as unusable rather than crash the run. The
    cost is a single cold pass.
    """
    import duckdb

    path = tmp_path / "a.parquet"
    con = duckdb.connect()
    con.execute("CREATE TABLE old (authority_id BIGINT, county VARCHAR)")
    con.execute("INSERT INTO old VALUES (17, 'CLUJ')")
    con.execute(f"COPY old TO '{path}' (FORMAT PARQUET)")
    con.close()

    resolver = AuthorityResolver(cache_path=path)
    resolver.prime(["5001864"])
    assert resolver.get("5001864")["county"] == "DOLJ"


def test_a_corrupt_cache_starts_cold_instead_of_failing(tmp_path, anaf) -> None:
    path = tmp_path / "a.parquet"
    path.write_bytes(b"not a parquet file")
    resolver = AuthorityResolver(cache_path=path)
    resolver.prime(["2816464"])
    assert resolver.get("2816464")["county"] == "BACĂU"


def test_no_cui_needs_no_lookup(tmp_path, anaf) -> None:
    resolver = AuthorityResolver(cache_path=tmp_path / "a.parquet")
    resolver.prime([None, "", "  "])
    assert anaf.requests == 0
    assert resolver.get(None) == {}


def test_seap_is_never_called_for_a_county() -> None:
    """Guard on the reason this change exists.

    If a SEAP entity lookup reappears, the request count returns to one per authority
    against the service that blocked us. The saving is not the speed; it is the load
    moved off SEAP.

    Checked as a CALL, not as a word: the docstring names the removed endpoint in order
    to explain why it went, and a bare substring search flags its own explanation. The
    first version of this test did exactly that.
    """
    source = Path("src/achizitii/pipeline.py").read_text(encoding="utf-8")
    # No \b before "client": underscore is a word character, so \bclient would not match
    # `self._client.get(` — which is the exact line this test exists to catch. Verified.
    calls = re.findall(r"client\.get\s*\(", source)
    assert not calls, (
        f"pipeline.py makes {len(calls)} SEAP client call(s) again. Resolving a county "
        "per authority is what this change removed; see the AuthorityResolver docstring."
    )


def test_to_rows_populates_the_county_through_the_new_path(tmp_path, anaf) -> None:
    """The integration point, which had no test at all before this change.

    `prime` and `get` can both behave correctly in isolation while the wiring in `to_rows`
    is wrong, and the symptom would be silent: every `judet` null, no error, no failing
    test, and a published archive that has quietly lost its geography. That is exactly the
    kind of fault this project has already shipped once.
    """
    from achizitii.pipeline import to_rows

    detail = {
        "directAcquisitionID": 991,
        "contractingAuthority": "4340536 SPITALUL CLINIC",
        "supplier": "21409203 FURNIZOR SRL",
        "finalizationDate": "2026-07-01T10:00:00",
        "sysAcquisitionContractType": {"text": "furnizare"},
        "directAcquisitionItems": [
            {
                "directAcquisitionItemID": 1,
                "catalogItemName": "banane",
                "cpvCode": {"text": "03222111-4 Banane"},
                "itemQuantity": 50,
                "itemMeasureUnit": "kg",
                "itemClosingPrice": 8.5,
            }
        ],
    }
    resolver = AuthorityResolver(cache_path=tmp_path / "a.parquet")
    rows = to_rows([detail], resolver)

    assert len(rows) == 1
    assert rows[0]["autoritate_cui"] == "4340536"
    assert rows[0]["judet"] == "IAŞI", "the county did not reach the row"
    # One batch for one authority — resolved before the loop, not inside it.
    assert anaf.requests == 1


def test_to_rows_batches_once_for_many_acquisitions(tmp_path, anaf) -> None:
    """Fifty acquisitions across three authorities must cost one request, not fifty."""
    from achizitii.pipeline import to_rows

    cuis = ["4340536", "5001864", "2816464"]
    details = [
        {
            "directAcquisitionID": i,
            "contractingAuthority": f"{cuis[i % 3]} INSTITUTIA",
            "supplier": "21409203 FURNIZOR SRL",
            "finalizationDate": "2026-07-01T10:00:00",
            "directAcquisitionItems": [],
        }
        for i in range(50)
    ]
    AuthorityResolver(cache_path=tmp_path / "a.parquet")
    to_rows(details, AuthorityResolver(cache_path=tmp_path / "a.parquet"))
    assert anaf.requests == 1, f"one batch expected, got {anaf.requests}"
    assert sorted(anaf.asked) == sorted(cuis)
