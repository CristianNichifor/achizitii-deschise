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
- Prices deflated to a common base year using INS CPI *(planned, Stage 1 — not yet
  applied; current figures are nominal)*.

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

All references are to Legea 98/2016.

### Deterministic by design

Every indicator is arithmetic over public fields. No language model is involved and none
is needed. This is not a limitation — it is what makes a finding checkable by the
authority it names, and what allows the exact query to be published alongside the result.

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

### Not yet implemented

- **Single-bidder rate** — the strongest indicator in the literature. **Not computable
  from the bulk exports**, which carry no bidder counts. It requires
  `GetCANoticeContracts` from the API, per notice.
- **Corruption Risk Index** composite scoring (Government Transparency Institute). The
  intent remains to adopt a published methodology rather than invent a score, but the
  missing bidder data blocks a faithful implementation today.
- **Brand-without-"sau echivalent"** in technical specifications (art. 156). Deterministic
  — a curated brand gazetteer plus one regex — but it needs the tender documents, which
  are Stage 4.
- **Ownership links** between suppliers and officials. Requires ONRC.

## Corrections

Errors are expected. If a record here misrepresents a real purchase, open an issue using
the correction template. Corrections are applied to the pipeline (so they persist through
rebuilds), logged in `CHANGELOG.md`, and the affected release is re-tagged.

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
- **No CPI deflation yet.** Cross-year comparisons are nominal and therefore overstate
  recent increases.
- **CPV is coarse.** One CPV code spans a €300 netbook and a €4,000 workstation, so
  CPV alone does not make two items equivalent. CPV plus unit plus description matching
  is required; the description layer is Stage 1.
- **County is the authority's registered county**, not necessarily the delivery location.
- **VAT treatment is not yet established** in the source data and may vary between
  records.
