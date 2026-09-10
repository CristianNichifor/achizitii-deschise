# Where the data lives

| Layer | Home | Why |
|---|---|---|
| The site | GitHub Pages | Static, cached, cannot go down. Loads even if everything below is broken. |
| Aggregates (~20 MB) | GitHub Pages, from a release asset | Small, and the common case should never depend on an external service. Delivered by release rather than by git — see below. |
| Collected daily prices | GitHub Pages, from git | Written once, never rewritten, and the only copy that exists. Versioned on purpose. |
| Line items (to 2.3 GB) | Cloudflare R2 — **planned, not live** | Exceeds the 1 GB Pages cap, and git keeps every version of a file forever. See below for what exists today. |
| Raw SEAP JSON | Cloudflare R2 | Kept so a mapping fix never means re-downloading. Not currently retained — see below. |
| Bulk gov Parquet (12 GB) | Local only | Rebuilt from data.gov.ro, which is unreachable from GitHub runners. |

## What is actually provisioned, as of 2026-09-10

Most of this document describes a design. Two parts of it are running and the rest is not,
and the table above reads as current state, so:

| | State |
|---|---|
| Bucket `achizitii-deschise` | exists, **3 objects** |
| Custom domain on it | **none** |
| CORS policy on it | **none** — the API answers `The CORS configuration does not exist` |
| Line items published to R2 | **no** |
| Aggregates on Pages, from a release asset | **yes** |
| Collected daily prices in git | **yes**, 7 files, 1.4 MB |

Nothing is wrong with that. The archive is seven days old because SEAP collection has been
held since 2026-09-08 pending ADR's answer; 1.4 MB belongs on Pages, and standing up a
custom domain and a CORS policy to serve it would be ceremony around nothing. The R2 half
earns its keep at gigabytes, not megabytes.

What matters is that the rows above are not mistaken for a thing that exists. Everything in
"Setting it up" is still to be done, and the monthly-consolidation reasoning below is a
design for when there are months to consolidate.

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
3. `scripts/fetch_bundle.sh` downloads the newest `bundle-*` release when `site/data` is not
   already present. Both `pages.yml` and `tests.yml` call it.

**The tests fetch it too, and that is the point.** A dozen tests assert things about the
bundle; `test_price_drilldown` reads the manifest unconditionally and several others carry a
`skipif(not is_file())` that would have quietly become a pass. Fetching keeps them running,
and upgrades them: they now check the **released** bundle against the **committed** price
archive, so publishing without refreshing the release makes `test_price_drilldown` fail
instead of the site 404ing on a file the manifest names.

Result on the tracked tree: **20.0 MB → 0.8 MB**, with the 1.4 MB price archive counted
separately. The 302 MB of history was left where it was — removing it means rewriting every
commit, which was a decision of its own and not one that change made. It was made on
2026-09-10; see below.

`site/data/preturi/**` is deliberately excluded and stays in git. Those are the collected
daily prices: written once, never rewritten, ~0.4 MB per collected day, and the only copy
that exists — unit prices cannot be reconstructed retroactively because the bulk exports
carry no quantities. `scripts/check_repo_size.py` measures the two totals separately for
exactly this reason, so a rule about the derived payload never reads as an argument for
deleting the archive.

## And then the history went too, on 2026-09-10

Ignoring a path stops the next version being added; it does nothing about the versions
already there. A clone still paid for all of them:

| | Before | After |
|---|---|---|
| `git clone` | **327 MB** | **5 MB** |
| Tracked tree | 2.6 MB | 2.6 MB |
| `main`'s tree hash | `798cc8dc…` | `798cc8dc…` |
| Price archive on `main` | 7 files | 7 files |
| Commits on `main` | 160 | 154 |

Every commit id changed. The rule was one `git filter-repo` callback:

```python
if filename.startswith(b"site/data/") and not filename.startswith(b"site/data/preturi/"):
    return None
return filename
```

**The trailing slash is the whole safety argument.** `site/data/preturi_unitare.parquet` is a
derived rollup and goes; `site/data/preturi/` is the archive and stays. Names that close are
not a good place to rely on reading the pattern correctly, so the rewrite was checked rather
than trusted: that no ref gained a file, that everything lost was under `site/data/`, that
nothing lost was under `site/data/preturi/`, and that `main`'s tree hash was unchanged. The
checks were themselves tested by deliberately breaking the callback twice — once to delete
the archive, once to delete `src/**` — and confirming each was caught.

Six commits on `main` touched nothing but derived parquet, became empty, and were dropped.
What they recorded was that a publish ran, which the release assets and the dated filenames
in the price archive both record better.

Two things worth knowing before anyone does this again:

**Tags must be pushed.** `git clone` fetches tags, so a tag left pointing at the old history
keeps the whole 323 MB reachable in every clone and the rewrite achieves nothing. The
`bundle-*` release survives being re-pointed: its target is the branch name `main` rather
than a commit id, and its assets are stored by GitHub outside git.

**Nothing was actually destroyed.** GitHub keeps `refs/pull/*` forever, and one
`git fetch origin '+refs/pull/*/head:refs/remotes/pr/*'` brings the clone straight back to
326 MB with fifteen versions of `furnizori_an.parquet` in it. The rewrite took the old blobs
out of the *default* clone path, which was the entire goal. It is the wrong tool for removing
a secret, and it was never being used for one — everything dropped here is public and
derived.

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
