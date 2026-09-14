# meeting-prep

The week's completed jobs in → one-page brief per tech + a shop summary for the Monday meeting.

## Pipeline

```
ingest jobs (export, or browser adapter for API-less FSMs)
-> normalize (sandbox) -> rule-based flags -> per-tech briefs
+ shop summary (Markdown + printable HTML) -> Slack digest
```

## Why Solari

- **Sandbox**: job data is an untrusted export — normalized inside a
  disposable VM.
- **Cloud browser**: FSMs without an API still have a completed-jobs page.
  `source: browser` scrapes it through a recorded cloud browser (fixture page
  in mock mode).

## Flags are rules, not vibes

Every flag is a deterministic rule that cites its `job_id`:

| Flag | Rule |
| --- | --- |
| `callback` | status=callback or callback language in notes |
| `complaint` | complaint language in notes |
| `warranty_risk` | warranty mentioned — verify coverage before billing |
| `missing_notes` | no notes — can't verify what was done |
| `zero_amount` | $0 on a non-callback job — pricing error? |

Coaching notes are derived from flag patterns per tech. Nothing in the brief
is unattributed — a manager can check every claim against the source record.

## Config — `config/meeting_prep.yaml`

`source` (json|browser), `jobs`, `jobs_url`, `profile`, `selectors`.

## Outputs

`out/brief-<tech>-<run>.md` + `.html` (printable), `out/shop-summary-<run>.*`,
and a Slack digest. Internal artifacts — not customer-facing, so no approval
gate is needed; the gate covers outbound actions only.

## Cost profile

One sandbox per run; one browser session when `source: browser`. See
`runs/cost-report.py`.

## Known limitations

- No LLM summary layer is shipped — flags and rendering are deterministic;
  the provider seam for model-assisted summaries is a documented extension
  point, not a bolt-on.
- The browser source reads the list page's columns only; per-job notes come
  from the export path.
