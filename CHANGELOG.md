# Changelog

Corrections to published data are recorded here, as committed to in
[`CORRECTIONS.md`](CORRECTIONS.md).

A correction is applied to the pipeline rather than to the published file, so that it
survives regeneration; the entry below names the commit that fixed it.

## 2026-09-07 — geography, and an eighth indicator

- **Added** `incetare-01`: 713 awards to suppliers already struck off or in liquidation
  at the award date (Legea 98/2016 art. 167(1)(b)).
- **Added** supplier and authority profiles from ANAF — free, no key. 182,530 companies,
  county for 100% of them.
- **Added** `judete_an`: 97.9% of the archive placed geographically; 58.0% of awards go
  to a supplier in the buyer's own county.
- **Not built**, deliberately: fiscal inactivity and company age. Both are computable and
  neither is an exclusion ground under Legea 98/2016. See METHODOLOGY.md.

## 2026-09-07 — contracts published

- **Added** `contracte_an`: 3.6M contracts, split by nature. Framework agreements carry a
  count and a median but **no total** — the ceiling is repeated on every supplier's row,
  so summing multiplies it by the number of suppliers and double-counts the call-offs
  beneath it.
- **Added** extreme-value disclosure for contracts: 301 rows (0.076%) carry 40% of the
  ordinary-contract total, so the total, the total excluding values above 1 bn RON, and
  the count are published together.
- **Fixed**: 26 repeated header rows had been ingested as data, producing a contracting
  authority named "Autoritate contractanta" on the published site.

## 2026-09-07 — inflation adjustment, and a trillion-lei correction

- **Added**: figures can be expressed in any year's money. The Eurostat HICP index is
  published as its own table and applied at query time, so published numbers stay
  as-published. 2026 has no index and is not extrapolated.
- **Corrected**: years with no established ceiling were screened against nothing, so a
  2016 works record of 543,595,445,218 RON — 98% of that year's works total — was inside
  the published sum. 2016 works falls from 553.7bn to 1.22bn. Values above the highest
  ceiling ever set for a category are now excluded in every year: 6,027 rows carrying
  30 trillion RON.
- **Added**: the RUTI meetings register, published as its own table and joined to nothing.

## 2026-09-07 — CPV labels were numbers

- **Corrected**: the CPV name column carried the internal `CPV_CODE_ID` instead of a
  label for 2016–2020 — 14,074,968 rows, 56% of every labelled row. On the published
  site a quarter of the CPV table showed e.g. `15113` where *Produse de curatenie*
  belongs, so searching those years by product name returned nothing. Labels are now
  recovered from 2021+, where the same code carries a real name; 510 codes have none
  anywhere and are published empty rather than guessed.
- **Added**: `validate` checks label columns for being *meaningless*, not merely absent.
  Null-rate checks are blind to a column that is full of the wrong thing, which is how
  this survived every previous pass.

## 2026-09-07 — the site goes live

- **Published** at
  [cristiannichifor.github.io/achizitii-deschise](https://cristiannichifor.github.io/achizitii-deschise/):
  four aggregate tables and the seven indicator tables, 24 MB, queried in the browser
  with DuckDB-Wasm over static Parquet. No server, no running cost.
- **Corrected**: the ceiling used to screen implausible values is per year *and
  category*. Applying the goods ceiling to works excluded 52,306 works acquisitions —
  12% of 2019's works — as impossible when they were very likely lawful. Total
  exclusions fall from 84,381 to 32,142.
- **Corrected**: 2022 contract estimates were never missing. One quarter heads the
  column `VALOARE_ESTIMATA_RON`; the alias was absent, and an explanation about the
  publisher changing practice mid-year had been written to fit our own bug.
  `estimare-01` rises from 24,543 findings to 42,631.
- **Recovered** eight columns lost to typos in the published source (`Numar achizite`,
  `Dtaa contract`, `Tip inchiere contract`, and five more).
- **Decided not to ship** the art. 156 brand indicator. On acquisition titles the
  "sau echivalent" qualifier appears in 0.04% of brand-naming rows, because a title is
  not a technical specification — the check would flag ~1.66M lawful rows. See
  METHODOLOGY.md.

## Corrections to published data

*None yet — no data has been published.*

Once publication begins, each entry records: the date, who reported it, which records
were affected, what was wrong, what it was changed to, and the commit that fixed it.

## Data-integrity fixes before first publication

These predate publication, so no published figure was ever affected. They are listed
because they show what the pipeline has silently got wrong, and because
`METHODOLOGY.md` treats that history as relevant to how much confidence a reader should
place in current output.

| Fix | Effect |
|---|---|
| False xlsx declared dimension | 2019–2020 direct acquisitions read as empty; ~4.7M rows recovered |
| Only the first sheet was read | 2023 Q1 read 64,999 of 584,138 rows; `contracte` 2021–22 truncated |
| Title row taken as header | Whole of 2023 parsed to zero rows |
| Duplicate quarterly resources | 2023–24 quarters published twice; ~130k phantom rows removed |
| Misspelled dataset slug upstream | 2019 absent entirely (`achiziti-publice-2019`) |
| snake_case headers unmatched | 2021 lost four columns across 1,043,345 rows |
| Renamed initiation notices | `initiere` absent for 2017, 2018, 2020, 2021 |
| Framework agreements double-counted | Contract totals inflated; 95% of rows are `acord-cadru` |
