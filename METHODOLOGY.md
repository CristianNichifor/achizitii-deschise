# Methodology

Version: **0.1.0** — every published figure is stamped with the methodology version that
produced it. Changes to this document are versioned like code.

## Principles

1. **Observation, not accusation.** This project reports prices and statistical position.
   It does not allege wrongdoing. A high price has many innocent explanations: urgency,
   small volume, remote delivery, different specification, bundled services.
2. **No number without its denominator.** Any aggregate is published with `n`. Groups
   below the minimum sample size are suppressed, not shown as a point estimate.
3. **Incomparable is better than wrong.** Where the data cannot support a comparison, the
   row is flagged and excluded rather than coerced into a number.
4. **Everything traces back.** Every row carries its `ocid`, source URL and
   `parse_version`. Every figure is one click from the original record.

## Unit price

For SEAP direct acquisitions the unit price is `itemClosingPrice`, taken directly.

This was verified rather than assumed. Across sampled records the identity

```
closingValue == Σ (itemClosingPrice × itemQuantity)
```

held for 25/25 records within 2% tolerance, and multi-quantity items are only sensible
under the unit-price reading (a Lenovo V15 laptop at quantity 15 shows 3637.0 RON;
divided by 15 it would be 242 RON, which is not a laptop).

We **never** derive a unit price by dividing a total by a quantity when the total is the
only figure available — that silently produces a bundle-average and is a common source of
error in procurement analysis.

## Comparability rules

A line item is marked `comparabil = false`, with `motiv_necomparabil`, when:

| Reason | Meaning |
|---|---|
| `pret_unitar_lipsa_sau_nepozitiv` | No unit price, or ≤ 0 |
| `cantitate_lipsa_sau_nepozitiva` | No quantity, or ≤ 0 |
| `unitate_nerecunoscuta` | `itemMeasureUnit` not in `data/um_map.yml`. **Never guessed.** |
| `unitate_de_tip_pachet_fara_marime_cunoscuta` | Bundle unit (`set`, `pachet`, `cutie`, `lot`, `serviciu`) with no recoverable pack size |
| `descriere_de_tip_pachet_fara_marime_cunoscuta` | Declared unit is a count, but the description names a bundle |

The last rule exists because buyers routinely book a bundle under a count unit. A real
record reads `PACHET ALIMENTAR`, quantity 1, unit `bucata`, 102.96 RON — the unit field
claims a piece, while the description says it is a package of assorted food. Trusting the
unit field alone under-reports bundles, so the description is checked as well.

## Units are UN/CEFACT codes

Each canonical unit carries its **UN/CEFACT Recommendation 20** common code (`buc` →
`H87` "piece", `mp` → `MTK` "square metre"), emitted as `unit.scheme: "UNCEFACT"` and
`unit.id` in OCDS. Unrecognised units carry **no** code — an absent `unit.id` is honest,
a guessed one is not. Codes were validated against the codelist published by ProZorro
(`ProzorroUKR/standards`, Apache-2.0); see `docs/prior-art.md`.

The bundle rule is the most consequential. "1 set" describes an unknown quantity of
goods. Comparing one buyer's set against another's is meaningless, and because bundles
are common this rule removes a large share of rows from benchmarks. That is the correct
outcome, not a defect.

Unrecognised units are a **backlog signal**: each one is a missing entry in
`data/um_map.yml`, and adding it makes more data comparable. Contributions welcome.

## Aggregation

For a benchmark over (CPV × unit × county × period):

- Only `comparabil = true` rows.
- Outliers trimmed at the 1st and 99th percentiles of the group before statistics.
- **Median, not mean.** Procurement prices are heavily right-skewed.
- Dispersion reported as the p90/p10 ratio.
- Minimum `n = 5` to publish a group; below that, suppressed.
- Published figures are **nominal**, with the deflator published alongside them so any
  year can be expressed in another year's money. See "Comparing money across years".

## Risk indicators

Implemented in `src/achizitii/indicators.py`, over the data.gov.ro bulk exports.

Every indicator declares four things, and will not run without them:

- **`legal_basis`** — the provision the pattern relates to. A finding without one is not
  defensible and should not be published.
- **`rationale`** — what behaviour the rule is designed to surface.
- **`applies_to`** — the scope. A rule never fires outside it.
- **`valid_from` / `valid_to`** — thresholds and rules change over time. Applying the
  2023 ceiling to 2018 data would manufacture findings, so a rule outside its validity
  window is skipped, loudly, rather than run.

| ID | Pattern | Legal basis |
|---|---|---|
| `prag-01` | Values clustering immediately below the direct-acquisition ceiling | art. 7 |
| `fara-competitie-01` | Awards with no prior notice published | art. 104 |
| `divizare-01` | Repeat purchases, same object and supplier, short window, cumulative value above the ceiling | art. 11 |
| `dependenta-01` | One supplier taking a dominant share of an authority's budget | art. 2 |
| `modificare-01` | Contract value increased after signature | art. 221 |
| `ofertant-unic-01` | Exactly one offer received | art. 2 |
| `estimare-01` | Awarded value far above the authority's own estimate | art. 2 and art. 9 |

All references are to Legea 98/2016.

### Deterministic by design

Every indicator is arithmetic over public fields. No language model is involved and none
is needed. This is not a limitation — it is what makes a finding checkable by the
authority it names, and what allows the exact query to be published alongside the result.

### The ceiling is detected from the data, not taken on trust

Establishing the direct-acquisition ceiling from the legal text alone proved unreliable.
Freely available consolidations of Legea 98/2016 disagree: the originally published form
gives 132,519 lei for goods/services and 441,730 for works; secondary sources quote
270,120 and variously 900,000 **or** 900,400; dated consolidations sit behind paywalls.
A wrong ceiling does not weaken the indicator — it *invents findings*.

So the operative ceiling is recovered from the distribution itself. Exceeding it is
unlawful, so the density of direct-acquisition values collapses at it. `detect_ceiling_sql`
scans candidate cutoffs and scores each by the ratio of mass in the 20,000 lei below to
the 20,000 lei above, requiring at least 100 acquisitions below and a ratio of 8:1.

Measured on H1 2026 goods/services:

| Year | Cliff at | Max observed | Below | Above | Ratio |
|---|---|---|---|---|---|
| 2026 | 274,000 | 273,036.00 | 1,755 | 37 | **47.4 : 1** |

**Open discrepancy, not yet resolved.** The declared ceiling is 270,120, but 37 records
sit above it and the largest is 273,036. Possible explanations — a ceiling raised for
2026, works contracts misclassified as goods/services, VAT or rounding treatment, or
genuinely unlawful records — have **not** been distinguished. Until they are, the
detected value is reported alongside the declared one and neither is presented as
settled. `prag-01` continues to use the declared legal figure, because that is the one
with a legal basis behind it.

Detection returns nothing when no sharp cliff exists, which is the correct answer for a
year whose ceiling cannot be established.

### Thresholds are dated and verified

The direct-acquisition ceiling is held as a dated schedule, not a constant. The
2023-01-01 value (270,120 RON for goods/services) is confirmed empirically: in the Q1
2026 export, direct acquisitions stop dead at that figure — 8 records above it across
the whole 270,120–280,000 range, against 377 in the 1,120 RON immediately below, and
236 priced at exactly 270,000.

**Earlier ceilings are not yet verified against the legal text, so the rule does not run
on those years.** A wrong threshold invents findings; refusing to run is the correct
failure mode.

### Interpretation limits

Each indicator has innocent explanations, and the output must never imply otherwise:

- Bunching below a ceiling can reflect a genuine internal budget limit.
- Awards without a notice are lawful in the circumstances art. 104 sets out.
- Repeat same-day purchases may be distinct lots of one real programme.
- A 90% supplier share may mean one qualified firm in a small local market.
- Contract increases are lawful within the limits of art. 221, and may reflect indexation.

The correct reading of any finding is **"this warrants review"**, never "this is fraud".

### Correction: single-bidder rate IS computable, for part of the archive

An earlier version of this document stated that single-bidder rate was not computable
from the bulk exports. **That was wrong.** The 2016–2018 exports carry
`NumarOfertePrimite` — the number of offers received — on every contract row. It was
dropped from later exports, so `ofertant-unic-01` is limited to the years that publish
it, expressed as a data requirement rather than a hardcoded window so the rule resumes
automatically if the column returns.

Measured on 2016–2017: 28,646 contracts received exactly one offer, against 24,034 with
two and 25,386 with three.

### Correction: the 2022 estimate gap was ours, not the publisher's

An earlier version recorded that contracts stopped carrying their own estimate part-way
through 2022, and reasoned about why the publisher might have changed practice mid-year.
**That was wrong.** One quarter of 2022 heads the column `VALOARE_ESTIMATA_RON` where the
rest of the year writes `Valoare estimata`, and the alias was missing — so the column
read as 40.6% null and the explanation was invented to fit an artefact of our own
ingest. With the alias added, 2022 is 99.7% populated and `estimare-01` rises from 24,543
findings to 42,631.

The column genuinely does disappear from 2023 onwards. The lesson kept here is that a
plausible story about the source is not evidence: the ingest was never checked against
the file's actual headers before the explanation was written down.

### Not yet implemented
- **Corruption Risk Index** composite scoring (Government Transparency Institute). The
  intent remains to adopt a published methodology rather than invent a score, but the
  missing bidder data blocks a faithful implementation today.
- **Brand-without-"sau echivalent"** in technical specifications (art. 156). The
  gazetteer and matcher are built and tested, but the indicator is **deliberately not
  shipped**, for reasons stronger than "the documents are Stage 4". See below.
- **Ownership links** between suppliers and officials. Requires ONRC.

### Why there is no brand indicator (a measured negative result)

`data/branduri.yml` and `achizitii.produs` were built to support an art. 156 check:
a technical specification that names a make without "sau echivalent" restricts
competition unlawfully. Running the matcher over the direct-acquisition archive before
writing the indicator showed it should not be published at all.

Measured on a 300,000-row sample of `denumire`:

| Measure | Result |
|---|---|
| Descriptions naming at least one brand | 6.2% — about **1.66 million** rows archive-wide |
| ...that are consumables, parts or repairs | **44.7%** |
| ...that say "sau echivalent" | **0.04%** (7 rows) |

Each number kills a different assumption.

**The qualifier is absent because the document is absent.** "Sau echivalent" appears in
0.04% of brand-naming descriptions — not because buyers omit it, but because a direct
acquisition has no *documentație de atribuire* to omit it from. `denumire` averages 89
characters; it is an object title, not a technical specification. So
`brand_without_equivalent` would return the same rows as `find_brands`, and the
indicator would reduce to "mentions a brand" while being *presented* as a legal finding.

**Naming the make is frequently the lawful option.** Nearly half the hits are toner for
an existing printer, a part for a specific vehicle, or a service on a named machine —
"Cartus toner Canon CRG-737", "Reparatie Ford Focus". Art. 156(2) permits identifying a
make where the object cannot otherwise be described precisely. Flagging these would
invert the law it claims to enforce.

**The scale is the accusation.** 1.66 million rows, each naming a real contracting
authority and a real supplier, is not an indicator — it is an unreviewable list. It
would also swamp the six indicators that *are* defensible.

The matcher is kept, because the gazetteer's other purpose stands: brand is the
strongest available signal for splitting a CPV code into price-comparable groups. If
tender documents are ingested at Stage 4, the check becomes meaningful — there the
qualifier is genuinely expected, and its absence genuinely means something.

The general rule this project takes from it: **an indicator must be tested against the
data before its legal basis is written down.** A provision that reads perfectly can
still have no computable counterpart in the available fields.

### A populated column is not a meaningful one

Every data defect found in this project until now was an *absence*: an unmatched alias
gives a column of NULLs, a renamed resource loses a year. `validate` was built around
that shape, comparing each column's null rate against its own history.

It cannot see a column that is full of the wrong thing.

The exports before 2021 wrote the internal `CPV_CODE_ID` into the CPV **name** column,
so `39831240` read `15113` instead of *Produse de curatenie*. That is **14,074,968 rows,
56% of every labelled row in the archive**, and 100% of 2016 through 2020. It passed
every validation pass and reached the published site, where a quarter of the CPV table
showed a number where the product name belongs — anyone searching "detergent" for those
years found nothing.

Two things came out of it:

- **The archive repairs itself.** The same code carries a proper label from 2021
  onwards, so no external vocabulary is needed. 8,572 codes recover a name; 510 never
  do, and those are published NULL rather than filled with a guess.
- **`validate` now checks label columns for being meaningless, not merely absent.** The
  known-defect years are declared, so the check stays useful: a permanently failing
  validation teaches everyone to ignore it, and only a *new* year appearing is worth an
  alarm.

One detail is worth recording because it nearly halved the fix. Matching `^[0-9]+$`
finds 7.4M rows and misses every value written `11728.0` — precisely half the problem,
while looking like a fix that worked. The pattern must allow a decimal part.

### Ambiguous brand tokens

Brand matching is word-boundary exact, but several real brands are spelled like ordinary
Romanian words. Bare matching was right only 24.5% of the time for `man` and 43.7% for
`lg`:

| Token | Plausible | What it usually is instead |
|---|---|---|
| `man` | 24.5% | *manual* — "DET.MAN.20KG", "GAR.MAN.+SILD" |
| `lg` | 43.7% | *legume* — "LEUSTEAN RO. LG. C.I" |
| `braun` | 63.9% | the colour — "PROSOP ISABEL BRAUN 70X100CM" |
| `bucovina` | 68.4% | the region — "Centrul Cultural Bucovina" |
| `tesa` | 70.4% | the staff category *tehnic, economic, socio-administrativ* |
| `dorna` | 85.4% | the town — "Transport ... la Vatra Dorna si retur" |

These now count only when the description also names something the brand actually makes,
which removed 934 false matches from a 600,000-row sample. The trade is recall for
precision, which is the right direction when the output feeds price groups: a wrongly
attributed brand splits or merges groups that should not be.

### Comparing money across years

Romanian prices rose 62% between 2016 and 2025 (HICP 98.93 → 160.06), so comparing
those years without adjusting shows inflation as much as procurement.

The index is **published, not applied**. `site/data/deflator.parquet` carries one row per
year and the site multiplies at query time, which means published figures stay exactly as
published, the reader chooses the base year, and the arithmetic is visible in the query
panel rather than baked into a stored number.

Source: Eurostat `prc_hicp_aind`, Romania, annual average, 2015=100 — chosen over the
national CPI because it is an open documented API and methodologically consistent across
the whole series. Mixing HICP with the national index would create a step that looks like
inflation. The values are checked into `data/ipc.yml` rather than fetched at build time,
so a Eurostat revision arrives as a reviewable commit instead of silently changing every
figure on the site.

**2026 has no index and is not extrapolated.** Selecting a base year blanks 2026's
monetary columns rather than leaving them nominal among adjusted ones — mixing the two in
a single column would be worse than an obvious gap.

### Correction: an unscreened year is not an unbounded one

Screening values against the legal ceiling has now been wrong in both directions, and
both errors were mine.

First, a single ceiling was applied to every category, so works were judged against the
goods figure. That excluded 52,306 works acquisitions — 12% of 2019's works — as
"impossible" when they were very likely lawful.

Fixing that introduced the opposite error. Works have no established ceiling before 2022,
so those years became screened against **nothing**, and a 2016 record of
**543,595,445,218 RON** — a school asphalting job, 98% of that year's works total —
entered the published sum. The real figure is about 1.2 billion.

The bound now used: a value above the most permissive ceiling the law has **ever** set
for its category cannot be a lawful direct acquisition in any year. That constrains the
unscreened years without asserting a ceiling we cannot evidence — we are not claiming to
know the 2016 works ceiling, only that 543 billion exceeds every ceiling this law has
ever had. Archive-wide this excludes 6,027 rows (0.02%) carrying 30 trillion RON.

### Contract values, and why frameworks carry no total

A framework agreement (*acord-cadru*) publishes a **ceiling** — the maximum callable over
the agreement's life — not money spent. For a multi-supplier framework that ceiling is
repeated on **every supplier's row**: the four largest values in the archive are the same
177,930,419,250 RON, one authority, four pharmaceutical wholesalers, one agreement.

Summing framework rows therefore multiplies a single ceiling by the number of suppliers,
and then double-counts again against the call-offs placed under it. So `contracte_an`
publishes frameworks with a count and a median and **no total at all**. Money actually
committed is the call-offs (*contract subsecvent*) plus ordinary contracts.

| Nature | Rows | Published total |
|---|---|---|
| `plafon_acord_cadru` | 2,080,764 | none — a ceiling, repeated per supplier |
| `contract_subsecvent` | 1,171,118 | yes — called off under a framework |
| `contract` | 394,424 | yes — ordinary contract |
| `nedeterminat` | 954 | yes, flagged |

The classification tests for call-offs **before** frameworks, because a call-off row also
carries framework wording; the other order would file every call-off as a ceiling.

**Extremes are disclosed, not excluded.** Unlike a direct acquisition, a public contract
has no legal ceiling — that is what distinguishes it — so nothing here can be called
impossible on legal grounds. But 301 rows (0.076%) carry 40% of the ordinary-contract
total, and a reader given only a sum would effectively be reading those rows. Each row
therefore publishes the total, the total excluding values above 1 billion RON, and the
count of such values, side by side.

## Corrections

Errors are expected. The process is documented in full in
[`CORRECTIONS.md`](CORRECTIONS.md), in Romanian and English, with three issue templates:
a data correction, a right of reply for named entities, and a data-quality report for
systematic problems.

Corrections are applied **to the pipeline**, not to the published file, so they survive
regeneration. They are logged in `CHANGELOG.md` and the affected release is re-tagged.
Initial response within 5 working days, resolution within 30.

Contracting authorities and suppliers have a standing right of reply. A contested record
is annotated with the response; we do not remove accurate public data, and we do not
leave a disputed figure unannotated.

## Comparisons that are invalid

**Never aggregate across CPV codes.** The median unit price of every item measured in
`buc`, grouped by county, measures *what each county bought*, not *what it paid*. In a
sample run this produced a county median of 4994 RON against another's 1.80 RON — the
first had bought computers, the second bottled water. The figure is arithmetically
correct and completely meaningless.

Every comparison must therefore hold constant, at minimum: **CPV code + canonical unit +
pack size**. The web UI defaults to a specific CPV for this reason and warns when no CPV
filter is set.

Even within one CPV, equivalence is not guaranteed — `30213100` covers both a budget
netbook and a mobile workstation. Description-level matching (Stage 1) is required before
within-CPV differences can be attributed to price rather than specification.

## Known limitations

- **Stage 0 covers direct acquisitions only.** Open tenders keep line items inside the
  *caiet de sarcini*; those are not yet parsed.
- **2026 cannot be deflated.** Eurostat has not published the index for it, so 2026
  figures are nominal even when a base year is selected, and appear blank rather than
  unadjusted.
- **CPV is coarse.** One CPV code spans a €300 netbook and a €4,000 workstation, so
  CPV alone does not make two items equivalent. CPV plus unit plus description matching
  is required; the description layer is Stage 1.
- **County is the authority's registered county**, not necessarily the delivery location.
- **VAT treatment is not yet established** in the source data and may vary between
  records.
