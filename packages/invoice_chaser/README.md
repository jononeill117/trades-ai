# invoice-chaser

Aging AR export in → escalating reminder sequence out → recovered revenue reported honestly.

## Pipeline

```
AR aging CSV -> normalize (sandbox) -> apply supplied payment/status events
-> next due reminder (configurable sequence) -> approval gate
-> send via email or SMS -> flag exhausted non-responders -> report
```

## Why Solari

**Sandbox**: an AR export is an untrusted file — it is parsed and normalized
inside a disposable VM (`normalize_worker.py`), and malformed rows come back
with warnings instead of crashing the run.

## States

`aging → contacted → promised → paid`, with `disputed` and `escalated` as side
exits. Status moves only two ways: the AR file's own status, or a supplied
payment/status event (`payments` config). **Revenue is never claimed as
recovered without a supplied `paid` event** — sending a reminder is not
recovery.

## The sequence is configuration

`config/invoice_chaser.yaml` — each step has `after_days` (days past due),
`channel` (email|sms, falls back to whichever contact exists), `tone`, and
templates. `escalate_after_days` flags invoices that exhausted the sequence to
Slack for a human call.

## Approval points

Every reminder — `invoice.reminder.email` / `invoice.reminder.sms` — goes
through the gate with tone, amount, and full body in the request payload.
Denied means nothing sent, and the step is not marked sent.

## Idempotency

`out/invoice_chaser_state.json` tracks `steps_sent` per invoice. A re-run
resumes the sequence where it left off — never resends a step already sent.

## Cost profile

One sandbox per run (normalization); sends ride the configured notifiers.
Measured in the JSONL run log — `runs/cost-report.py`.

## Known limitations

- No live accounting integration: input is CSV, payments are a JSON export.
  Wire your system's export to those paths.
- Channel fallback picks whatever contact exists; an invoice with neither
  email nor phone is reported skipped, never silently dropped.
- "Escalated" is a Slack flag — it does not place the call.
