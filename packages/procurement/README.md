# procurement — parts list in, cheapest compliant quote out

```
load parts → price-check every supplier (cloud browsers)
           → aggregate (sandbox) → render quote → deliver
```

## Problem

Buying parts means checking the same SKUs across supplier sites, each
with different markup, stock, and login-gated pricing. This package
automates the comparison and produces a quote a human can sign off.

## Pipeline

1. **load** — parts list from `fixtures/parts_list.csv` (or the client's
   export).
2. **price-check** — one cloud browser per supplier (sequential on
   free-plan: one concurrent session). Each supplier adapter parses
   result cards and detects login-gates and out-of-stock.
3. **aggregate** — `aggregate_worker.py` runs in a sandbox. A
   login-gated price can't win a line; savings vs. the configured
   baseline supplier are computed honestly — 0 priced offers means
   $0.00, not an estimate.
4. **render** — Markdown + HTML quote in `out/`.
5. **deliver** — Slack/email summaries (gated).

## Approvals

Outbound delivery (email/Slack with customer-facing numbers) passes the
approval gate.

## Cost profile

Measured live run (2026-09-13): 3 sequential browsers ≈ 2.6s total,
1 sandbox ≈ 1.9s — and 0 priced offers because the target sites
bot-blocked the non-stealth free-plan browser. That is the honest cost
of a blocked run: you still pay for the sessions. Stealth + residential
proxy (`SOLARI_STEALTH=1`, paid plan) is the lever for hostile targets.

## Solari primitives

- **browser** — supplier price-checks, `recording=True`.
- **sandbox** — offer aggregation; scraped HTML never reaches the host.

## Mock vs live

Mock: `HttpDriver`/`FixtureDriver` serve cached supplier pages from
`fixtures/supplier_pages/`. Live: real browsers against real sites —
expect bot-blocking on free plan.

## Limitations

- Supplier adapters are regex/selector-driven; sites change markup —
  `healthcheck.py` catches fixture drift, a broken live selector shows
  up as 0 offers for that supplier (logged, not silent).
- Login-gated pricing needs a profile (sign in once via handoff link).
- No purchasing is ever performed — this produces quotes only.
