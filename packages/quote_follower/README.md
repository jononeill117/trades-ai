# quote-follower

Quotes created but never sent — or sent and gone quiet — found, flagged to the owner, and followed up after approval.

## Pipeline

```
fetch quotes -> classify (never_sent | sent_inactive | active)
-> owner alert (Slack) -> follow-up message -> approval gate -> send
-> outcome tracked in the run log
```

## Sources — the adapter contract

| Source | When | Notes |
| --- | --- | --- |
| `csv` | Always works | Any FSM/CRM exports CSV; `quotes_csv` path |
| `browser` | No API/export | Scrapes the system's quotes-list page through a cloud browser with config selectors |
| API | Your system has one | Implement `fetch_quotes() -> list[Quote]` in `sources.py` |

## Why Solari

**Cloud browser**: plenty of field-service systems have no API and no export —
the only way to see the quote list is the web UI. A recorded cloud browser
reads it; mock mode serves the bundled fixture page through the same driver.

## Classification

- `never_sent` — status draft/created/unsent. Money left un-sent.
- `sent_inactive` — sent but no activity for `idle_days`.
- `active` — recent activity or already won/lost; left alone.

## Approval points

Every follow-up (`quote.followup.email` / `quote.followup.sms`) is gated with
the quote value, days idle, and the full message in the payload. Denied =
nothing sent.

## Honest reporting

The run reports quote value, days idle, activity, and that a follow-up was
sent. It does **not** claim the follow-up won the job — outcomes come only
from supplied status changes, never inferred attribution.

## Config — `config/quote_follower.yaml`

`source`, `quotes_csv`, `list_url`, `profile`, `selectors`, `idle_days`,
`shop_name`.

## Cost profile

One cloud-browser session per run (browser source). See `runs/cost-report.py`.

## Known limitations

- The browser source sees what the list page shows; contact/activity fields
  are joined from the CSV export when configured.
- Follow-ups are single touches, not sequences — wire into invoice-chaser's
  sequence machinery if you want escalation.
