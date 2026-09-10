# Native HTML Contract

This is the narrow, tested CSS-only contract for non-React applications. It does
not implement dialogs, menus, tabs, pagination state, sorting, validation or data
loading. Those behaviors remain with the host. Keep complex controls on their
existing accessible implementation.

## Distribution

**Unreleased 0.3.0 preparation:** the next reviewed release can include `civic-ui-css-VERSION.tgz`
beside the React package and `SHA256SUMS`. Version 0.2.0 does not have this asset.
Do not use an unreleased URL or replace published assets. No npm account is needed.

The CSS archive contains only CSS, this document and the MIT license. Extract it
into a local vendor directory and retain the license. Review its checksum against
the same version's `SHA256SUMS` before committing the files and provenance. Import
`native.css` followed by a theme or host adapter; retain all relative CSS imports.

```html
<link rel="stylesheet" href="./vendor/civic-ui/native.css">
<link rel="stylesheet" href="./vendor/civic-ui/themes/neutral.css">
<main class="civic-scope civic-neutral">
  <!-- Host content -->
</main>
```

There is no JavaScript, React, Radix, Lucide, remote font or external runtime
network dependency in this archive. CSS uses nesting and `:has()` and is tested in the repository's
pinned Chromium, Firefox and WebKit engines, not legacy browsers. Vendored CSS
also loads over `file://`; the host's data, routing and other assets determine
whether the complete application works offline. This does not add a service worker.

## Controls

The scope supplies dimensions. A theme must supply all color tokens listed in
`themes/neutral.css`. Hosts may map them to their own existing light/dark tokens;
the neutral theme is optional and the USR adapter is opt-in. CSS does not set a
page layout, body font, dark-mode preference or host navigation.

```html
<div class="civic-field">
  <label for="region">Region</label>
  <select id="region" class="civic-select civic-select--native"
          aria-describedby="region-hint">
    <option value="all">All regions</option>
    <option value="north">North</option>
  </select>
  <small id="region-hint">Reporting area</small>
</div>
<label class="civic-choice">
  <input type="checkbox" name="comparison">
  <span>Include comparison</span>
</label>
<button type="button" class="civic-button">Reset</button>
<button type="submit" class="civic-button civic-button--primary">Apply</button>
```

- `.civic-field` is required for select and input borders, padding and colors.
  Associate labels with unique IDs. `input.civic-input` uses the same field wrapper.
- Plain selects need `.civic-select--native` and `native.css` to preserve the
  browser's arrow. Do not add the React select wrapper or decorative chevron.
  Do not apply this single-select contract to multi-selects or `select[size]`.
- Use native `disabled`, `checked`, `required` and form semantics, not classes or
  ARIA alone. Checkboxes retain native keyboard and submission behavior. Indeterminate
  checkbox state, if needed, is a host-owned DOM property.
- Errors need `aria-invalid="true"`, `aria-describedby` pointing to a visible
  `.civic-error` message, and the host's validation behavior. CSS alone does not validate.
- Explicit button `type` avoids accidental submission. Toggle buttons need host-managed
  `aria-pressed`; disabled navigation requires host behavior. Give icon-only controls
  accessible names and visible tooltips; the CSS archive supplies no icons.

## Tables and Pagination

```html
<div class="civic-table-scroll" role="region" aria-label="Regional totals" tabindex="0">
  <table class="civic-table">
    <caption>Regional totals</caption>
    <thead><tr><th scope="col">Region</th><th scope="col">Total</th></tr></thead>
    <tbody><tr><th scope="row">North</th><td>120</td></tr></tbody>
  </table>
</div>
<nav class="civic-pagination" aria-label="Results pages">
  <button type="button" class="civic-button" disabled>Previous</button>
  <span aria-live="polite">1 / 2</span>
  <button type="button" class="civic-button">Next</button>
</nav>
```

The focusable, named region permits keyboard horizontal scrolling when the host
sets an appropriate table width. Keep caption, header scopes and native table
display semantics. Do not hide headers with `display:none` on mobile. Complex
headers require explicit `id`/`headers` associations. Pagination state, labels,
disabled boundaries and updates remain host-owned. These styles do not paginate.

## Verification Boundary

The plain HTML fixture tests local-file loading with JavaScript disabled and HTTP(S)
requests blocked, both
neutral color modes, 320/390/1440px viewports, native selection/checkbox behavior,
focus, disabled/invalid states, labels, table semantics and keyboard scrolling in
three browser engines. Offline emulation is enabled after file loading because
WebKit rejects `file://` navigation when that emulation is already enabled; no
HTTP(S) requests are permitted during loading either. This is not a screen-reader/device certification or a full
host-app accessibility audit. Consumer migrations still need behavior/data parity,
host-theme contrast and offline checks.
