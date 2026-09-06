# Prior art and what we reuse

## ProZorro (Ukraine)

Ukraine's national e-procurement system, and the most mature OCDS implementation in the
region. Two GitHub orgs: [`openprocurement`](https://github.com/openprocurement) (older,
largely dormant) and [`ProzorroUKR`](https://github.com/ProzorroUKR) (active).

**Important distinction:** ProZorro is a *transaction system* — it runs the actual
tendering and auctions. We are a *read-only analytics layer* over a system someone else
runs. Most of the codebase (auctions, bid encryption, CDB, chronograph, document service)
solves problems we do not have. The value to us is in the **codelists and the
methodology**, not the application code.

### ✅ Adopted: UN/CEFACT unit codes

[`ProzorroUKR/standards`](https://github.com/ProzorroUKR/standards) — **Apache-2.0**,
actively maintained. `unit_codes/all.json` holds 2,091 UN/CEFACT Recommendation 20 common
codes; `unit_codes/recommended.json` is a curated subset.

This closed a real gap. `data/um_map.yml` previously mapped Romanian free text onto
canonical strings we invented (`buc`, `kg`, `ora`). Those strings meant nothing outside
this repository. Every canonical unit now also carries its **UN/CEFACT code**, which is
the scheme OCDS specifies for `item.unit.id`:

```json
"unit": {
  "name": "bucata",          // the buyer's original text, preserved
  "scheme": "UNCEFACT",
  "id": "H87",               // piece
  "value": { "amount": 102.96, "currency": "RON" }
}
```

Consequences: our releases are internationally comparable rather than Romania-specific;
a unit typo in `um_map.yml` is caught by a test; and the Ukrainian and Romanian datasets
become joinable on unit for cross-country price work.

We did **not** vendor the codelist — we use ~20 codes, and the codes themselves are a UN
standard rather than ProZorro's IP. ProZorro is credited in `data/um_map.yml` as the
source we validated against.

### 📖 Reference only: risk indicators

[`ProzorroUKR/prozorro-risks`](https://github.com/ProzorroUKR/prozorro-risks) —
**no licence file.** Absent a licence, default copyright applies: readable, **not
copyable**. No code or wording from it may enter this repository.

Its *architecture* is still instructive for our Stage 3, and architecture is not
copyrightable. Each indicator is a self-describing class carrying:

| Property | Why it matters |
|---|---|
| `identifier` (`sas-3-2`) | Stable ID, so a finding cites a versioned rule |
| `name`, `description` | Published in plain language |
| `legitimateness` | **The legal basis for the rule** |
| `development_basis` | Why the rule was written at all |
| `procurement_methods`, `tender_statuses`, `procurement_categories` | Explicit applicability — a rule never fires outside its scope |
| `start_date` / `end_date` | Rules are switched on and off in time as law changes |

Two lessons worth taking:

1. **Every indicator states its legal basis and its scope.** That is exactly the
   discipline `METHODOLOGY.md` needs, and it is what makes a finding defensible rather
   than an accusation. Our equivalent anchor is Legea 98/2016.
2. **Indicators are dated.** Procurement law changes; a rule valid in 2019 may be invalid
   in 2026. Applying today's rules to old data silently manufactures findings.

Their `sas-3-2` (buyer disqualified every bidder except the winner) is deterministic
counting — no model involved — which matches the position already taken in
`METHODOLOGY.md`.

### ↔️ Considered, not adopted

| Repo | Verdict |
|---|---|
| `openprocurement.api` | The CDB behind ProZorro. We do not run tenders; nothing to reuse. |
| `prozorro_crawler` | Crawler for ProZorro's own paginated feed. Our source has different pagination semantics (`total` capped at 2000). |
| `openprocurement-ocds-mapping`, `openprocurement.ocds.export` | Maps *their* schema to OCDS. Wrong source schema, and last touched 2019. |
| `ocdsapi` | Serving OCDS over an API. Relevant only if we outgrow static hosting — Stage 3+ at the earliest. |
| `prozorro-catalog` | Catalogue item/unit modelling. Worth revisiting: Romanian direct acquisitions also come from an electronic catalogue, so their profile/requirement modelling may inform description-level matching in Stage 1. |
| `standards/classifiers` | Ukrainian ДК 021:2015 is CPV-derived, so structure is familiar, but the Romanian CPV labels must come from the EU vocabularies. |

## Open Contracting Partnership

[`standard.open-contracting.org`](https://standard.open-contracting.org/) — the schema
itself, plus the Data Review Tool for validation. Stage 2 target: pass the validator
cleanly and register an OCID prefix.

## SICAP.ai

[`ciocan/SICAP.ai`](https://github.com/ciocan/SICAP.ai) — MIT, actively maintained,
Romanian. A search engine over the same source. Complementary rather than competing:
it answers *what was bought*, we answer *whether the price was reasonable*. Its
`docs/company-enrichment-brief.md` records a hard-won fact worth heeding before any
supplier join: **CUI is not unique in ONRC** (~3.97M distinct CUIs across 4.17M
registration records).
