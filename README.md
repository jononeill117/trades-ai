# trades-ai

The open-source AI automation toolkit for home-service trades — plumbing,
HVAC, electrical — built on [Solari](https://getsolari.com) cloud browsers,
sandboxed microVMs, and full Linux desktops.

Nine end-to-end automations over one shared `core/`. Every package is the
same shape: work comes in (an email, a missed call, an aging report), the
agent does the clicking and the math on Solari infrastructure, a human
approves anything customer-facing, and the result lands where the shop
actually looks — Slack, email, SMS — with a JSONL audit log and a recorded
session for every machine the agent touched.

Built for builders: automation agencies, technical ops people, and
developers who deploy this stuff for real shops. Everything here runs in
`--mock` with zero credentials, so you can evaluate, fork, and extend
before spending a cent.

**Entry for the [Pinetree Research SWE contest](https://x.com/harrychow_/status/1968397534577631408) — open-source use of Solari.**

## Architecture first

```
                 ┌────────────────────────────────────────────────┐
                 │                     core/                       │
                 │  SolariCore ── browsers / sandboxes / desktops  │
                 │  MockSolari ── local stand-ins for --mock       │
                 │  RunLog ───── JSONL audit log (runs/)           │
                 │  CostTracker ─ measured session usage + pricing │
                 │  ApprovalGate ─ fail-closed human gates         │
                 │  TrustLedger ── earned autonomy, owner-granted  │
                 │  notify/ ──── Slack · Gmail · Quo SMS           │
                 │  drivers/ ─── PageDriver over Playwright/HTTP   │
                 └───────▲───────────────────────────▲────────────┘
                         │                           │
        ┌────────────────┴───────────────────────────┴───────────┐
        │                  packages/<use-case>/                   │
        │   async def run(core, run_log, cfg) — same pipeline     │
        │   in mock and live; only where machines live changes    │
        └─────────────────────────────────────────────────────────┘
```

The one rule that keeps the monorepo coherent: **use-case packages never
import the Solari SDKs** — everything goes through `core/`. Details:
[docs/architecture.md](docs/architecture.md).

### The trust boundary

Untrusted input — inbound email, scraped HTML, uploaded photos — is
processed **inside a Solari sandbox**, never by the orchestrator. Worker
scripts are self-contained stdlib files copied into the microVM; only
normalized JSON printed after a `@@RESULT@@` marker crosses back out. A
prompt-injection attempt in a work-order email can produce a weird field
value — it cannot reach your disk, network, or credentials, because those
things aren't in the VM.

### The approval boundary

Every customer-facing or external action — sending a text, publishing a
review reply, delivering a quote, posting to social — goes through
`core/approvals.py` first. The gate is **fail-closed**: no decision
backend configured means the action is denied, logged, and the run moves
on. Backends: CLI prompt, Slack, mock decisions for tests, and a
`preapproved_actions` list for genuinely low-risk actions you choose to
auto-approve. Every request and decision lands in the run log.

### Progressive trust

Nobody hands customer-facing sends to a new system on day one. Every gated
action type starts at `gate` in `config/autonomy.yaml`; the trust ledger
(`out/trust_ledger.json`) records how each approval went — unchanged,
edited, denied. When an action's confidence crosses the bar, the system
**proposes** loosening — logged, echoed in run output, posted to ops — but
it never loosens itself. The owner grants or revokes per action type;
`auto` actions still log fully and are marked `trust_auto`. Philosophy and
config: [docs/trust.md](docs/trust.md).

### Cost instrumentation

Every run meters its Solari sessions (primitive, session id, wall-clock
seconds) and appends a `cost_summary` to the JSONL log. Rates live in
`config/pricing.yaml`; unset rates report `unavailable` rather than an
invented number. Summarize across runs:

```bash
python runs/cost-report.py                 # table of all runs
python runs/cost-report.py --package dispatch
```

## The nine packages

| Package | Pipeline | Solari primitives | Real constraint it solves |
| --- | --- | --- | --- |
| `dispatch` | work-order email → parse → schedule → book → notify | sandbox + browser (+ desktop fallback) | Untrusted email parsed in a microVM; booking needs a real logged-in browser; GUI-only portal steps get a desktop fallback |
| `procurement` | parts list → price-check suppliers → aggregate → quote | browser × N + sandbox | Supplier sites block bots; login-gated prices must not "win" a line |
| `missed-call-textback` | missed call → SMS textback → qualify → book → confirm | sandbox + browser | Speed-to-lead in minutes, but a human approves every outbound text |
| `review-responder` | reviews → draft replies → approve → publish → digest | sandbox + browser | Low-star replies need a human-edit lane; publishing is gated |
| `invoice-chaser` | aging AR + payments → match → escalate-by-days → remind | sandbox | Escalation tone is policy, not vibes; recovered revenue is counted only from real payment events |
| `quote-follower` | stale quotes → bucket → follow up → track | browser or CSV | "Sent but ghosted" vs "never sent" need different follow-ups |
| `weekly-brief` | week's exports → aggregate → detect friction/wins → leader's meeting brief | sandbox | The owner walks into the weekly tech meeting knowing wins, friction themes, and per-tech coaching notes — every claim cited to its source record |
| `quote-builder` | plain-English job description → history-trained pricing → full quote doc → deliver | sandbox | Prices come from the band of what the shop actually charged, every line cites its source jobs; exclusions, exploratory areas, risks, and terms are always on the page |
| `photo-marketer` | job photos → dedupe/clean → captions → approve → post | sandbox + browser | Exif/PII stripped in-sandbox; nothing posts without approval |

Per-package docs: `packages/<name>/README.md`. Adapter contracts:
[docs/adapter-guide.md](docs/adapter-guide.md). Deploying for a client:
[docs/deployment.md](docs/deployment.md).

## Run it — no keys needed

```bash
pip install -r requirements.txt   # PyYAML + the Solari SDKs
pip install pytest                # for the test suite

python demo.py --mock             # all nine packages, end to end
python demo.py --mock --only invoice-chaser
```

`--mock` runs every pipeline with zero credentials. "Browsers" are real
HTTP fetches against a bundled demo portal (FieldDesk, a tiny stdlib web
app in this repo) and fixture pages; "sandboxes" are real local
subprocesses running the *same* worker scripts; "desktops" log the
computer-use actions a real run would take. The pipeline code is identical
either way — only where the machines live changes. `[plan]`/`--` lines are
exactly what live mode would send, and approval requests are shown as
previews.

Every run appends a JSONL audit log to `runs/` and writes artifacts
(quotes, briefs, cleaned photos) to `out/`. Tests: `pytest`. Selector
health: `python healthcheck.py`.

## Run it live

```bash
cp .env.example .env        # then edit .env
# SOLARI_API_KEY=slr_live_...   from https://console.getsolari.com

python demo.py --live --only dispatch
```

`.env.example` documents every knob. All optional: `SLACK_WEBHOOK_URL`
posts summaries, `GMAIL_USER` + `GMAIL_APP_PASSWORD` (a Gmail *app
password*) sends mail and ingests real work-order email, `QUO_*` texts
through the Quo virtual phone system. Anything not configured is skipped
and logged as "would have sent" — the run still completes.

For sites that need a login, see `docs/adapter-guide.md`: you sign in once
through a human-handoff link and the Solari profile keeps every later run
authenticated. **Credentials never go in this repo or its config.**

## What actually happened live (2026-09-13)

All nine packages ran against real Solari infrastructure via the contest
launcher. Run logs: `runs/*.jsonl` (untracked, regenerate by running).
Replay manifest: [docs/demo/replays.json](docs/demo/replays.json).

- **dispatch** — two fictional work orders parsed in real sandboxes and
  booked through a real cloud browser into FieldDesk (`JOB-1001`,
  `JOB-1002`). Desktop fallback skipped itself on the free plan's
  one-session concurrency limit — logged, not hidden.
- **missed-call-textback** — four missed calls processed, three booked
  (`JOB-1001`…`JOB-1003`) in the live portal after CLI-approved textbacks;
  one escalated per policy.
- **procurement** — three parts checked across real supplier sites in
  sequential cloud browsers. Ferguson/SupplyHouse/Home Depot Pro block
  non-stealth automation: 9 offers found, 0 priced. The run completes
  honestly and the quote shows only what was actually obtainable.
- **invoice-chaser** — 6 invoices reconciled in a real sandbox, 4
  reminders approved via the gate (senders not configured → logged as
  would-send).
- **review-responder** — 5 reviews drafted and gated; publish step skipped
  with a logged reason because no real review platform is configured.
- **quote-follower** — fixture list URL detected; fell back to CSV source
  in live mode rather than scraping a fake domain.
- **weekly-brief, quote-builder, photo-marketer** — real sandbox runs
  (weekly-brief and quote-builder in their current form were run in mock;
  their predecessors ran live sandboxes on 2026-09-13); photo publish
  boundary not exercised (fixture composer URL).

**Replays.** Replays on this account have been unreliable — most sessions
return `ReplayUnavailable` (404, non-retryable) even after polling. One
real rrweb replay was captured: `docs/demo/*.replay.ndjson` (a procurement
browser session). The manifest records the true status of every session.
Nothing here is faked.

## Cost, honestly

- The software is MIT-licensed and free. Solari has a free tier; mock
  mode needs no credentials and spends nothing.
- The free tier is fine for evaluation and development. It allows **one
  concurrent session** and **no stealth, residential proxies, or captcha
  solving** — which is why the live procurement run above priced zero
  offers.
- Production volume — repeated browser workflows, concurrency, stealth —
  will likely need a paid Solari plan. Pricing and capabilities change;
  check the current terms.
- Every run reports measured session-seconds; dollar estimates appear
  only when you set rates in `config/pricing.yaml`. Where no rate is
  configured the report says `unavailable`. Costs vary with volume,
  retries, concurrency, desktop usage, and anti-bot features — run
  `runs/cost-report.py` against your own usage.

## Repo layout

```
core/            SolariCore + MockSolari, PageDriver, RunLog, CostTracker,
                 ApprovalGate, TrustLedger (progressive trust), notify/
packages/        nine use-case packages, each with adapters, tests, README
config/          YAML knobs — portals, suppliers, approvals, pricing, per-package
fixtures/        fictional work orders, reviews, AR aging, quote history, photos…
scripts/         collect_replays.py — honest replay/artifact collection
docs/            architecture.md · trust.md · adapter-guide.md ·
                 deployment.md · demo/ (replay evidence) · launch/
demo.py          the runner · new_usecase.py  the scaffolder
healthcheck.py   selector/fixture drift checks · runs/cost-report.py
```

## Add your own use case

```bash
python new_usecase.py my-automation
```

Scaffolds `packages/my_automation/` with the full contract —
`async def run(core, run_log, cfg)`, approval-gate usage, cost metering,
a health module, config stub, and a test. Register it in `demo.py`'s
`PACKAGES` list and it runs in `--mock` immediately.

## Notes

- Python ≥ 3.10. Dependencies are deliberately light: PyYAML + the Solari
  SDKs. Everything else is the standard library.
- All fixtures are fictional — `.example` domains, 555 numbers, invented
  people and companies.
- The community portal adapters (ServiceTitan, Housecall Pro, Jobber) are
  documented examples with placeholder selectors, not tested integrations
  or partnerships — fill in your tenant's selectors.
- Known limitations and what stays mock/fixture-backed in a default
  deployment are listed in [docs/deployment.md](docs/deployment.md).

## License

MIT — see [LICENSE](LICENSE).
