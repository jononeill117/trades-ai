# Demo recordings

Every browser and desktop session in trades-ai is created with
`recording: true`. The replay is both the demo video and the audit trail —
URLs land in the run log (`runs/*.jsonl`, `replay_url` field), and
`scripts/collect_replays.py` downloads what actually exists into this
folder.

## What's in this folder

- `replays.json` — the honest manifest: every live browser session we
  asked about, and what the replay endpoint actually said.
- `*.replay.ndjson` — session replays (rrweb NDJSON: a DOM-level
  recording, not a video — small, greppable, diffable). Files named
  `mock_*` are placeholder artifacts from `--mock` runs; real replays
  come from `--live`.

## Current status (2026-09-13)

Replays on the free-tier account used for the live runs were mostly
unreliable: most sessions return `ReplayUnavailable` (non-retryable 404)
even after polling. One real replay was captured from a live procurement
browser session — see `replays.json` for the session id and signed URL.
Nothing here is synthetic except the clearly-named `mock_*` file.

## Producing replays

```bash
python demo.py --live --only <package>   # records every browser session
python scripts/collect_replays.py        # needs SOLARI_API_KEY
```

The collector polls `GET /sessions/{id}/replay-url`, downloads whatever
exists, and records `ReplayPending`/`ReplayUnavailable`/errors verbatim in
`replays.json`. Replays upload asynchronously after a session is released
— poll before concluding one is missing (the helper does), and accept
`unavailable` as a real answer.
