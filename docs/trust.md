# Progressive trust — training wheels that come off when earned

Nobody who runs a shop hands customer-facing sends to a new system on day
one. That skepticism is correct, and trades-ai is built around it: every
action type starts fully gated, earns autonomy from your actual decisions,
and can be revoked at any time. You are always in charge.

## The levels

Every action type (`review.publish`, `invoice.reminder.email`,
`quote.deliver`, `sms.textback`, `social.post`, …) has one of two levels in
`config/autonomy.yaml`:

- **`gate`** (default for everything) — a human approves each action
  through the normal approval gate. Fail-closed, always.
- **`auto`** — the action runs without a per-item decision. It is still
  fully logged and marked `trust_auto` in the run log, so you can audit
  everything it did.

Anything not listed in `levels:` stays gated. There is no third state —
autonomy is never ambient, it is granted per action type by you.

## The ledger

`out/trust_ledger.json` records, per action type:

- `attempts` — how many times the action came up
- `approved` — you approved the draft **unchanged**
- `edited` — you approved after changing it
- `denied` — you refused it
- `auto` — runs under an `auto` grant (counted, but not part of confidence)

**Confidence** is the share of human decisions that approved the draft
unchanged: `approved / (approved + edited + denied)`. Edits and denials
push it down — the system has to keep earning it.

## The proposal flow

The system never loosens itself. When an action's confidence crosses the
bar in `config/autonomy.yaml` (`min_attempts` decisions at
`min_confidence` unchanged-approval), it writes a proposal event — logged
in the run log, echoed in run output, and posted to the ops channel:

> I've drafted 47 review replies — you approved 46 unchanged (98%).
> Confidence is very high. Proposal: auto-post replies to 5-star reviews,
> keep gating 1-3 stars. You can always check in on me or revoke this in
> config/autonomy.yaml.

Per-action proposal wording lives under `proposals:` in the config, so the
suggestion is specific (e.g. "auto-send friendly reminders, keep gating
final notices") rather than all-or-nothing.

You grant or deny by editing `config/autonomy.yaml`:

```yaml
levels:
  review.publish: auto    # grant
  review.publish: gate    # revoke — back to full approvals
```

A denial or an edit on an action with an open proposal **withdraws the
proposal** — the signal changed, so the offer is stale. It will propose
again only if confidence crosses the bar again.

## The seed block

`seed:` in `config/autonomy.yaml` imports decisions made before the ledger
existed (applied once, when `out/trust_ledger.json` is first created).
The bundled seed reflects a plausible shop history so `demo.py --mock`
shows a real proposal. Delete it to start cold.

## Where it's wired

`review-responder` (`review.publish`), `invoice-chaser`
(`invoice.reminder.email` / `.sms`), and `quote-builder`
(`quote.deliver`) call `core.trust.require(gate, trust, run_log, ...)`
instead of `gate.require(...)`. Any other package can adopt it the same
way — one line at the gate call site.
