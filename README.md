# trades-ai

Open-source AI automations for home-service trades — plumbing, HVAC, electrical —
built on the [Solari](https://getsolari.com) platform (cloud browsers, sandboxed
microVMs, and full Linux desktops).

A monorepo with a shared `core/` and one package per use case. Each use case is
a real, end-to-end pipeline: work comes in (email, a parts list), the agent does
the clicking and the math on Solari infrastructure, and the result lands where
the shop actually looks — Slack, email, SMS — with a recorded session for every
machine the agent touched.

**Entry for the [Pinetree Research SWE contest](https://x.com/harrychow_/status/1968397534577631408).**

## The two use cases

### `packages/dispatch` — work-order email in, booked job out

```
ingest → parse (sandbox) → schedule → book (cloud browser)
       → confirm (desktop fallback) → notify → audit
```

Reads work-order emails (bundled fixtures, or a real Gmail inbox over IMAP),
parses them inside a Solari sandbox — untrusted input never touches the host —
proposes a slot from your availability rules, then drives a field-service
portal in a cloud browser to book the job. When a portal insists on a GUI-only
step (our demo portal's "dispatch board"), the run falls back to a Solari
desktop: a real Linux machine driven by screenshot + mouse/keyboard. Slack,
email, and SMS notifications go out; every step lands in a JSONL run log.

### `packages/procurement` — parts list in, cheapest compliant quote out

```
load parts → price-check every supplier (parallel cloud browsers)
           → aggregate (sandbox) → render quote → deliver
```

Takes a parts CSV/JSON, checks prices across suppliers in cloud
browsers (sequential in live mode — Solari free-plan accounts allow one
concurrent session; stealth + managed residential proxy + captcha solving
are opt-in via `SOLARI_STEALTH=1` on a paid plan),
aggregates the offers inside a sandbox — a login-gated price can't win a line,
and savings vs. your baseline supplier are computed honestly — then renders a
Markdown/HTML quote and delivers it.

## How Solari is used

| Primitive | What it does here |
| --- | --- |
| **Cloud browsers** | Portal booking, supplier price-checks. Sessions are created with `recording=True` — the rrweb replay is both the demo video and the audit trail. Browser profiles hold logins server-side so a human signs in once (via a handoff link) and every later run starts authenticated. |
| **Sandboxes** | The untrusted-input boundary. Raw emails and scraped HTML are processed inside disposable microVMs by self-contained stdlib worker scripts (`parse_worker.py`, `aggregate_worker.py`); only normalized JSON crosses back out. In live mode the demo portal itself runs inside a sandbox behind a preview URL so a cloud browser can reach it. |
| **Desktops** | The fallback for steps a browser can't do — native apps, OS dialogs, GUI-only confirmations. A real Linux desktop driven by computer-use, recorded to mp4, with a live VNC `streamUrl` in the run log. |

`docs/architecture.md` explains the layering rule (use-case packages never
import the Solari SDKs — everything goes through `core/`) and the Solari
gotchas this codebase already handles.

## Run it — no keys needed

```bash
pip install -r requirements.txt   # PyYAML + the Solari SDKs
pip install pytest                # for the test suite

python demo.py --mock
```

`--mock` runs **both** pipelines end to end with zero credentials. "Browsers"
are real HTTP fetches against a bundled demo portal (FieldDesk, a tiny stdlib
web app in this repo) and cached supplier pages; "sandboxes" are real local
subprocesses running the *same* worker scripts; "desktops" log the computer-use
actions a real run would take. The pipeline code is identical either way —
only where the machines live changes. `[plan]`/`--` lines in the output are
exactly what live mode would send.

Run one use case at a time:

```bash
python demo.py --mock --only dispatch
python demo.py --mock --only procurement
```

Every run appends a JSONL audit log to `runs/` and writes quote reports to
`out/`. Tests: `pytest`.

## Run it live

```bash
cp .env.example .env        # then edit .env
# SOLARI_API_KEY=slr_live_...   from https://console.getsolari.com

python demo.py --live
```

`.env.example` documents every knob. All optional: `SLACK_WEBHOOK_URL` posts
summaries to an ops channel, `GMAIL_USER` + `GMAIL_APP_PASSWORD` (a Gmail *app
password*) send customer confirmations and ingest real work-order mail,
`QUO_*` texts customers through the Quo virtual phone system. Anything not
configured is skipped and logged as "would have sent" — a run still completes.

Live mode needs no other code changes: the same adapters drive real portal
and supplier sites through cloud browsers. For sites that need a login, see
`docs/adapter-guide.md` — you sign in once through a human-handoff link and
the profile keeps you authenticated forever after. **Credentials never go in
this repo or its config.**

## Live demo results

Both pipelines have been run end to end against real Solari infrastructure
(2026-09-13). The dispatch pipeline is fully live; procurement runs live
with a documented free-plan limitation.

**Dispatch (live):** Two fictional work-order emails were ingested, parsed
inside real Solari sandboxes, scheduled, and booked through a real Solari
cloud browser driving a demo portal (FieldDesk) hosted in a Solari sandbox.
Both jobs were created: `JOB-1001` (plumbing) and `JOB-1002` (HVAC). The
desktop fallback for the GUI-only confirmation step gracefully skips on
Solari free-plan accounts (concurrency limit of 1) — the job is already
booked via the browser, so this is a demo flourish, not a correctness issue.

**Procurement (live):** Three parts were price-checked across suppliers using
real Solari cloud browsers (sequential on free-plan accounts to respect the
concurrency limit), aggregated in a Solari sandbox, and rendered to
Markdown/HTML quotes. Note: supplier websites (Ferguson, SupplyHouse, Home
Depot Pro) aggressively block non-stealth browsers; Solari's stealth mode +
residential proxy + captcha solving require a paid plan (HTTP 402 on free).
Set `SOLARI_STEALTH=1` to enable the full stealth stack on a paid plan.

## Repo layout

```
core/            SolariCore (live) + MockSolari, PageDriver, RunLog, notify/
packages/
  dispatch/      ingest → parse → schedule → book → confirm → notify
    portals/     adapter interface + FieldDesk (working) + ST/HCP/Jobber examples
  procurement/   parts → price-check → aggregate → quote → deliver
    suppliers/   regex/config-driven adapters (Ferguson, SupplyHouse, HD Pro)
config/          YAML knobs — availability, portal selectors, suppliers
fixtures/        fictional work orders, parts lists, supplier pages, portal seed
docs/            architecture.md · adapter-guide.md · demo/ (replays)
demo.py          the runner · new_usecase.py  the scaffolder
```

## Add your own use case

```bash
python new_usecase.py review-responder
```

Scaffolds `packages/review_responder/` with the contract every package
follows — `async def run(core, run_log, cfg)` — plus a config stub and a test
file. `core` gives you browsers, sandboxes, desktops, notifications, and the
audit log for free. Then wire it into `demo.py`. Adapter authors: read
`docs/adapter-guide.md` — a new portal or supplier is usually YAML selectors,
not Python.

## Notes

- Python ≥ 3.10. Dependencies are deliberately light: PyYAML + the Solari
  SDKs. Everything else is the standard library.
- All fixtures are fictional — `.example` domains, 555 numbers, invented
  people and companies.
- The community portal adapters (ServiceTitan, Housecall Pro, Jobber) are
  documented examples with placeholder selectors, not tested integrations —
  fill in your tenant's selectors in `config/portals.<name>.yaml`.

## License

MIT — see [LICENSE](LICENSE).
