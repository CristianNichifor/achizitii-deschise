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

- **public IP:** `[adresa IP redactata]`
- **time:** 2026-09-07 ~22:50 UTC (01:50 EEST, 8 September)
- context: automated collection for an open-data project, since corrected to ~8 req/s

## The rule this establishes

Do not discover a service's tolerance by approaching it. For a public service with no
published rate limit, pick a rate that is obviously modest, and if more is genuinely
needed, ask the operator. A throughput measurement is not permission.
