# missed-call-textback

Missed call in → instant acknowledgment text → qualified need → booked job or human escalation.

## Pipeline

```
missed-call event -> approval gate -> first SMS (Quo) -> qualify (rules)
-> book in field-service portal (cloud browser) OR escalate (Slack)
-> confirmation SMS (approval gate) -> audit log
```

## Why Solari

**Cloud browser**: booking happens in the field-service portal UI itself — the
same screens a dispatcher would drive. Live mode runs a real Solari cloud
browser against the portal (the bundled FieldDesk portal runs inside a Solari
sandbox behind a preview URL); mock mode runs the identical pipeline against
FieldDesk on localhost.

## Approval points

- `sms.textback` — the first customer-facing text. Always gated. Deployers who
  can legally send a pre-approved transactional template may add
  `sms.textback` to `preapproved_actions` in `config/approvals.yaml`; the
  request and decision are still logged.
- `sms.booking_confirm` — the post-booking confirmation text.

Denied approval = no send, and the event is marked processed so a retry does
not re-prompt for the same call.

## Config — `config/missed_call_textback.yaml`

| Key | Meaning |
| --- | --- |
| `events` | JSON list of missed-call events (fictional fixture by default) |
| `textback_template` / `shop_name` | First SMS body; `{shop}` substituted |
| `portal`, `portal_base_url`, `portal_profile`, `portal_seed` | Booking target — same adapter contract as dispatch |
| `availability` | Tech schedule used to pick a slot |
| `default_site` | Used when the caller left no address |

## Qualification rules

Deterministic keyword matching (the transcript is untrusted input):
emergency signals (`gas smell`, `flooding`, `sparking`, …) always escalate to
Slack instead of booking. Unknown trade or empty transcript also escalates.

## Idempotency

Processed call ids persist in `out/missed_call_state.json`. Re-running the
same events is a no-op — no duplicate texts, no duplicate jobs.

## Cost profile

Measured per run in the JSONL log (`usage` events + `cost_summary`): one
cloud-browser session per booked call, plus one sandbox in live mode hosting
the portal. See `runs/cost-report.py`.

## Known limitations

- Keyword qualification is intentionally conservative; ambiguous calls route
  to a human rather than guess.
- The Quo adapter is a documented stub — verify the endpoint against current
  Quo API docs before relying on it.
- Escalation delivers to Slack only when `SLACK_WEBHOOK_URL` is configured;
  otherwise it's logged as `skipped` with the full payload.
