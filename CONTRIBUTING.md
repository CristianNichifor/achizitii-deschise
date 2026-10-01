# Contributing

Use Python 3.11+ (CI uses 3.12). From the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/check_offline.py
.venv/bin/ruff check .
.venv/bin/python scripts/check_repo_size.py
```

The bounded entrypoint reuses existing inline SEAP detail records, in-memory bulk
CSV/spreadsheet fixtures, normalization cases, indicator boundary cases and a
three-row temporary Parquet price archive. It exercises mapping, comparability,
format detection, thresholds, append-only storage and aggregate rebuilding.
After dependency installation it needs no network, release bundle, SEAP access,
credentials or production data. Temporary outputs are created by pytest and do not
replace `site/data`. Extend these fixtures for bounded changes.

Full existing CI also validates the released aggregate bundle against the committed
price archive. That is a separate, network-dependent check, not the offline path:

```sh
./scripts/fetch_bundle.sh
.venv/bin/python -m pytest -q
```

The fetch uses the public upstream repository even on forks; no token is required.
`BUNDLE_REPOSITORY=owner/repo` explicitly selects another release source. It leaves
an existing local manifest untouched. Do not run a production ingest to satisfy tests.
Do not substitute a synthetic manifest for released-bundle acceptance assertions.

For UI changes install Node.js/npm, run `npm ci --ignore-scripts`, then
`npx playwright install --with-deps` and `npm run test:browser` after fetching the
bundle. See [browser setup](docs/BROWSER_BASELINE.md). CI reuses the existing browser
job once alongside lint, full pytest and the repository-size check. The aggregate
`verify` requires both jobs to succeed, including on fork PRs; skipped or cancelled
jobs fail. The offline subset does not claim released-data or browser coverage.

## Changes and review

Start from current `origin/dev`. Maintainers use `wt new <name> origin/dev` in
this repo, placing worktrees at `<repo>/.worktrees/<name>`; remove with `wt rm`
or `wt gc`. Without `wt`, use a separate standard clone and
`git switch -c <name> origin/dev`. Use `feat/`, `fix/`, `chore/`, `docs/`, `sec/`
or `adr/` branch prefixes. Never mix changes from another repository.

An issue or PR should state the observable problem, bounded scope, acceptance
criteria, affected domain invariants, and the commands/results that demonstrate
success. Add a small regression fixture for changed behavior, including provenance
for real source excerpts; disclose untested paths and data coverage limits.
Use Conventional Commits with an imperative lower-case subject, no trailing period,
and at most 72 characters. Link issues with `Refs: #N` or `Closes: #N` trailers.
Open PRs against `dev`; agents never merge or deploy. Keep the existing license.

No private handbook, 1Password, production credentials, or production collection is
needed for contributor verification. Publishing and collection are maintainer tasks,
not setup steps. Do not run deployment, ingest or release workflows for a code PR.

## The most useful contribution is not code

Extraction is solved — SEAP hands us structured line items. **Comparability** is the open
problem, and it is mostly a data-curation job that does not require Python.

### 1. Unit aliases (`data/um_map.yml`)

Every unit the pipeline does not recognise becomes an excluded row. Use an existing maintainer-provided sample to
look at excluded units (do not run an ingest while the SEAP hold is active):

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


### Ground rules

1. **Verify against recorded source evidence; do not assume field semantics.** The project's central
   fact — that `itemClosingPrice` is a unit price — was established by testing the
   invariant `closingValue == Σ(price × qty)`, not by reading a field name. Most records
   have quantity 1, so wrong assumptions hide easily. Add a test for anything you learn.
2. **Never guess a unit.** Unrecognised is a valid, useful answer.
3. **No personal data past `staging`.** If you add a source, extend `src/achizitii/gdpr.py`.
4. **Be a good guest.** SEAP is public infrastructure on an undocumented endpoint. Do not
   raise `MAX_RPS`, and keep a contactable User-Agent.
5. **Observation, not accusation.** Language in code, docs and UI describes prices and
   statistical position — never wrongdoing. See `METHODOLOGY.md`.

For shared-control changes, preserve the [native Civic UI contract](docs/CIVIC_UI.md).
