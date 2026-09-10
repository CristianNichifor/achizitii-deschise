# Where the data lives

| Layer | Home | Why |
|---|---|---|
| The site | GitHub Pages | Static, cached, cannot go down. Loads even if everything below is broken. |
| Aggregates (~20 MB) | GitHub Pages, from a release asset | Small, and the common case should never depend on an external service. Delivered by release rather than by git — see below. |
| Collected daily prices | GitHub Pages, from git | Written once, never rewritten, and the only copy that exists. Versioned on purpose. |
| Line items (to 2.3 GB) | Cloudflare R2 | Exceeds the 1 GB Pages cap, and git keeps every version of a file forever. |
| Raw SEAP JSON | Cloudflare R2 | Kept so a mapping fix never means re-downloading. Not currently retained — see below. |
| Bulk gov Parquet (12 GB) | Local only | Rebuilt from data.gov.ro, which is unreachable from GitHub runners. |

## The rule applied to the aggregates too

"Git keeps every version of a file forever" is the reason the line items went to R2. It is
just as true of the aggregates, which stayed in git, and that is where most of this
repository turned out to be. Measured 2026-09-10, across all history:

| Path | Total in history | Versions | Current |
|---|---|---|---|
| `site/data/furnizori_an.parquet` | 199.9 MB | 14 | 7.8 MB |
| `site/data/autoritati_an.parquet` | 45.2 MB | 14 | 1.9 MB |
| `site/data/cpv_an.parquet` | 26.4 MB | 14 | 2.3 MB |

271 MB of a 302 MB pack, from three files totalling 12 MB today. Every `achizitii publish`
rewrites all three, so every publish adds ~20 MB that no clone can ever avoid downloading.
Nothing about it looks wrong in a diff — it is one line saying a binary changed.

**The aggregates stay on Pages.** That part of the reasoning below is unchanged and worth
keeping: someone opening the site and reading totals should never depend on an external
service or spend a Class B operation. What changes is only how the bytes reach the Pages
build:

1. `achizitii publish` writes `site/data/` locally, as before.
2. `scripts/release_bundle.sh` uploads it as a `bundle-<date>` release asset — everything
   under `site/data` **except** `site/data/preturi/**`.
3. `.github/workflows/pages.yml` downloads the newest `bundle-*` release when `site/data`
   is not in the checkout, extracts it, and deploys exactly what it would have deployed
   before.

`site/data/preturi/**` is deliberately excluded and stays in git. Those are the collected
daily prices: written once, never rewritten, ~0.4 MB per collected day, and the only copy
that exists — unit prices cannot be reconstructed retroactively because the bulk exports
carry no quantities. `scripts/check_repo_size.py` measures the two totals separately for
exactly this reason, so a rule about the derived payload never reads as an argument for
deleting the archive.

## Why monthly files and not daily

DuckDB-Wasm reads Parquet over HTTP range requests, and every request is a **Class B
operation** against R2's 10 million per month free allowance. File *count* dominates cost:

| Layout | Files | Ops per full scan | Free scans/month |
|---|---|---|---|
| One per day | 4,015 | ~12,000 | **830** |
| One per month | 132 | ~400 | **25,000** |
| One per year | 11 | ~33 | 303,000 |

Daily files would exhaust the free tier at under a thousand queries. Collection stays
daily — that is the unit a run produces, and the never-shrink rule depends on it — but
`achizitii publish --r2` consolidates to one file per month before upload.

## The free tier is shared with the rest of the account

This project is one of several Cloudflare projects on the same account, and the 10 GB /
1M Class A / 10M Class B allowance is **per account, not per bucket**. Overage lands on
one invoice regardless of which project caused it, so this project is deliberately
frugal:

| Artefact | Size | On R2? |
|---|---|---|
| Published line items, 2 years | 0.29 GB | yes |
| Published line items, full history | 2.30 GB | yes |
| Raw SEAP JSON archive | 14 GB | **no** — alone it exceeds the whole shared tier |
| Bulk gov Parquet | 12 GB | **no** — rebuildable from data.gov.ro, stays local |
| Aggregates | 26 MB | **no** — stays on Pages, which keeps ordinary reads off R2 entirely |

Three mechanisms keep it there:

**A hard budget.** `upload()` refuses to push more than `R2_MAX_GB` (default 4) and says
why. Failing loudly beats discovering the overage on someone else's invoice.

**Immutable caching.** A closed month cannot change — the never-shrink rule only ever adds
days to the *current* month — so closed months are served
`max-age=31536000, immutable` and, behind a custom domain, are read from Cloudflare's edge
rather than from R2. A cached read is not a Class B operation. The current month gets an
hour, the manifest five minutes.

**Aggregates stay on Pages.** The common case — someone opening the site and looking at
totals — never touches R2 at all. Only drill-down into line items does.

## Cost

| Footprint | Over the 10 GB free tier | Monthly |
|---|---|---|
| Published parquet only (2.3 GB) | — | **$0.00** |
| + raw SEAP JSON (16.3 GB) | 6.3 GB | **$0.09** |
| + bulk gov parquet (28.3 GB) | 18.3 GB | **$0.27** |

Egress is free on R2, which is what makes serving a multi-gigabyte archive to the public
viable at all.

## Setting it up

R2 must be enabled in the Cloudflare dashboard first — the API refuses with
`Please enable R2 through the Cloudflare Dashboard` until it is.

1. Create a bucket, e.g. `achizitii-deschise`.
2. Create an API token scoped to **Object Read & Write** on that bucket.
3. Add four repository secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
4. Attach a **custom domain** to the bucket. Not `r2.dev` — Cloudflare documents it as
   rate-limited and non-production, and a custom domain also returns CORS headers
   automatically and puts Cloudflare Cache in front, so repeat reads stop counting as
   Class B operations.
5. Set the bucket CORS policy to allow the Pages origin.

Without those secrets nothing breaks: `publish` writes locally and logs that R2 is not
configured, so anyone can build the whole site from a clone.
