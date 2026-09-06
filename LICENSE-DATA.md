# Licence for published data

The **code** in this repository is MIT licensed (see `LICENSE`).

The **datasets** this project publishes — the OCDS releases, the Parquet files and
the derived benchmark aggregates — are licensed
**[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**.

You may copy, redistribute, remix and build upon them, including commercially,
provided you give appropriate credit:

> Sursa: achizitii-deschise (github.com/CristianNichifor/achizitii-deschise),
> date primare din SEAP / e-licitatie.ro și data.gov.ro, Licența pentru Guvernare Deschisă v1.0.

## Upstream sources and their terms

| Source | Terms | Used as |
|---|---|---|
| SEAP / e-licitatie.ro `api-pub` | Public data, Open Government Licence v1.0 | **Input** |
| data.gov.ro (ADR / ANAP quarterly exports) | Open Government Licence v1.0 | **Input** |
| ONRC / Ministerul Finanțelor | Public registry data | Input (enrichment, planned) |

## Sources deliberately NOT used as input

**OpenTender (opentender.eu/ro)**, published by the Government Transparency
Institute, is licensed **CC BY-NC-SA 4.0**. The NonCommercial and ShareAlike terms
are incompatible with publishing our own output under CC BY 4.0 — ingesting it
would make our dataset non-commercial and viral.

OpenTender is therefore used **only as an external validation reference** (comparing
our aggregates against theirs to detect systematic error). No OpenTender data enters
this pipeline. See `docs/sources.md`.

## Personal data

Upstream API responses contain personal data (names, e-mail addresses and phone
numbers of contract contact persons). These fields are stripped at the staging
boundary and never reach published artefacts. See `src/achizitii/gdpr.py` and
`METHODOLOGY.md`.
