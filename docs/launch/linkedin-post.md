# LinkedIn post — DRAFT (do not publish)

I just open-sourced trades-ai — nine production-shaped AI automations for
plumbing, HVAC, and electrical shops, built entirely on Solari's cloud
infrastructure (@getsolari).

The problem: trades businesses lose real money to boring operational
gaps. Missed calls that never get a text back. Invoices that sit unpaid
for 60 days. Quotes that were never followed up. Reviews that go
unanswered.

The toolkit covers the whole loop:

- dispatch: work-order email → parsed inside a sandboxed microVM →
  scheduled → booked in the field-service portal by a cloud browser
- missed-call-textback: missed call → approved SMS → qualified → booked
- invoice-chaser: aging AR reconciled in a sandbox → escalating reminders
- review-responder: drafts replies, low-star reviews get a human-edit
  lane, publishing is approval-gated
- quote-builder / quote-follower: plain-English job descriptions priced
  from the shop's own quote history — every line cites the jobs behind it;
  stale quotes chased
- weekly-brief: the week's reviews, callbacks, quotas, and lost quotes →
  a leader's brief for the weekly tech meeting
- progressive trust: every action starts human-gated; the system proposes
  earned autonomy per action type, and the owner grants or revokes it
- procurement: parts list → price-checks across supplier sites → cheapest
  compliant quote
- photo-marketer: job photos cleaned in-sandbox → approved → posted

What makes it serious rather than a demo:

- Every customer-facing action passes a fail-closed approval gate.
- Untrusted input is processed inside disposable microVMs — a prompt
  injection in an email can't reach anything.
- Every run writes a JSONL audit log with measured session usage; a cost
  report turns that into dollars only when you configure real rates.
- Mock mode runs all nine pipelines with zero credentials — evaluate
  before you spend anything.

Honest notes: on Solari's free tier, replays were unreliable
(ReplayUnavailable on most sessions) and big supplier sites block
non-stealth browsers — the run logs record exactly what happened.

MIT licensed. Built for the Pinetree Research SWE contest — thanks
@harrychow_ for the push to build in the open.

https://github.com/jononeill117/trades-ai
