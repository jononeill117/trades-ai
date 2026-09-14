# quote-builder

A plain-English job description in → a complete professional quote out,
priced from YOUR shop's own history.

## The iron rule

**The model never invents pricing.** Line items are proposed by keyword
rules over the job description and priced at the median of what this shop
actually charged for the same item — clamped inside that band, with the
historical job IDs cited on every line. Anything ambiguous (a single weak
keyword like "tankless") is flagged as an option for a human to confirm,
never silently priced.

## Pipeline

```
plain-English description + quote history (fixtures/history/*.json)
-> match items (keywords -> historical item keys)
-> assemble in the sandbox (median-of-band pricing, totals, tax)
-> render the full quote document (Markdown + HTML)
-> approval gate -> email delivery
```

## Why Solari

**Sandbox**: untrusted job text is processed in a disposable VM
(`quote_worker.py`) which reads the price bands and emits the quote —
numbers exist nowhere else in the pipeline.

## What the quote contains

- Line items with quantities, unit prices, and the historical jobs that
  justify each price
- Explicit scope **inclusions** AND **exclusions** (never empty — the
  worker falls back to standard exclusions if config is cleared)
- **Exploratory areas** — what can't be known until work starts ("flue
  liner condition unknown until the old unit is pulled"), gathered from
  the history behind the matched items
- **Concerns & risks** about this specific project, stated plainly
- **Escalation & scope-increase policy** — how price changes get approved
  if the job grows
- **Deposit, payment terms, validity period, terms of service**

## Idempotent

An unchanged quote for the same job is rendered again but never re-sent —
`out/quote_builder_state.json` fingerprints each delivered document.

## Config — `config/quote_builder.yaml`

`history`, `job`, `tax_rate`, `inclusions`, `exclusions`, `terms`
(deposit / payment / validity / escalation_policy / tos), `fallback_email`.

## Approval points

`quote.deliver` — rendered to `out/` regardless; emailed only on an
approved decision. Wired through the trust ledger (`core/trust.py`), so
this action can earn `auto` over time — see docs/trust.md.

## Cost profile

One sandbox per run. See `runs/cost-report.py`.

## Known limitations

- Matching is keyword rules; a model can replace `match_description` as
  long as it only proposes item keys — prices stay in the history.
- Photos are carried as references; no vision scoring is shipped.
- Delivery is email-only in v1.
