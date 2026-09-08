"""The request rate, against the limit SEAP actually published.

SEAP stated its thresholds on 2026-07-15: 500 accesses in 5 minutes from one IP, and 50
in any single second. That is 1.67 requests per second sustained.

This project got IP-blocked on 2026-09-07 after a burst test showed 37 records a second
with flat latency and that was read as permission. The fix set MAX_RPS to 4.0 and called
it conservative. 4.0 rps is 1,200 requests per 5 minutes — still 2.4x the published
limit, and it stayed that way for a day until someone read the announcement.

So the number lives in a test now. A rate limit that exists only as a constant with a
long comment above it is a rate limit that gets raised by whoever is in a hurry.
"""

from __future__ import annotations

from achizitii import config

SEAP_MAX_PER_5_MIN = 500
"""Published: accesses from one IP in a 5-minute window before restriction."""

SEAP_MAX_PER_SECOND = 50
"""Published: burst ceiling in any single second."""


def test_sustained_rate_is_within_the_published_limit() -> None:
    per_window = config.MAX_RPS * 300
    assert per_window <= SEAP_MAX_PER_5_MIN, (
        f"MAX_RPS={config.MAX_RPS} is {per_window:.0f} requests per 5 minutes, over SEAP's "
        f"published {SEAP_MAX_PER_5_MIN}. This is the mistake that caused the 2026-09-07 "
        "block and survived the first fix. If more throughput is needed, ask SEAP support "
        "— see docs/incident-2026-09-07-seap-block.md."
    )


def test_burst_rate_is_within_the_published_limit() -> None:
    """Workers share one limiter, so the worst case is MAX_RPS, not MAX_RPS x workers.

    Asserted anyway: if the limiter ever moves per-worker, the burst ceiling is the first
    thing that breaks and the symptom would be another block, not a test failure.
    """
    worst_case = config.MAX_RPS * config.DETAIL_WORKERS
    assert worst_case <= SEAP_MAX_PER_SECOND, (
        f"{config.DETAIL_WORKERS} workers at {config.MAX_RPS} rps could reach "
        f"{worst_case}/s against a published ceiling of {SEAP_MAX_PER_SECOND}"
    )


def test_headroom_is_left_for_retries() -> None:
    """Running exactly at the line means any retry crosses it.

    RETRIES>0 means a failing request is re-sent, and those re-sends count too.
    """
    assert config.MAX_RPS * 300 <= SEAP_MAX_PER_5_MIN * 0.95, (
        "leave at least 5% headroom under the published limit for retries"
    )
