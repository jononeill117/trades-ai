# Demo recordings

Every browser and desktop session in trades-ai is created with
`recording: true`. The replay is both the demo video and the audit trail —
URLs land in the run log (`runs/*.jsonl`, `replay_url` field), and live runs
download local copies here.

## What's in this folder

- `*.replay.ndjson` — session replays (rrweb NDJSON: a DOM-level recording,
  not a video — small, greppable, diffable). Files named `mock_*` are
  placeholder artifacts from `--mock` runs; real replays come from `--live`.

## Producing real replays

```bash
python demo.py --live
```

Each run's log prints session IDs; replays download automatically where the
code paths call `core.download_replay(...)`. To fetch one manually:

```python
from core.solari_client import SolariCore
core = SolariCore()                     # needs SOLARI_API_KEY
await core.download_replay("<session-id>", dest_dir="docs/demo")
```

Replays upload asynchronously after a session is released — poll for ~30s
before concluding one is missing (the helper already does).

## Watching a desktop live

Desktop sessions return `streamUrl` (a VNC stream) — it lands in the run log
via `run_log.session("desktop", ..., stream_url=...)`, so you can watch a
computer-use run while it happens.
