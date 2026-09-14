# review-responder

New reviews in → drafted replies in your voice → human approval → published → weekly digest.

## Pipeline

```
poll reviews -> normalize untrusted text (sandbox) -> draft reply
(pluggable provider + owner voice) -> approval gate (two lanes)
-> publish reply (cloud browser) -> weekly rating digest (Slack/email)
```

## Why Solari

- **Sandbox**: review text is untrusted, stranger-written content. It is
  normalized inside a disposable VM (`normalize_worker.py`) — control
  characters, markup, and link/phone flags — before anything downstream sees it.
- **Cloud browser**: platforms without a reply API still need the reply posted
  through their web UI. A cloud browser with a saved profile does that; in
  mock mode the bundled fixture page plays the platform.

## Approval lanes — nothing auto-publishes

| Stars | Lane | Behavior |
| --- | --- | --- |
| 1–3 | `human_edit` | Draft is flagged; approver is expected to edit before posting |
| 4–5 | `fast` | Quicker approval, but still requires a decision |

A denied or timed-out approval means nothing is published — fail closed.

## Config — `config/review_responder.yaml`

- `reviews` — source JSON (fictional fixture by default).
- `portal_base_url`, `profile`, `selectors` — the publish target.
- `drafting.provider` — `template` by default; implement `ReplyProvider` in
  `draft.py` to plug in a model. Model selection is deliberately a one-method
  interface so providers stay swappable.
- `voice` — sign-off, contact line, and per-band templates. The shop's voice
  is config, not code.
- `digest_email` — weekly digest recipient.

## Idempotency

Published/resolved review ids persist in `out/review_responder_state.json`;
a re-run never drafts or posts twice for the same review.

## Cost profile

One sandbox per run (normalization) + one cloud-browser session per published
reply. Measured per run in the JSONL log — see `runs/cost-report.py`.

## Known limitations

- The bundled publish target is a fictional fixture page; a real GBP or
  Facebook publish needs `portal_base_url`, a logged-in Solari profile, and
  selector config for that platform's UI.
- Flagged content (links, phone numbers in review text) is surfaced in the
  run log; it does not block drafting.
- The weekly digest covers the ingested set — plug in your platform's review
  list API for a real week-over-week trend.
