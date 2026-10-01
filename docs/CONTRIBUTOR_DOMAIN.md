# Contributor domain map

## Mapping and comparability (`src/achizitii/`, `data/`)

Read [METHODOLOGY.md](../METHODOLOGY.md) before changing a published statistic.
`ocds.py` maps source detail to OCDS; preserve `ocid`, source URL, parser version,
organization identifiers and CPV codes. `itemClosingPrice` is already a unit price:
do not divide it by quantity. Unknown units and unquantified bundles remain
incomparable with a reason, never guessed into a benchmark. Unit aliases live in
`data/um_map.yml`; see [OCDS mapping](ocds-mapping.md).

Indicator thresholds depend on date and procurement category. Findings describe
patterns requiring review, never accusations. Aggregates need denominators and
small-group suppression; framework ceilings must not become expenditure totals.
Update methodology and indicator documentation alongside behavioral changes.

## Fixtures and storage (`tests/`, `site/data/`, `state/`)

`test_ocds.py` contains a SEAP-shaped detail fixture; `test_govdata.py` builds tiny
bulk-format samples. `test_price_archive.py` creates three synthetic line items in
temporary Parquet and tests archive and rebuild behavior. The offline entrypoint
runs these plus normalization and indicator tests, with no collection or downloads.

Raw/staging Parquet and derived aggregate bundles are generated, not source edits.
The committed daily `site/data/preturi/` archive is append-only and cannot be
reconstructed from bulk exports: never rewrite historical days for a fixture test.
`state/` is a resume ledger, not disposable cache. Derived bundles ship as releases;
see [storage](storage.md). Frontend source lives in `site/`, vendored libraries must
remain untouched, and release bundles are not a prerequisite for core fixture work.

## Collection and publication (workflows, `scripts/`)

Preserve the SEAP collection hold and rate limits; see [source notes](sources.md)
and [hold decision](adr-revenire.md). Do not contact SEAP, run bulk ingest, refresh
production state or publish merely to verify a code change. Fork PR tests consume
public released aggregates without publishing credentials. Agents never merge or
deploy, even with administrator credentials.
