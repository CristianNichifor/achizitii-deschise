"""The archive file index, and why the browser cannot manage without it.

A median is an argument; the acquisitions under it are the evidence. Every other view on
the site is an aggregate a reader has to take on trust, so the drill-down from a price
group to its individual line items is the one screen that lets someone check the claim.

It exists only because the manifest names the files. DuckDB-Wasm reads Parquet over HTTP
range requests, and a glob needs a directory listing that HTTP does not provide. Verified
in a real browser against a real server:

    read_parquet('.../preturi/**/*.parquet')      -> WebAssembly.Exception
    read_parquet('.../preturi/an=2026/luna=07/*') -> WebAssembly.Exception
    read_parquet(['.../a.parquet', '.../b.parquet']) -> OK, 22,349 rows

So the publisher enumerates, and the browser picks from the list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = Path("site/data/manifest.json")

# The ingest workflow runs unit tests before rebuilding the generated Pages bundle. Keep the
# publication contract tests active when a bundle is present, but do not make a held/offline ingest
# fail merely because generated site output is intentionally absent from the checkout.
pytestmark = pytest.mark.skipif(
    not MANIFEST.is_file(), reason="generated site/data/manifest.json is not present"
)
INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_names_every_archive_file(manifest: dict) -> None:
    archive = manifest.get("preturi_arhiva")
    assert archive, "the browser cannot glob over HTTP; the files must be listed"
    on_disk = sorted(
        str(p.relative_to(Path("site/data") / archive["baza"])).replace("\\", "/")
        for p in (Path("site/data") / archive["baza"]).rglob("*.parquet")
    )
    assert archive["fisiere"] == on_disk, "the index and the archive have diverged"


def test_every_listed_file_exists(manifest: dict) -> None:
    """A named file that 404s is worse than one that was never named.

    DuckDB fails the whole query, so one missing file takes out the drill-down for every
    group, not just the group that needed it.
    """
    archive = manifest["preturi_arhiva"]
    base = Path("site/data") / archive["baza"]
    missing = [f for f in archive["fisiere"] if not (base / f).is_file()]
    assert not missing, f"listed but absent: {missing}"


def test_file_names_carry_their_date(manifest: dict) -> None:
    """The date in the name is load-bearing, not decoration.

    The browser narrows to the days a group actually spans. Without a parseable date it
    would have to open the whole archive on every drill-down — one HTTP request per file,
    forever, growing by a file a day.
    """
    import re

    for name in manifest["preturi_arhiva"]["fisiere"]:
        assert re.search(r"\d{4}-\d{2}-\d{2}\.parquet$", name), (
            f"{name} has no date, so it cannot be excluded from a query window"
        )


def test_the_drilldown_narrows_to_the_group_date_range() -> None:
    """Guard the narrowing itself.

    Verified in a browser: drilling into a group spanning 2026-07-01..07-05 fetched five
    files and left the 2026-09 file alone.
    """
    source = INDEX.read_text(encoding="utf-8")
    assert "function archiveFiles(" in source
    body = source.split("function archiveFiles(")[1][:1200]
    assert "m[1] >= from" in body and "m[1] <= to" in body, (
        "the file list must be filtered by the group's date range"
    )


def test_an_undateable_file_is_included_rather_than_dropped() -> None:
    """Failing open. Showing one extra acquisition is a smaller error than hiding one."""
    source = INDEX.read_text(encoding="utf-8")
    body = source.split("function archiveFiles(")[1][:1200]
    assert "if (!m) return true;" in body


def test_the_detail_view_is_not_a_tab() -> None:
    """It is reached by clicking a row; a twelfth tab would not say what it shows."""
    source = INDEX.read_text(encoding="utf-8")
    assert "if (v.hidden) continue;" in source
    assert "hidden: true," in source


def test_leaving_the_drilldown_forgets_the_group() -> None:
    """Otherwise a later click can inherit a stale group and show the wrong prices."""
    source = INDEX.read_text(encoding="utf-8")
    assert "if (key !== 'detaliu') drill = null;" in source
