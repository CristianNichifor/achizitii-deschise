"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# SEAP publication and finalization dates are Romanian local time. Deriving "yesterday"
# from UTC would select the wrong day for runs scheduled near midnight.
RO_TZ = ZoneInfo("Europe/Bucharest")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _years(value: str) -> list[int]:
    """Accept '2024', '2016-2026' or '2019,2021'."""
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.extend(range(lo, hi + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def _emit(payload: object) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="achizitii",
        description="Romanian public procurement: line items, OCDS releases, risk indicators.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- seap: per-record line items from the live API ------------------------------
    seap = sub.add_parser("seap", help="ingest line items from the SEAP api-pub endpoints")
    seap.add_argument("--start", type=_parse_date)
    seap.add_argument("--end", type=_parse_date)
    seap.add_argument("--days-back", type=int)
    seap.add_argument("--skip-raw", action="store_true")
    seap.add_argument("--limit", type=int, help="cap records per day (smoke tests only)")

    # -- gov: bulk quarterly exports -----------------------------------------------
    gov = sub.add_parser("gov", help="ingest the data.gov.ro quarterly exports")
    gov.add_argument("--years", type=_years, required=True, help="e.g. 2024 or 2016-2026")
    gov.add_argument(
        "--tables", nargs="*", default=None,
        help="subset of: achizitii_directe contracte fara_anunt initiere modificari",
    )
    gov.add_argument(
        "--check-coverage",
        action="store_true",
        help="report which tables each year matches, without downloading anything",
    )

    # -- validate ------------------------------------------------------------------
    sub.add_parser(
        "validate",
        help="cross-year sanity checks on ingested data (null rates, category coverage)",
    )

    # -- ceilings ------------------------------------------------------------------
    ceil = sub.add_parser(
        "ceilings",
        help="detect the direct-acquisition ceiling per year and corroborate the declared one",
    )
    ceil.add_argument("--years", type=_years, default=None)

    # -- cluster -------------------------------------------------------------------
    cl = sub.add_parser(
        "cluster",
        help="propose synonym clusters for human review (never applied automatically)",
    )
    cl.add_argument("--cpv", default=None, help="restrict to a CPV prefix, e.g. 3911")
    cl.add_argument("--limit", type=int, default=40, help="candidate keys to send")
    cl.add_argument(
        "--dry-run", action="store_true",
        help="show candidates and prompt size without calling any API (needs no key)",
    )

    # -- indicators ----------------------------------------------------------------
    ind = sub.add_parser("indicators", help="run risk indicators over ingested bulk data")
    ind.add_argument("--only", nargs="*", default=None, help="indicator identifiers")
    ind.add_argument("--on-date", type=_parse_date, default=None,
                     help="evaluate rule validity as of this date")
    ind.add_argument("--list", action="store_true", help="list indicators and exit")

    # -- publish -------------------------------------------------------------------
    pub = sub.add_parser(
        "publish", help="build the browser-queryable bundle served from GitHub Pages"
    )
    pub.add_argument(
        "--out", type=Path, default=None,
        help="output directory (default: site/data)",
    )
    pub.add_argument(
        "--only", choices=["preturi"], default=None,
        help=(
            "rebuild only this section and merge into the existing manifest. "
            "'preturi' needs no bulk archive, so it works on a GitHub runner"
        ),
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "seap":
        from .pipeline import run

        if args.days_back is not None:
            end = datetime.now(RO_TZ).date() - timedelta(days=1)
            start = end - timedelta(days=args.days_back - 1)
        elif args.start:
            start, end = args.start, args.end or args.start
        else:
            seap.error("provide --start (and optionally --end) or --days-back")
        if start > end:
            seap.error("--start must not be after --end")
        _emit(run(start, end, skip_raw=args.skip_raw, limit=args.limit))
        return 0

    if args.command == "gov":
        from .govpipeline import check_coverage, ingest_years

        if args.check_coverage:
            coverage = check_coverage(args.years)
            _emit(coverage)
            return 0

        summary = ingest_years(args.years, args.tables)
        _emit(summary)
        # Per-year and per-resource errors are caught so one bad file cannot abort a
        # decade-long backfill. That must not turn a total failure into a success:
        # a run that ingested nothing while recording errors is a failure.
        errored = [f for f in summary["files"] if f.get("error")]
        if summary["total_rows"] == 0 and errored:
            print(
                f"ERROR: ingested 0 rows; {len(errored)} resource(s) failed. "
                f"First: {errored[0]['error']}",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.command == "validate":
        from .govpipeline import validate

        report = validate()
        _emit(report)
        if not report["ok"]:
            print(
                f"ERROR: {len(report['problems'])} data-integrity problem(s) found.",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.command == "ceilings":
        from .govpipeline import ceilings_report

        _emit(ceilings_report(args.years))
        return 0

    if args.command == "cluster":
        from .govpipeline import propose_clusters

        result = propose_clusters(args.cpv, args.limit, args.dry_run)
        _emit(result)
        # Unavailability is an expected outcome for a free experimental endpoint, not a
        # pipeline failure — nothing downstream depends on it.
        return 0

    if args.command == "publish":
        from .publish import build

        manifest = build(args.out, only=args.only)
        _emit(manifest)
        return 0

    if args.command == "indicators":
        from .indicators import INDICATORS

        if args.list:
            _emit([
                {
                    "identifier": i.identifier,
                    "name": i.name_ro,
                    "legal_basis": i.legal_basis,
                    "applies_to": list(i.applies_to),
                    "valid_from": i.valid_from.isoformat(),
                    "valid_to": i.valid_to.isoformat() if i.valid_to else None,
                }
                for i in INDICATORS
            ])
            return 0

        from .govpipeline import run_indicators

        summary = run_indicators(only=args.only, on_date=args.on_date)
        _emit(summary)
        # Every indicator skipping means there was no data to assess. Reporting that as
        # success is how a broken pipeline goes unnoticed.
        if summary["results"] and not any("findings" in r for r in summary["results"]):
            reasons = {
                r.get("skipped") or r.get("error") for r in summary["results"]
            }
            print(
                f"ERROR: no indicator produced results. Reasons: {sorted(reasons)}",
                file=sys.stderr,
            )
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
