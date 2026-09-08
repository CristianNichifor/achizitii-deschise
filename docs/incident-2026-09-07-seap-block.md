# 2026-09-07 — SEAP blocked our IP

## What happened

At roughly 22:50 UTC on 2026-09-07, `e-licitatie.ro/api-pub` began returning **HTTP 403**
with `server: SICAP` and an HTML notice:

> Accesul de la adresa dumneavoastră IP a fost restricționat. Sistemul a detectat, de la
> adresa IP de pe care accesați platforma, un volum de trafic automat care depășește
> limitele de utilizare normală. […] Pentru deblocare, contactați echipa de suport,
> menționând adresa IP publică și ora la care ați întâmpinat problema.

The restriction is on the **IP address**, not an API key, and affects everything behind
that address.

## Why it happened

We went looking for the maximum sustainable request rate and treated "the server answers
quickly with no errors" as evidence that a rate was acceptable. Over roughly two hours we:

- probed concurrency at 8, 16, 24 and 32 workers
- ran a sustained 400-record burst at 16 workers, measuring 36.9 records/second
- listed a full weekday across 12 CPV categories (8,272 records)
- fetched 400 acquisition details concurrently

`MAX_RPS` had been raised from 3.0 to 40.0 and `DETAIL_WORKERS` set to 16 on the strength
of those measurements.

**The reasoning was wrong.** Latency and error rate measure whether a server *can* absorb
load, not whether its operator is willing to serve it. SICAP's own threshold for "normal
use" is the only measure that mattered, and it is not published.

## What it cost

Access to the only source of line-item quantities and unit prices that exists. The bulk
data.gov.ro exports are unaffected, so 30M rows of procurement metadata, all eight
indicators, company profiles and the whole published site keep working. What stops is the
unit-price archive, which was one day old.

## What changed

- `MAX_RPS` 40.0 → **4.0**, `DETAIL_WORKERS` 16 → **2** (~8 records/second, a little over
  twice the long-standing 3.0 that never drew a complaint)
- schedule every three hours → **once a day**, as it ran for months without incident
- both constants now carry the incident in their docstring, so the next person to find a
  "safe" rate by measurement reads this first

The CPV-category partitioning from #39 is kept: it is 12 list calls a day and was not
what drew the block. It also remains correct — it is the difference between capturing
22% and 93% of a weekday.

## To restore access

Contact SEAP support with:

- **the public IP that was restricted** — deliberately not recorded here; it is a
  residential address, it identifies a person rather than the project, and a public
  incident report is not the place for it. It is in the operator's own logs, and whoever
  sends the request will know their own address.
- **time:** 2026-09-07 ~22:50 UTC (01:50 EEST, 8 September)
- context: automated collection for an open-data project, since corrected to 1.5 req/s —
  within the published limit of 500 requests per 5 minutes

## The rule this establishes

Do not discover a service's tolerance by approaching it. For a public service with no
published rate limit, pick a rate that is obviously modest, and if more is genuinely
needed, ask the operator. A throughput measurement is not permission.

## Follow-up, 2026-09-08: the limit was published all along

The fix above set `MAX_RPS` to 4.0 and called it "far below what drew the block". That was
true and beside the point. SEAP had published its thresholds on 2026-07-15:

| Threshold | Meaning |
|---|---|
| 500 accesses in 5 minutes from one IP | 1.67 requests/second sustained |
| 50 accesses in 1 second | burst ceiling |

4.0 rps is 1,200 requests per 5 minutes — **2.4× the published sustained limit**. The
post-incident configuration was still over the line; it simply ran on GitHub runners,
whose addresses had not yet been blocked. A backfill was cancelled mid-run on 2026-09-08
when this was found.

`MAX_RPS` is now **1.5**, leaving headroom under 1.67 for retries.

### What this costs

At 1.5 rps a weekday of ~8,300 acquisitions takes about 2 hours rather than 45 minutes.
Full history goes from ~100 days of continuous fetching to **~266 days**; the last three
years, from 21 days to **~56**.

### The part that is not a rate question

The same announcement discourages parallel queries, repeated requests at short intervals,
and **continuous automated processes over extended periods**, and directs anyone needing
high-volume or recurring programmatic access to contact support. A multi-month crawl is
what that paragraph describes, at any rate. The next step is a request to SEAP support —
both to lift the block and to ask what bulk or whitelisted access exists
for a public-interest open-data project. Not another round of tuning.

### The lesson, stated plainly

The first mistake was inferring consent from performance: 37 records a second with flat
latency meant the server answered quickly, never that it agreed. The second was fixing
that by picking a smaller number that still felt polite, instead of looking for whether
the operator had said what the number should be. They had, three weeks earlier.
