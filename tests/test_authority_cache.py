"""The contracting-authority cache, and the one thing it must never remember.

Authority lookups are the second-largest cost in a run: a real weekday holds ~8,200
acquisitions across ~2,992 distinct authorities, and those 2,992 calls are sequential at
4 rps — roughly 12 minutes of a ~45 minute day. Persisting them across runs is worth real
time on the backfill, which is why these tests exist at all.

The risk that comes with persistence is that a *failure* gets written down. A cached empty
row is permanent: nothing would ever look that authority up again, so one timeout would
silently cost a county forever. That is what the first test guards.
"""

from __future__ import annotations

from typing import Any

from achizitii.pipeline import AuthorityResolver


class FakeClient:
    """Records every path fetched, so tests can assert what was NOT fetched."""

    def __init__(self, responses: dict[int, Any] | None = None, fail: set[int] | None = None):
        self.responses = responses or {}
        self.fail = fail or set()
        self.calls: list[str] = []

    def get(self, path: str) -> Any:
        self.calls.append(path)
        authority_id = int(path.rsplit("/", 1)[-1])
        if authority_id in self.fail:
            raise ConnectionResetError("connection reset")
        return self.responses.get(authority_id, {})


def _payload(county: str) -> dict[str, Any]:
    return {"county": county, "city": "Cluj-Napoca", "fiscalNumber": 4288331, "isUtility": False}


def test_failed_lookups_are_never_written_to_the_cache(tmp_path) -> None:
    """A timeout must cost one run, not every future one.

    If the empty result were persisted, this authority would be permanently county-less:
    the cache would answer for it and the lookup would never be retried.
    """
    path = tmp_path / "autoritati.parquet"
    client = FakeClient(responses={1: _payload("Cluj")}, fail={2})
    resolver = AuthorityResolver(client, cache_path=path)
    resolver.get(1)
    resolver.get(2)
    assert resolver.save() == 1

    reloaded = AuthorityResolver(FakeClient(), cache_path=path)
    assert reloaded.get(1)["county"] == "Cluj"
    assert 2 not in reloaded._cache, "a failed lookup was persisted"


def test_a_cached_authority_is_not_fetched_again(tmp_path) -> None:
    """The whole point: a warm run pays only for authorities it has never seen."""
    path = tmp_path / "autoritati.parquet"
    first = FakeClient(responses={7: _payload("Timiș")})
    cold = AuthorityResolver(first, cache_path=path)
    cold.get(7)
    assert cold.save() == 1

    second = FakeClient(responses={7: _payload("Timiș"), 8: _payload("Iași")})
    warm = AuthorityResolver(second, cache_path=path)
    warm.get(7)
    warm.get(8)
    assert second.calls == ["/Entity/getCAEntityView/8"], (
        f"authority 7 was already known but was fetched again: {second.calls}"
    )


def test_a_cached_value_is_indistinguishable_from_a_fresh_one(tmp_path) -> None:
    """Provenance must not change a type.

    `fiscalNumber` arrives as an integer from the API and comes back as a string from
    Parquet. Normalising on the way in means downstream code cannot tell — and cannot
    develop a dependence on which run first saw the authority.
    """
    path = tmp_path / "autoritati.parquet"
    client = FakeClient(responses={5: _payload("Brașov")})
    fresh = AuthorityResolver(client, cache_path=path)
    fresh_value = dict(fresh.get(5))
    fresh.save()

    cached_value = AuthorityResolver(FakeClient(), cache_path=path).get(5)
    assert fresh_value == cached_value, f"{fresh_value} != {cached_value}"
    assert isinstance(fresh_value["fiscal_number"], str)


def test_a_missing_cache_is_not_an_error(tmp_path) -> None:
    """Deleting the file must only ever mean "the next run is cold"."""
    resolver = AuthorityResolver(FakeClient(responses={1: _payload("Dolj")}),
                                 cache_path=tmp_path / "absent.parquet")
    assert resolver.get(1)["county"] == "Dolj"


def test_a_corrupt_cache_starts_cold_instead_of_failing(tmp_path) -> None:
    """An unreadable cache is a performance problem, never a correctness one."""
    path = tmp_path / "autoritati.parquet"
    path.write_bytes(b"not a parquet file")
    resolver = AuthorityResolver(FakeClient(responses={1: _payload("Arad")}), cache_path=path)
    assert resolver.get(1)["county"] == "Arad"


def test_no_authority_id_needs_no_lookup(tmp_path) -> None:
    # An explicit tmp path, not None: None would fall back to the repository's real
    # cache, making the test depend on checked-in data.
    client = FakeClient()
    assert AuthorityResolver(client, cache_path=tmp_path / "c.parquet").get(None) == {}
    assert client.calls == []
