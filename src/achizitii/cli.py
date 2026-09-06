"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# SEAP publication and finalization dates are Romanian local time. Deriving "yesterday"
# from UTC would select the wrong day for runs scheduled near midnight.
RO_TZ = ZoneInfo("Europe/Bucharest")

from .pipeline import run


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="achizitii",
        description="Ingest Romanian public procurement line items from SEAP.",
    )
    parser.add_argument("--start", type=_parse_date, help="start date YYYY-MM-DD")
    parser.add_argument("--end", type=_parse_date, help="end date YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="convenience: ingest the last N days ending yesterday",
    )
    parser.add_argument(
        "--skip-raw",
        action="store_true",
        help="do not write the raw archive (useful for local experiments)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap records fetched per day (smoke tests only — produces partial data)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.days_back is not None:
        end = datetime.now(RO_TZ).date() - timedelta(days=1)
        start = end - timedelta(days=args.days_back - 1)
    elif args.start:
        start = args.start
        end = args.end or args.start
    else:
        parser.error("provide --start (and optionally --end) or --days-back")

    if start > end:
        parser.error("--start must not be after --end")

    summary = run(start, end, skip_raw=args.skip_raw, limit=args.limit)
    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
