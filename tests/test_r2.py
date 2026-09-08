"""Publishing to R2, and the file layout that makes it affordable."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from achizitii.r2 import MONTHLY_DIR, PRICES_DIR, R2Config, consolidate_months, upload


def _day(base: Path, day: str) -> None:
    """Write one archived day, as archive_items would."""
    year, month, _ = day.split("-")
    target = base / PRICES_DIR / f"an={year}" / f"luna={month}" / f"{day}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        f"""COPY (SELECT '{day}'::DATE AS data_finalizare, 'x' AS cpv,
                         'p' AS denumire_key, 1.0 AS pret_unitar_ron)
            TO '{target}' (FORMAT PARQUET)"""
    )
    con.close()


def test_days_are_consolidated_into_one_file_per_month(tmp_path: Path) -> None:
    """File COUNT is what R2 charges for, not size.

    Every range request is a Class B operation against a 10M/month allowance, and the
    browser pays at least one per file per query. Four thousand daily files would spend
    the free tier on metadata: ~830 full scans a month against ~25,000 for monthly files.
    """
    for day in ("2026-07-01", "2026-07-02", "2026-08-14"):
        _day(tmp_path, day)

    written = consolidate_months(tmp_path)
    names = sorted(p.name for p in written)
    assert names == ["2026-07.parquet", "2026-08.parquet"]

    con = duckdb.connect()
    july = con.execute(
        f"SELECT count(*) FROM read_parquet('{tmp_path / MONTHLY_DIR / '2026-07.parquet'}')"
    ).fetchone()[0]
    con.close()
    assert july == 2, "both July days should be in the July file"


def test_daily_archive_is_left_intact(tmp_path: Path) -> None:
    """Consolidation is for serving; collection stays daily.

    The never-shrink rule and the resume logic both key on day files, so consolidating
    must not remove them.
    """
    _day(tmp_path, "2026-07-01")
    consolidate_months(tmp_path)
    assert (tmp_path / PRICES_DIR / "an=2026" / "luna=07" / "2026-07-01.parquet").is_file()


def test_no_archive_is_not_an_error(tmp_path: Path) -> None:
    assert consolidate_months(tmp_path) == []


def test_missing_credentials_are_a_normal_state(monkeypatch, tmp_path: Path) -> None:
    """Anyone who clones the repo must be able to build the whole site.

    Absent R2 configuration means "publish locally", not "fail" — otherwise a
    contributor without bucket access cannot run the pipeline at all.
    """
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(key, raising=False)
    assert R2Config.from_env() is None
    result = upload([], tmp_path)
    assert result["configured"] is False
    assert result["uploaded"] == 0


def test_config_is_read_from_the_environment(monkeypatch) -> None:
    for key, value in (
        ("R2_ACCOUNT_ID", "acct"),
        ("R2_ACCESS_KEY_ID", "key"),
        ("R2_SECRET_ACCESS_KEY", "secret"),
        ("R2_BUCKET", "bucket"),
    ):
        monkeypatch.setenv(key, value)
    cfg = R2Config.from_env()
    assert cfg is not None
    assert cfg.endpoint == "https://acct.r2.cloudflarestorage.com"
    assert cfg.bucket == "bucket"


def test_partial_credentials_are_refused(monkeypatch) -> None:
    """Three of four set is a misconfiguration, and silently uploading nowhere hides it."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("R2_BUCKET", raising=False)
    assert R2Config.from_env() is None


def test_secrets_are_never_written_to_the_repo() -> None:
    """A credential in a tracked file is the one mistake that cannot be undone.

    Deliberately no substring check against the endpoint host here: CodeQL reads
    `"r2.cloudflarestorage.com" in src` as URL sanitization and flags it high severity,
    which is fair — that pattern IS a bypass when the string really is a URL. The
    endpoint is asserted properly, by equality, in
    test_config_is_read_from_the_environment.
    """
    src = Path("src/achizitii/r2.py").read_text()
    for leak in ('aws_access_key_id="', "AKIA", 'secret_access_key="'):
        assert leak not in src, f"{leak!r} looks like a hardcoded credential"
    assert "os.environ.get" in src, "credentials must come from the environment"


def test_closed_months_are_immutable_but_the_current_one_is_not() -> None:
    """Edge caching is the main lever on Class B ops, which the whole account shares.

    A closed month cannot change — the never-shrink rule only ever adds days to the
    CURRENT month — so it can be cached for a year and read for free thereafter. The
    current month and the manifest change daily and must not be.
    """
    from datetime import UTC, datetime

    from achizitii.r2 import _cache_control

    current = datetime.now(UTC).strftime("%Y-%m")
    assert "immutable" in _cache_control(Path("2019-03.parquet"))
    assert "immutable" not in _cache_control(Path(f"{current}.parquet"))
    assert "max-age=300" in _cache_control(Path("manifest.json"))


def test_upload_refuses_to_exceed_the_shared_budget(tmp_path: Path, monkeypatch) -> None:
    """The 10 GB free tier belongs to the account, not to this project.

    Other projects draw on the same allowance, so silently pushing past it would land on
    someone else's invoice. Failing loudly is the only honest option.
    """
    from achizitii import r2

    for key, value in (
        ("R2_ACCOUNT_ID", "a"), ("R2_ACCESS_KEY_ID", "b"),
        ("R2_SECRET_ACCESS_KEY", "c"), ("R2_BUCKET", "d"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(r2, "BUDGET_GB", 0.000001)

    big = tmp_path / "big.parquet"
    big.write_bytes(b"0" * 4096)

    boto3 = pytest.importorskip("boto3")
    assert boto3 is not None
    with pytest.raises(RuntimeError, match="budget"):
        r2.upload([big], tmp_path)


def test_same_size_different_content_is_still_uploaded(tmp_path: Path) -> None:
    """The bug the first real upload exposed.

    The skip compared byte length only. Two consecutive publishes produced a manifest of
    11,560 bytes both times and a different file each time — a date moving from
    2026-09-06 to 2026-09-07 is exactly the same length — so the bucket kept an index
    that no longer described the data, and every size check agreed it was fine.

    A closed month is genuinely immutable and may still be skipped; the manifest and the
    month currently being written may not.
    """
    from achizitii import r2

    current = datetime.now(UTC).strftime("%Y-%m")
    manifest = tmp_path / "manifest.json"
    live = tmp_path / MONTHLY_DIR / f"{current}.parquet"
    closed = tmp_path / MONTHLY_DIR / "2019-03.parquet"
    live.parent.mkdir(parents=True, exist_ok=True)
    for p in (manifest, live, closed):
        p.write_bytes(b"x" * 64)

    assert r2._mutable(manifest), "the manifest changes on every run"
    assert r2._mutable(live), "the current month is still being written"
    assert not r2._mutable(closed), "a closed month is frozen and may be skipped"
