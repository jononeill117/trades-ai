# Architecture

trades-ai is a monorepo: one shared `core/` plus one folder per use case under
`packages/`. The rule that keeps it coherent: **use-case packages never import
the Solari SDKs directly** — they go through `core`.

```
                 ┌──────────────────────────────────────────────┐
                 │                    core/                      │
                 │  SolariCore ── browsers / sandboxes / desktops│
                 │  MockSolari ── local stand-ins for --mock     │
                 │  RunLog ───── JSONL audit log (runs/)         │
                 │  notify/ ──── Slack · Gmail · Quo SMS         │
                 │  drivers/ ─── PageDriver over Playwright/HTTP │
                 └───────▲───────────────────────▲──────────────┘
                         │                       │
              ┌──────────┴───────┐    ┌──────────┴────────┐
              │ packages/dispatch│    │packages/procurement│
              │ email -> booked  │    │ parts -> cheapest  │
              │      job         │    │      quote         │
              └──────────────────┘    └────────────────────┘
```

## The trust boundary

Untrusted content — inbound email, scraped supplier pages — is processed
**inside a Solari sandbox**, never by the orchestrator itself. The worker
scripts (`parse_worker.py`, `aggregate_worker.py`) are self-contained stdlib
files copied into the microVM; only their normalized JSON output (printed
after a `@@RESULT@@` marker) crosses back out. A prompt-injection attempt in
an email can write a weird field value — it cannot reach your disk, your
network, or your credentials, because those things simply aren't in the VM.

## Solari gotchas this codebase already handles

Read these before touching `core/solari_client.py` — each one cost time:

- **Recording is opt-in per session, at create time.** `recording=True` is
  our default everywhere; a session created without it can never produce a
  replay (the endpoint 404s forever).
- **Replays upload asynchronously after release.** Poll for ~30s after
  `browser.close()` before concluding there is no replay. The HTTP client
  already decompresses — don't `gzip.decompress()` the blob.
- **`browser.close()` releases the session.** Skip it and the slot is held
  until the plan deadline. Always try/finally.
- **`sandbox.kill()` destroys the VM** — `close()` only drops the local
  control channel. Desktops need both `desktop.close()` and
  `client.destroy(id)`.
- **Profiles are not auto-applied or auto-saved.** Attach `profile_id`, then
  pass `browser.session.storage_state` into `new_context()`; call
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

## Mock mode

`MockSolari` mirrors `SolariCore`'s API: "browsers" are real HTTP fetches
against the bundled FieldDesk portal or cached supplier pages, "sandboxes" are
local subprocesses running the same worker scripts, "desktops" log the
computer-use actions. The pipeline code is identical — the demo is honest
about which lines are real and which are "would" lines.

## Adding a use case

`python new_usecase.py <name>` scaffolds `packages/<name>/` — an `agent.py`
with the `(core, run_log, cfg)` contract, a config stub, and a test file. For
portal/supplier adapters, see `docs/adapter-guide.md`.
