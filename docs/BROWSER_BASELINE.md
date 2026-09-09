# Browser Adoption Baseline

Before a shared-control migration, `npm run test:browser` exercises the unchanged
static site in Chromium, Firefox and WebKit at 390px and 1440px. Node dependencies
are development-only: there is no new site build or runtime dependency.

The check loads the real DuckDB-Wasm module and worker from the site's existing CDN,
queries the committed public summary Parquet, filters by an available year, downloads
CSV and reloads the shared URL. It verifies table content survives the reload and
captures screenshots as CI artifacts. Data is not regenerated or uploaded.

```sh
npm ci --ignore-scripts
npx playwright install --with-deps
npm run test:browser
```

Python 3 is needed for the temporary local static server. CI uses the matching pinned
Playwright container. Test servers are stopped by Playwright.
Node 20 or newer is required for the development tooling.

## Limits

This is an online integration baseline, not a cold-offline guarantee: initial ESM,
worker and WebAssembly loading still needs the existing external CDN. CDN failure
fails the check rather than substituting a mock database. The local server serves
complete files; production byte-range behavior is not certified by this check.
Summary totals are not hardcoded because the published dataset changes. This does
not yet cover every entity drilldown, indicator or source ingestion pipeline, nor
replace the Python domain tests or a screen-reader audit.
