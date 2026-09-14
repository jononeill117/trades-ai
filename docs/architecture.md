# Architecture

trades-ai is a monorepo: one shared `core/` plus one folder per use case
under `packages/` (nine today). The rule that keeps it coherent:
**use-case packages never import the Solari SDKs directly** — they go
through `core`.

```
                 ┌────────────────────────────────────────────────┐
                 │                     core/                       │
                 │  SolariCore ── browsers / sandboxes / desktops  │
                 │  MockSolari ── local stand-ins for --mock       │
                 │  RunLog ───── JSONL audit log (runs/)           │
                 │  CostTracker ─ measured session usage           │
                 │  ApprovalGate ─ fail-closed human gates         │
                 │  notify/ ──── Slack · Gmail · Quo SMS           │
                 │  drivers/ ─── PageDriver over Playwright/HTTP   │
                 └───────▲───────────────────────────▲────────────┘
                         │                           │
   ┌─────────┬───────────┴───────────┬───────────────┴──┬─────────┐
   │dispatch │missed-call- │review-  │invoice- │quote-  │meeting- │
   │         │textback     │responder│chaser   │follower│prep     │
   │procure- │             │         │         │        │         │
   │ment     │quote-builder│         │         │        │photo-   │
   │         │             │         │         │        │marketer │
   └─────────┴─────────────┴─────────┴─────────┴────────┴─────────┘
```

## The package contract

Every package is `packages/<name>/agent.py` exposing:

```python
async def run(core, run_log, cfg) -> dict
```

and must:

- run the same pipeline in `--mock` and `--live` (only `core` differs)
- meter every session with `run_log.usage(...)` / `run_log.time_session(...)`
- send untrusted input through a sandbox worker
- gate every external action through `core.get_gate(mode, run_log)`
- be idempotent (a `out/*_state.json` processed-set, or equivalent)
- emit JSONL audit events and write artifacts under `out/`
- never import `solari_*` SDKs directly

`new_usecase.py` scaffolds all of this.

## The trust boundary

Untrusted content — inbound email, scraped supplier pages, uploaded
photos — is processed **inside a Solari sandbox**, never by the
orchestrator itself. The worker scripts (`parse_worker.py`,
`aggregate_worker.py`, `normalize_worker.py`, `media_worker.py`, …) are
self-contained stdlib files copied into the microVM; only their
normalized JSON output (printed after a `@@RESULT@@` marker) crosses back
out. A prompt-injection attempt in an email can write a weird field
value — it cannot reach your disk, your network, or your credentials,
because those things simply aren't in the VM.

## The approval boundary

`core/approvals.py` + `config/approvals.yaml`. `gate.require(action,
channel, summary, payload, requester)` records an `approval_request`
event, asks the configured backend, and returns a decision that is logged
either way. Backends:

- `cli` — interactive `[y/N]` prompt (default live behavior)
- `slack` — post to an approvals channel, poll for a reaction
- `mock` — deterministic decisions for tests/`--mock`
- `preapproved_actions` — explicit opt-in allowlist per action type

No backend configured → denied. Fail-closed by design.

## Cost instrumentation

`core/cost.py` + `config/pricing.yaml`. `run_log.usage(kind, session_id,
seconds)` emits a `usage` event per session; `run_log.finish()` appends a
`cost_summary` (session counts, seconds per primitive, estimated cost or
`unavailable`). `runs/cost-report.py` summarizes across history,
deduplicating session open/close events by session id.

## Solari gotchas this codebase already handles

Read these before touching `core/solari_client.py` — each one cost time:

- **Recording is opt-in per session, at create time.** `recording=True`
  is our default everywhere; a session created without it can never
  produce a replay (the endpoint 404s forever).
- **Replays upload asynchronously after release** — and on some accounts
  may never land (`ReplayUnavailable`, non-retryable 404). Poll for ~30s,
  then record the truth. `scripts/collect_replays.py` does exactly this.
- **`browser.close()` releases the session.** Skip it and the slot is
  held until the plan deadline. Always try/finally.
- **`sandbox.kill()` destroys the VM** — `close()` only drops the local
  control channel. Desktops need both `desktop.close()` and
  `client.destroy(id)`.
- **Profiles are not auto-applied or auto-saved.** Attach `profile_id`,
  then pass `browser.session.storage_state` into `new_context()`; call
  `profiles.save()` (or `POST /sessions/:id/save-profile`) at the end.
- **A hand-built context loses the pool's timezone pin.** With a managed
  proxy, pass `timezone_id = session.proxy.timezone_id` to `new_context()`.
- **`proxy` and `captcha` require `stealth=True`.**
- **Sandbox commands are not shell-interpreted.** `commands.run("sh",
  args=["-c", "..."])` for anything with pipes/redirects; plain argv
  otherwise.
- **`run_code` output is a list of result items** (`stdout`/`stderr`/
  `result`), not a single `.stdout` string.
- **Python `SandboxClient`/`DesktopClient` need `base_url` explicitly** —
  only the umbrella TS client defaults it.
- **In TypeScript, missing `await solari.close()` hangs the process.** The
  Python client doesn't have the same trap, but `aclose()` is provided and
  `demo.py` calls it.
- **Free-plan concurrency is 1 session.** Packages that would parallelize
  browsers run them sequentially; dispatch's desktop fallback detects the
  held slot and skips itself rather than deadlocking.

## Mock mode

`MockSolari` mirrors `SolariCore`'s API: "browsers" are real HTTP fetches
against the bundled FieldDesk portal or fixture pages (`use_pages` serves
fixture HTML through the same `PageDriver` path), "sandboxes" are local
subprocesses running the same worker scripts, "desktops" log the
computer-use actions. The pipeline code is identical — the output is
honest about which lines are real and which are "would" lines.

## Healthchecks

`healthcheck.py` runs each package's `health.py` — fixture pages and
selector maps are checked for drift (does the selector still match the
markup?), plus optional live-only checks. A ready-to-enable nightly
workflow ships at `docs/ci/nightly-healthcheck.yml` — copy it to
`.github/workflows/` on your fork to run the mock tier in CI.

## Adding a use case

`python new_usecase.py <name>` scaffolds `packages/<name>/` — an
`agent.py` with the contract, gate + metering + idempotency wired in, a
health module, a config stub, and a test. Register the name in
`demo.py`'s `PACKAGES`. For portal/supplier adapters, see
`docs/adapter-guide.md`.
