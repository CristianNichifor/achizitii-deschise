# Native Civic UI Adoption

The site vendors the public MIT-licensed Civic UI CSS-only **0.4.0** release.
`site/vendor/civic-ui/provenance.json` records its URL, archive SHA-256 and exact
file hashes. The eight upstream files are unmodified, with license retained.
The archive's original `NATIVE.md` describes release preparation; the versioned
artifact recorded in provenance has since been published. No npm account, React,
component JavaScript or additional browser network dependency is required.

`site/civic-host.css` maps shared tokens to the existing light/dark procurement
colors and retains compact spacing, native-arrow clearance and responsive layout.
Neither the neutral nor USR theme is loaded. Native selects remain browser controls.

## Scope

- Five native selects and two inputs composed in field wrappers.
- Search, CSV export, share-link and both pagination buttons.
- Existing loading indicator and status announcements; pagination live label.
- Table-scroll wrapper styling and the existing conditional keyboard focus behavior.

Tabs, entity/indicator navigation, sortable headers, charts, disclosures and the
mobile table-card renderer remain host-owned. The shared table component is not
applied: mobile tables have a bespoke stacked presentation and data labels. Their
existing hidden header behavior is not a claim of full screen-reader certification.
Native field controls now have explicit accessible names. No data, SQL generation,
DuckDB, CDN, CSP, source ingestion, export serialization or URL codec is replaced.

## Checks

`npm run test:browser` runs the real DuckDB baseline plus summary/CPV pagination
round trips in light/dark mode at 390px and 1440px, and control/focus/keyboard-scroll
checks with a 320px layout check. Chromium, Firefox and WebKit run without retries.
Screenshots are CI artifacts, not committed content. Python tests verify the exact
vendored release files alongside the existing source/domain checks.

The migration was also compared to local pre-change snapshots from main `6258744`:
rendered cell text, SQL, filter choices and URL state on summary and two CPV pages.
Historical snapshots contain only existing public data and remain local. To repeat
that comparison, run `tests/browser/parity.spec.js` on the old version with
`CIVIC_RECORD_BASELINE=/absolute/temporary/directory`, then the new version with
`CIVIC_PARITY_BASELINE` pointing at that directory. CI's routine tests check the
pagination round trip; they do not pretend to carry a committed historical dataset.

The application still needs its original DuckDB CDN and data availability on a
cold load. Vendored CSS works locally but does not make the whole site offline.
