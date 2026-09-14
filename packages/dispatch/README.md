# dispatch — work-order email in, booked job out

```
ingest → parse (sandbox) → schedule → book (cloud browser)
       → confirm (desktop fallback) → notify → audit
```

## Problem

Work orders arrive as unstructured email. Somebody has to read each one,
figure out the trade and urgency, find an open slot, key it into the
field-service portal, and tell the customer. This package does the whole
loop.

## Pipeline

1. **ingest** — bundled fixture emails, or a real Gmail inbox over IMAP
   when `GMAIL_*` is set.
2. **parse** — `parse_worker.py` runs inside a Solari sandbox: untrusted
   email never touches the host; normalized JSON comes back over
   `@@RESULT@@`.
3. **schedule** — availability rules in `config/dispatch.yaml` propose a
   slot and tech.
4. **book** — a portal adapter drives a cloud browser. FieldDesk (the
   bundled fictional portal) is the reference; ServiceTitan / Housecall
   Pro / Jobber ship as selector-map examples.
5. **confirm** — if the portal needs a GUI-only step the adapter raises
   `NeedsDesktopError` and a Solari desktop takes over. On free-plan
   accounts the held portal session trips the concurrency limit and this
   step skips itself — logged, not hidden.
6. **notify** — Slack summary, customer email + SMS (gated; unconfigured
   channels log "would have sent").

## Approvals

Customer-facing notifications (email/SMS confirmations) pass through the
approval gate. Portal writes are gated by the client's
`preapproved_actions` policy.

## Cost profile

Measured live run (2026-09-13): 2 sandboxes ≈ 4.7s, 2 browsers ≈ 6.1s
for two work orders. Dollar estimate: `unavailable` until rates are set
in `config/pricing.yaml`. Desktop fallback adds a desktop session when
actually exercised.

## Solari primitives

- **sandbox** — untrusted email parsing; in live mode the FieldDesk demo
  portal itself is hosted in a sandbox behind a preview URL.
- **browser** — portal booking, `recording=True`.
- **desktop** — fallback for GUI-only portal steps.

## Mock vs live

Mock: FieldDesk runs locally, emails come from `fixtures/work_orders/`,
sandboxes are local subprocesses running the same worker. Live: real
Solari sessions, real Gmail IMAP if configured.

## Limitations

- Real portals need a selector map (`config/portals.<name>.yaml`) or an
  adapter class — see `docs/adapter-guide.md`.
- Desktop fallback needs a free concurrency slot; on free plans it skips.
- IMAP polling is batch, not webhook — schedule the run (see
  `docs/deployment.md`).
