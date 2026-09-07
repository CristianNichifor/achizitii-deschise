# achizitii-deschise

**Date deschise despre achizițiile publice din România — prețuri unitare comparabile, în format OCDS.**

Open data on Romanian public procurement. This project publishes the **unit price of
individual line items** — what a single laptop, toner cartridge or hour of guard duty
actually cost — so that prices paid by different public buyers can be compared.

**👉 [cristiannichifor.github.io/achizitii-deschise](https://cristiannichifor.github.io/achizitii-deschise/)**
— 26,6 milioane de achiziții directe, interogabile direct în browser. Fără server:
DuckDB-Wasm citește fișiere Parquet statice.

> **Status: early.** The archive is complete for 2016–2026 and the risk indicators run,
> but figures have not been independently validated. Treat them as a starting point for
> checking a record against the source, not as a citable statistic.

## Why this exists

Romania publishes a great deal of procurement data, but almost all analysis stops at the
contract level: *who* bought *what*, for *how much in total*. The question that actually
matters — *was that a reasonable price?* — needs line items, and needs them normalised.

Two things make that possible and nobody has done it:

1. **The data is already structured.** SEAP's `PublicDirectAcquisition/getView` endpoint
   returns `directAcquisitionItems[]` with `itemQuantity`, `itemMeasureUnit` and
   `itemClosingPrice`. **No PDF parsing, no OCR, no LLM is required** for direct
   acquisitions — the high-volume, low-scrutiny spending where most waste occurs.
2. **The gap is real.** The only OCDS publication of Romanian data in the
   [OCP Data Registry](https://data.open-contracting.org/en/publication/75) is
   *OpenTender*, a third party, derived from TED and therefore skewed to
   above-threshold tenders — with Planning, Contracts, Transactions, Milestones and
   Amendments all empty. Romania committed to publishing OCDS
   ([OGP RO0046](https://www.opengovpartnership.org/members/romania/commitments/RO0046/))
   and has not done so at the below-threshold, item level.

## What it does

```
SEAP api-pub   ──►  raw/     immutable archive, personal data stripped
                    core/    OCDS 1.1 releases
                    marts/   normalised line items (Parquet)

data.gov.ro    ──►  gov/     11 years, 5 tables, 30M rows
                    findings/  risk indicators
                            └─►  site/data/  aggregates + findings, 24 MB
                                 └─►  GitHub Pages + DuckDB-Wasm, queried in the browser
```

The published bundle is aggregates, not raw rows: the archive is 1.5 GB and Pages caps a
site at 1 GB, but more to the point a browser has no use for 26.7M rows it must download
before answering anything. Build it with `achizitii publish`.

The whole pipeline runs in GitHub Actions and publishes static files. There is no
server and no running cost.

## The hard part is not extraction

Extraction is a JSON field access. **Comparability** is the engineering problem:

- `itemMeasureUnit` is free text. A single day contains `bucata`, `bucată`, `buc`,
  `Buc`, `BUC`, `PACHET`, `pachet servicii`, `Cutie`.
- Bundles are booked as quantity 1. `PACHET CARTUSE DE TONER, qty 1, 3853.72 RON` is
  not comparable with another buyer's differently-sized toner pack.
- Romanian text mixes ș/ț (comma below) with ş/ţ (cedilla).

So every line item carries an explicit `comparabil` flag and, when false, a
`motiv_necomparabil`. **Incomparable rows are kept but excluded from benchmarks.** A
silently wrong average is worse than a missing one — see `METHODOLOGY.md`.

## Verified facts this project relies on

Established empirically against the live API, not assumed:

| Fact | Evidence |
|---|---|
| `api-pub` needs only a `Referer` header — no cookies, no browser | Without it: `403 {"message":"Access Denied: Referrer cannot be null."}` |
| **`itemClosingPrice` is the UNIT price, not the line total** | Lenovo V15 laptop, qty 15 → 3637.0. Water, qty 384 → 1.75. Guard duty, qty 680 ore → 34.0 |
| `closingValue == Σ(itemClosingPrice × itemQuantity)` | 25/25 sampled records matched within 2% |
| County comes from `Entity/getCAEntityView/{id}` | Returns `county`, `city`, `fiscalNumber` |

## Two sources, two questions

They are not interchangeable, and this is the central design fact:

| Source | Carries | Answers |
|---|---|---|
| **SEAP `api-pub`** | quantity, unit, **unit price** | *Was the price reasonable?* |
| **data.gov.ro bulk** | every acquisition and contract, **no quantities** | *Were the rules followed? Who benefits?* |

Behavioural risk indicators need only the bulk export — four files per quarter, no
per-record API calls.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# --- line items and unit prices, from the live API ---
.venv/bin/achizitii seap --start 2026-09-01 --limit 25 --skip-raw   # smoke test
.venv/bin/achizitii seap --days-back 1                              # a full day

# --- bulk exports and risk indicators ---
.venv/bin/achizitii gov --years 2026            # one year
.venv/bin/achizitii gov --years 2016-2026       # full backfill
.venv/bin/achizitii indicators --list           # what each rule checks, and its legal basis
.venv/bin/achizitii indicators                  # run them, write findings to data/findings/

# --- the published site ---
.venv/bin/achizitii publish                     # build site/data/ (aggregates + findings)
python3 -m http.server -d site 8000             # then open http://localhost:8000
```

## Risk indicators

Eight deterministic indicators over the bulk data. Each declares its **legal basis**, the
scope it applies to, and the dates between which it is valid — because thresholds and
rules change, and applying today's threshold to 2018 data manufactures findings.

| ID | Checks | Legal basis |
|---|---|---|
| `prag-01` | Values clustering just under the direct-acquisition ceiling | Legea 98/2016 art. 7 |
| `fara-competitie-01` | Awards with no prior notice published | Legea 98/2016 art. 104 |
| `divizare-01` | Repeat buys, same object and supplier, short window | Legea 98/2016 art. 11 |
| `dependenta-01` | One supplier taking most of an authority's budget | Legea 98/2016 art. 2 |
| `modificare-01` | Contract value rising after signature | Legea 98/2016 art. 221 |
| `ofertant-unic-01` | Contracts that drew exactly one offer | Legea 98/2016 art. 2 |
| `estimare-01` | Award far above the authority's own estimate | Legea 98/2016 art. 2 & 9 |
| `incetare-01` | Supplier already struck off or in liquidation at the award date | Legea 98/2016 art. 167(1)(b) |

No model is involved in any of them. That is deliberate: a finding computed by
arithmetic over public fields can be checked by the authority it names.

**These report patterns, never conclusions.** Every output means "requires review".
Bunching at a threshold may be a real budget cap; a 91% supplier share may mean one
qualified local firm. See `METHODOLOGY.md`.

## Corectări și drept la replică

Indicatorii de mai sus numesc autorități contractante și operatori economici reali.
Oricine poate cere o corectare, iar entitățile numite au **drept la replică**: publicăm
poziția lor alături de înregistrarea contestată, needitată pe fond.

Nu ștergem date publice corecte pentru că sunt incomode — dar nici nu lăsăm o cifră
contestată fără mențiune.

👉 **[CORRECTIONS.md](CORRECTIONS.md)** — procesul complet, în română și engleză.
Răspuns inițial în 5 zile lucrătoare.

## Data sources and licence

| Source | Terms | Role |
|---|---|---|
| SEAP / e-licitatie.ro `api-pub` | Open Government Licence v1.0 | input |
| data.gov.ro quarterly exports (`achizitii-publice-{YYYY}`, 2016→2026) | Open Government Licence v1.0 | input (bulk history) |
| OpenTender | CC BY-**NC**-SA 4.0 | **validation reference only — never ingested** |

Code is **MIT**. Published data is **CC BY 4.0**. OpenTender's NonCommercial and
ShareAlike terms are incompatible with that, so its data is deliberately kept out of the
pipeline. See `LICENSE-DATA.md`.

Personal data (names, e-mails, phone numbers of contact persons) present in upstream
responses is stripped before anything is written to `core/`. See `src/achizitii/gdpr.py`.

## Roadmap

- [x] **Stage 0** — direct acquisitions → line items → normalisation → Parquet → browser
- [x] **Published site** — aggregates + findings on GitHub Pages, queried with DuckDB-Wasm
- [ ] **Stage 1** — full 2016→now backfill via data.gov.ro; CPV alias table; CPI deflation
- [ ] **Stage 2** — registered OCID prefix, OCDS validator-clean, tagged release + Zenodo DOI
- [ ] **Stage 3** — corruption risk indicators, methodology doc, right-of-reply process
- [ ] **Stage 4** — OCP Data Registry listing, co-maintainers, partnerships

## Related work

- [SICAP.ai](https://github.com/ciocan/SICAP.ai) — actively maintained search engine over
  the same source. Complementary, not competing: **SICAP.ai tells you what was bought;
  this project tells you whether the price was reasonable.**
- [sicap-parser](https://github.com/upbeside/sicap-parser) — original documentation of the
  `api-pub` endpoints.
- [sicap-explorer](https://github.com/ciocan/sicap-explorer) — 2007–2020 archive (torrent).

## Contributing

The most valuable contribution is **not code**: it is entries in `data/um_map.yml` and
`data/cpv_aliases.yml`. Every unit alias and CPV synonym added makes more of the dataset
comparable. See `CONTRIBUTING.md`.
