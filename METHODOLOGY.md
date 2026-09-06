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

*Not yet implemented (Stage 3).* When added, the intent is:

- Adopt the published **Corruption Risk Index** methodology (Government Transparency
  Institute) rather than inventing a proprietary score.
- Compute deterministically wherever possible. Submission windows, single-bidder rates,
  supplier concentration and award/estimate ratios are arithmetic, not inference, and
  must not be delegated to a language model.
- For technical specifications, the strongest Romanian indicator is deterministic: Legea
  98/2016 requires that where a specification names a make or source, it be accompanied
  by "sau echivalent". A brand gazetteer plus one regex is more precise and far more
  defensible than a model's judgement.

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
