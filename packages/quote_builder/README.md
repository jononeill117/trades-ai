# quote-builder

Job details + photos in → line items matched from YOUR pricebook → approved → delivered.

## The iron rule

**The model never invents pricing.** Every line item comes from
`config/quote_builder.yaml`'s pricebook CSV — stable item IDs, descriptions,
unit prices, taxability. Keyword matching proposes which IDs apply; totals
are computed from pricebook rows only. Anything uncertain is flagged for
human selection, not priced by a guess.

## Pipeline

```
job record + pricebook -> match items (keywords -> item IDs,
uncertain -> human-selection list) -> assemble totals (sandbox)
-> render Markdown/HTML -> approval gate -> email delivery
```

## Why Solari

**Sandbox**: untrusted job text is processed in a disposable VM
(`quote_worker.py`), which reads the pricebook and emits the quote — prices
exist nowhere else in the pipeline.

## What the quote shows

Item IDs, quantities, match reasons ("keywords: expansion tank"), uncertain
matches needing human confirmation, assumptions, exclusions, subtotal, tax,
total. The approval request includes the total and the unconfirmed list.

## Config — `config/quote_builder.yaml`

`pricebook`, `job`, `tax_rate`, `assumptions`, `exclusions`, `fallback_email`.

## Approval points

`quote.deliver` — the quote is rendered to `out/` regardless, but it is only
emailed on an approved decision.

## Cost profile

One sandbox per run. See `runs/cost-report.py`.

## Known limitations

- Matching is keyword rules; a model can be layered into `match_items`' place
  as long as it only proposes IDs — prices stay in the pricebook.
- Photos are carried as references; no vision scoring is shipped.
- Delivery is email-only in v1.
