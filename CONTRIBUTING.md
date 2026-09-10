# Contributing

For shared-control work, run the [real-browser adoption baseline](docs/BROWSER_BASELINE.md)
alongside the Python checks. It does not change the site's static deployment.
The [native Civic UI contract](docs/CIVIC_UI.md) records the vendored release,
host theme adapter and intentional migration boundaries.

## The most useful contribution is not code

Extraction is solved — SEAP hands us structured line items. **Comparability** is the open
problem, and it is mostly a data-curation job that does not require Python.

### 1. Unit aliases (`data/um_map.yml`)

Every unit the pipeline does not recognise becomes an excluded row. Run an ingest and
look at what fell out:

```sql
SELECT um_brut, count(*) n
FROM 'site/data/items/**/*.parquet'
WHERE motiv_necomparabil = 'unitate_nerecunoscuta'
GROUP BY 1 ORDER BY n DESC;
```

Add each real unit to the right canonical entry. Two rules:

- If it names a **definite** quantity (`role`, `flacon 500ml`), it is comparable.
- If it names an **indefinite bundle** (`set`, `pachet`, `lot`), it is `comparable: false`.
  Do not "fix" a bundle by marking it comparable — that is exactly the error the flag exists
  to prevent.

### 2. CPV aliases (`data/cpv_aliases.yml`)

Romanian buyers describe identical goods differently — *"bănci parc"* and *"mobilier odihnă
exterior"* are the same thing. Curated aliases beat any fuzzy-matching heuristic. Start
with high-value items; they matter most and are easiest to verify.

**Add synonyms, not hierarchy.** Measuring the archive first showed that most codes which
*look* related are not synonyms, and encoding them would corrupt a benchmark:

| Pattern | Example | Why not |
|---|---|---|
| Hierarchy | `03221400` Varză / `03221410` Varză albă | A kind of, not a name for — and CPV already encodes it in the code prefix |
| Catch-alls | `33690000` Diverse medicamente absorbing specific drug classes | Buyers reach for the general code; co-occurrence is not equivalence |
| Mis-filing | `30125100` toner / `35331500` cartridges | `35331500` is **ammunition** |

The case that genuinely needs you is the same object under *different words*, because
nothing automatic can connect phrases that share no tokens. Where the words already match
and only the code differs — 31.7% of specific-product rows — `preturi_produs` groups them
without curation.

Every group must say **how you checked**; a test rejects one that does not, and another
rejects a parent/child pair outright.

### 3. Corrections

If a record here misrepresents a real purchase, open an issue with the `ocid` and what is
wrong. Corrections are fixed **in the pipeline**, so they survive rebuilds.

## Code

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Smoke test against the live API (be polite — the rate limiter is not optional):

```bash
.venv/bin/achizitii --start 2026-08-20 --limit 25 --skip-raw
```

### Ground rules

1. **Verify against live data; do not assume field semantics.** The project's central
   fact — that `itemClosingPrice` is a unit price — was established by testing the
   invariant `closingValue == Σ(price × qty)`, not by reading a field name. Most records
   have quantity 1, so wrong assumptions hide easily. Add a test for anything you learn.
2. **Never guess a unit.** Unrecognised is a valid, useful answer.
3. **No personal data past `staging`.** If you add a source, extend `src/achizitii/gdpr.py`.
4. **Be a good guest.** SEAP is public infrastructure on an undocumented endpoint. Do not
   raise `MAX_RPS`, and keep a contactable User-Agent.
5. **Observation, not accusation.** Language in code, docs and UI describes prices and
   statistical position — never wrongdoing. See `METHODOLOGY.md`.
