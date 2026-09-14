# weekly-brief

The week's shop data in → a leader's briefing for the weekly tech meeting.

Walk in knowing the company-wide areas of improvement, the recurring
friction, and exactly what to talk about — every claim cited back to the
exports it came from.

## Inputs

Drop the week's exports in a folder (bundled fictional set in
`fixtures/`):

| File | Contents |
| --- | --- |
| `jobs.csv` | job_id, tech, quoted, sold, amount — drives revenue + close rates |
| `quotas.csv` | tech, weekly_quota, actual |
| `callbacks.csv` | job_id, tech, reason, days_since_install, warranty |
| `failed_jobs.csv` | job_id, tech, quote_amount, reason — the $0/ghosted quotes |
| `wins.csv` | job_id, tech, amount, kind, note — big tickets, upsells |
| `reviews.json` | Google/Facebook-style 1–5 star reviews with text + tech |
| `surveys.csv` | post-job survey ratings + comments |

## Pipeline

```
ingest exports -> aggregate in the sandbox (revenue, close rate,
callback/warranty rate, sentiment, quota attainment per tech)
-> detect (friction themes, improvement areas, wins)
-> render briefing markdown -> Slack digest
```

## Why Solari

**Sandbox**: exports are untrusted files — all parsing and math runs inside
a disposable VM (`aggregate_worker.py`); only computed JSON crosses back.

## What the brief contains

- **Wins to celebrate** — top closer, 5-star streaks, big tickets, upsells
- **Friction to address** — recurring callback reasons and 1–3-star review
  themes, each with verbatim evidence
- **Areas of improvement** — techs below quota, low close rates, repeat
  callbacks, review dips — every item carries the number behind it
- **Talking points for the meeting** — concrete, per-topic openers
- **Per-tech coaching notes** — one-on-one material per tech

## Internal only

This package never contacts a customer — no approval gate is needed. If it
ever drafts external messages, those go through `core/approvals.py` like
everything else.

## Idempotent

One delivery per week label (`config/weekly_brief.yaml: week`). Re-runs
rewrite `out/weekly-brief-<week>-*.md` but do not re-notify.

## Config — `config/weekly_brief.yaml`

`week`, `fixtures`, and detection thresholds (`quota_attainment_min`,
`close_rate_min`, `callback_rate_max`).

## Cost profile

One sandbox per run. See `runs/cost-report.py`.
