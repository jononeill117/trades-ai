# Client deployment runbook

How to take a shop from "git clone" to running automations, and what to
tell them up front. Audience: the agency owner or developer doing the
deploy, not the shop owner.

## 0. Evaluate first (free, no credentials)

```bash
pip install -r requirements.txt pytest
python demo.py --mock                # all nine pipelines, zero keys
python healthcheck.py
pytest
```

Mock mode runs the real pipeline code against fixtures and local
subprocess sandboxes. If the package doesn't do what the shop needs in
mock, it won't in live either.

## 1. Solari account

- Free tier: fine for development and low-volume live use.
  **One concurrent session** — the packages already serialize browsers.
  **No stealth / residential proxy / captcha solving** — expect
  bot-blocking on hostile targets (the live procurement run priced 0
  offers on free plan for exactly this reason).
- Paid plan: needed for production browser volume, concurrency, stealth.
  Pricing changes — check current terms; this repo makes no claims about
  Solari pricing. Set rates you're quoted into `config/pricing.yaml` and
  `runs/cost-report.py` will turn measured usage into dollar estimates.
- Get `SOLARI_API_KEY` from https://console.getsolari.com. It goes in the
  client's `.env` — never in the repo, never in `config/*.yaml`.

## 2. Configure the client

`cp .env.example .env` and fill in what the client actually has:

| Env | Enables | Needed by |
| --- | --- | --- |
| `SOLARI_API_KEY` | all live sessions | every package |
| `SLACK_WEBHOOK_URL` | ops digests + approval channel | most packages |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | email send + work-order ingest | dispatch, invoice-chaser, quote-follower, quote-builder, meeting-prep |
| `QUO_*` | SMS via Quo | dispatch, missed-call-textback, quote-follower |

Then `config/*.yaml` per package: portal URLs + selectors, supplier list,
escalation policy, pricebook path, approval rules. Anything not
configured degrades to a logged "would have sent" — safe to roll out
incrementally.

## 3. Logins (profiles + handoff)

For any target behind a login, use Solari profiles — never store
passwords in config:

1. Set `profile: <name>` in the package config.
2. Run once live; when the session isn't authenticated the adapter should
   mint a handoff link (`core.login_handoff_url`).
3. A human at the client opens the link, signs in (2FA included), the
   profile saves via `core.save_profile_from_session`.
4. Every later run starts authenticated.

## 4. Approvals

Decide the client's posture in `config/approvals.yaml`:

- `cli` backend for a supervised go-live.
- `slack` backend once they want approvals in their ops channel.
- `preapproved_actions` only for actions the client explicitly accepts
  as auto-approved (e.g. internal digests). Customer-facing sends should
  stay human-gated until trust is earned — the gate fails closed, so the
  default is safe.

## 5. Schedule

Packages are idempotent batch jobs (state files under `out/`), so cron is
enough:

```cron
*/15 * * * *  cd /opt/trades-ai && .venv/bin/python demo.py --live --only missed-call-textback
0   7 * * *   cd /opt/trades-ai && .venv/bin/python demo.py --live --only meeting-prep
0   9 * * 1-5 cd /opt/trades-ai && .venv/bin/python demo.py --live --only invoice-chaser
```

Keep runs sequential on a free plan (one concurrent session).

## 6. Operate

- Audit: `runs/*.jsonl` — every step, every approval, every session id.
- Cost: `python runs/cost-report.py` — measured session-seconds always;
  dollar estimates when `config/pricing.yaml` has rates, `unavailable`
  otherwise.
- Drift: `python healthcheck.py` (a ready-to-enable nightly GitHub
  Actions workflow ships at `docs/ci/nightly-healthcheck.yml` — copy it
  into `.github/workflows/`).
  A failing selector check means a target site changed its markup —
  update the selector map in `config/`, not the code.
- Replays: `scripts/collect_replays.py` polls replay URLs and saves what
  actually exists to `docs/demo/`. Replays can be `ReplayUnavailable` —
  that's a platform truth, not a bug in your deploy.

## What stays fixture/mock-backed unless you configure it

- Real FSM/review/social targets: `portal_base_url`, `list_url`,
  `composer_url`, review-platform config. Unset → the package logs that
  the boundary wasn't exercised rather than pretending.
- Notification channels without env vars log "would have sent".
- The FieldDesk portal is a fictional demo portal. Real portals need a
  selector map (`config/portals.<name>.yaml`) or an adapter class.

## Incident notes

- Provider blocks/rate-limits: stop that provider's requests, log it,
  tell the client — don't hammer retries. On free plan, expect
  bot-blocking on large retail/supplier sites.
- Approval backend unreachable: actions deny (fail-closed). The run log
  shows every denied request for replay later.
