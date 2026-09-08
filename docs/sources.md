# Data sources

Everything here was verified against the live services on 2026-09-06. Endpoints are
undocumented and can change; treat this file as evidence of what was true, not a contract.

## 1. SEAP `api-pub` (primary, live)

Base: `https://www.e-licitatie.ro/api-pub/`. Licence: **Open Government Licence v1.0**.

### The one access requirement

Requests without a `Referer` header are rejected:

```
403  {"message":"Access Denied: Referrer cannot be null."}
```

Adding `Referer: https://www.e-licitatie.ro/pub` returns `200`. No cookies, no tokens, no
JavaScript execution, **no browser automation**. This is the entire "session trap".

### Endpoints used

| Endpoint | Method | Purpose |
|---|---|---|
| `DirectAcquisitionCommon/GetDirectAcquisitionList/` | POST | Direct acquisitions by `finalizationDate` |
| `PublicDirectAcquisition/getView/{id}` | GET | Detail incl. `directAcquisitionItems[]` |
| `Entity/getCAEntityView/{id}` | GET | Authority profile incl. `county`, `city` |

### Known but not yet used

| Endpoint | Method | Purpose |
|---|---|---|
| `NoticeCommon/GetCANoticeList/` | POST | Tender notices |
| `C_PUBLIC_CANotice/get/{id}` | GET | Tender detail |
| `RFQ_PUBLIC_CANotice/get/{id}` | GET | Simplified-procedure detail |
| `C_PUBLIC_CANotice/GetCANoticeContracts` | POST | Winners per notice |
| `Entity/getSUEntityView/{id}` | GET | Supplier profile |

Historical archive 2007–2018 is served from `http://istoric.e-licitatie.ro` on the same paths.

### Gotchas

- The list endpoint reports `total` capped at **2000**. Page until a short page returns
  rather than trusting `total`, and keep date windows narrow.
- The detail response has **no** buyer/supplier name strings — only IDs. Names come from
  the list response. See `docs/ocds-mapping.md`.
- Responses contain personal data (`assignedUserEmail`, and `email`/`phone`/`fax` on the
  entity endpoints). Stripped by `src/achizitii/gdpr.py`.
- `itemMeasureUnit` is free text typed by the buyer.

## 2. data.gov.ro bulk exports (primary, historical)

> **data.gov.ro is unreachable from GitHub-hosted runners.**
>
> Measured 2026-09-06: four attempts from `ubuntu-latest`, each a 60-second connect
> timeout, ~16 minutes total, zero bytes transferred — while the identical request from
> a residential connection returned `200` in 0.93s. The host appears not to accept
> connections from GitHub/Azure IP ranges.
>
> Consequences, all deliberate:
> - `bulk.yml` has **no scheduled trigger**. A weekly red build nobody can fix trains
>   people to ignore CI.
> - It takes a `runner` input so it can be pointed at a self-hosted runner with a route
>   to data.gov.ro.
> - The bulk pipeline is a **local or self-hosted step**. The long-term fix is to ingest
>   where the host is reachable and publish the derived Parquet for CI to consume.
>
> The SEAP `api-pub` endpoints have no such restriction and work fine from Actions —
> `daily.yml` runs there successfully.

Publisher: **Autoritatea pentru Digitalizarea României**. Licence: **OGL v1.0**.
Dataset slugs `achizitii-publice-{YYYY}`, covering **2016 → 2026 T2**, quarterly.

```bash
curl -s "https://data.gov.ro/api/3/action/package_show?id=achizitii-publice-2026" \
 | jq -r '.result.resources[] | "\(.format)\t\(.name)\t\(.url)"'
```

Seven tables per quarter: achiziții directe · notificări de atribuire la cumpărarea
directă · anunțuri de inițiere · invitații SAD · contracte · anunțuri de atribuire la
proceduri fără anunț de inițiere · **date din modificare contract**.

Formats drift between years (CSV, XLS, XLSX, and some `.ods` files labelled `.xlsx`), so
the ingest must sniff rather than trust the declared format. This is the Stage 1 backfill
path and the only source for contract amendments.

### Dataset slugs drift too — including a typo

**2019 is published under a misspelled slug: `achiziti-publice-2019`**, one `i` short of
every other year. A single `achizitii-publice-{year}` template silently loses the entire
year — no error, just an absent year. `DATASET_SLUGS` therefore tries fallbacks.

### Resource names drift constantly

The initiation-notice table alone has been published as:

| Years | Name |
|---|---|
| 2016–2019 | `Anunturi participare`, `Anunturi initiere` |
| 2020–2021 | `Anunțuri inițiere` (no "de") |
| 2022–2026 | `Anunturi de initiere publicate`, `... publicate in SEAP` |

Matching only `anunturi de initiere` lost this table for four years — and it is the one
the estimate-versus-award comparison needs. A regex that stops matching does not raise;
the table just disappears.

Run `achizitii gov --years 2016-2026 --check-coverage` to see what each year matches,
without downloading anything. Verified coverage as of 2026-09-06:

| Table | Available |
|---|---|
| `achizitii_directe` | 2016–2026 |
| `contracte` | 2016–2026 |
| `initiere` | 2016–2026 |
| `fara_anunt` | **2023–2026 only** — the report type was introduced then |
| `modificari` | **2021–2026 only** — likewise |

The last two are genuine absences, not matching failures. Indicators depending on them
are therefore limited to those windows.

Deliberately unclassified: `Contracte subsecvente` (framework call-offs),
`Invitatii participare` / `Invitatii de depunere SAD` (invitations to an existing dynamic
purchasing system), and `Notificari de atribuire la cumpararea directa` (the award side
of direct purchases — a candidate future table).

## 3. OpenTender — reference only, NOT ingested

`https://opentender.eu/ro`, published by the Government Transparency Institute; the only
Romania entry in the [OCP Data Registry](https://data.open-contracting.org/en/publication/75).
OCID prefix `ocds-70d2nz`. 2007–2024: 448,018 tenders, 1,006,432 awards, 1,111,258 parties.

**Licence: CC BY-NC-SA 4.0.** NonCommercial + ShareAlike are incompatible with publishing
our output under CC BY 4.0, so **no OpenTender data enters this pipeline**. It is used
only to sanity-check our aggregates.

It is also TED-derived and therefore skewed above-threshold, with `Planning`, `Contracts`,
`Transactions`, `Milestones` and `Amendments` **empty** — which is precisely the gap this
project addresses.

## 4. Planned

- **ONRC / Ministerul Finanțelor** — supplier enrichment. Note the empirically-established
  caveat from SICAP.ai's work: CUI is *not* unique in ONRC (~3.97M distinct CUIs across
  4.17M registration records), so a naive join silently duplicates rows.
- **INS CPI** — deflation to a constant base year.
- ~~**ANI / PREVENT**~~ — spiked 2026-09-08 and rejected; see the section below.
- **CPV vocabulary (RO labels)** — from EU vocabularies, for the alias table.

## Prior art

- [SICAP.ai](https://github.com/ciocan/SICAP.ai) — MIT, actively maintained search engine.
- [sicap-parser](https://github.com/upbeside/sicap-parser) — original `api-pub` documentation.
- [sicap-explorer](https://github.com/ciocan/sicap-explorer) — CC0; ships a torrent of an
  Elasticsearch snapshot: 22,101,610 direct acquisitions + 470,811 tenders, 2007 → July 2020.
  A possible fast backfill, though the torrent is from 2021 and seeders are unverified.


## RUTI — Registrul Unic al Transparenței Intereselor

`https://ruti.gov.ro/api/registry/meetings`

Meetings that ministers, secretaries of state, deputies and senators publish in advance
with interest groups — Romania's counterpart to the EU Transparency Register.

| | |
|---|---|
| Access | Open JSON, no key. `robots.txt` is `User-agent: * / Disallow:` |
| Size | **666 meetings**, 2025-10-13 → 2026-09-09 |
| Cost to mirror | **7 requests** at `per_page=100` |
| Conclusions published | **97 of 666 (15%)** — publishing that a meeting happened is required; saying what was discussed is not |
| Institutions / decision-makers | 35 / 92 |
| Company identifier | **none** — the third party appears only in free-text `name` and `description` |

**Published as its own table; joined to nothing.** A supplier↔meeting indicator would be
fuzzy string matching against 751,505 supplier names, over institutions that make up
1.70% of our archive (452,922 of 26,672,772 acquisitions), across 666 records. It would
fire almost never and would name real individuals on the strength of a string match. A
meeting is lawful and the register exists to make it visible — its presence in this
dataset says nothing about any contract. A test enforces the separation.


## ANI / PREVENT — reference only, NOT ingested

The National Integrity Agency runs the one system in Romania built precisely for the
inference this project keeps being asked for: **PREVENT** cross-references integrity
forms filed during SEAP bidding against declarations of assets and interests, to catch
conflicts of interest before a contract is awarded. Legal basis: **Legea 184/2016**.

Spiked 2026-09-08 against the plan's own decision gate — *is there a structured
identifier that joins to `furnizor_cui` / `autoritate_cui`?* The answer is no, on three
independent counts.

| | |
|---|---|
| PREVENT warnings, case level | **not published.** Only aggregate quarterly and annual results |
| PREVENT warnings, total | **212** over 2017-06-20 → 2024-09-30, 9.9 mld RON |
| ANI on data.gov.ro | **absent** — not one of the 181 organisations; `declaratii avere` returns 0 datasets |
| Declarations portal | `declaratii.integritate.eu`, an Angular/form.io app over a Spring backend behind Cloudflare. `/robots.txt` returns the app shell, so there is no stated crawl policy |
| Subject of a declaration | **a person**, not a company |

**The join does not exist.** A declaration names an official and the companies where they
or their relatives hold a role. Connecting that to procurement means person → company
name → one of our **161,168** supplier identities, by fuzzy string match — then publishing
the result as a financial connection involving a **named private individual**. That is the
same shape rejected for [RUTI](#ruti--registrul-unic-al-transparenței-intereselor) and
strictly worse: RUTI at least identifies an institution, and a meeting is openly lawful.

Access was not the blocker and should not be recorded as one. The backend answers a plain
request — `/api` returns an ordinary Spring 404, not a challenge — and an early reading of
`challenges.cloudflare.com` in the bundle as an anti-automation gate was wrong; form.io
ships Turnstile as a stock component. **The reason not to build this is the inference, not
the access.**

**Worth keeping for scale, though.** PREVENT — purpose-built, with subpoena-grade inputs
this project cannot see — issues about **30 warnings a year**. `ofertant-unic-01` alone
flags 36,989 rows and `prag-01` 18,658. That is not a contradiction and not a claim to be
doing PREVENT's job better: PREVENT answers *"is this specific award a conflict of
interest?"* with data on people, and answers it authoritatively. This project answers
*"which awards are worth a look?"* over a far larger surface, with data anyone can check.
Where PREVENT's aggregates are useful is as an external sanity check on the order of
magnitude of what a state body considers actionable.

Revisit if ANI ever publishes case-level warnings carrying a tender identifier or a CUI.
That single change would make `conflict-01` buildable with a real legal basis to cite
(Legea 98/2016 art. 58-63) and no fuzzy matching at all.


## ANAF — company facts (free, no key)

`https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva`

POST up to **100 fiscal codes** per request. A real batch of 100 answered in 0.8s.

| | |
|---|---|
| Suppliers matched | **165,494 of 166,704 (99.3%)** |
| With county | **165,487 (100%)** — the archive is missing supplier locality on 77% of rows |
| Fiscally inactive | 11,207 |
| Struck off | 1,858 |
| Full pass | ~1,667 requests, ~30 min at 1 req/s |

**Status is point-in-time.** ANAF evaluates `statusInactivi` relative to the date you
ask about — the same company reads inactive today and active as of 2022. The stored
dates are absolute, so an award is judged against `data_inactivare` /
`data_reactivare` / `data_radiere` versus the award date, never against today's flag.

**Not available anywhere open: ownership.** ONRC sells it, and `beneficiari reali`
returns zero datasets on data.gov.ro — public access to beneficial-ownership registers
was restricted EU-wide after the 2022 CJEU ruling.
