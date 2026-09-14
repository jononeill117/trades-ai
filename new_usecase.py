#!/usr/bin/env python3
"""Scaffold a new use-case package:  python new_usecase.py review-responder

Creates packages/<name>/ following the package contract:
  - agent.py with `async def run(core, run_log, cfg)` — mock/live identical,
    Solari through `core` only, usage metering, approval-gated outbound
    actions, fail-closed behavior
  - health.py with selector checks for healthcheck.py
  - a fictional-fixture-aware test file, a README, and a config/<name>.yaml
Then register the package in demo.py's PACKAGES list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent

AGENT = '''"""{title} — describe the workflow here.

Package contract (keep it):
- `async def run(core, run_log, cfg)` — the SAME pipeline in mock and live;
  only the infrastructure boundary changes (MockSolari vs SolariCore).
- Every Solari session through `core` (recording on by default), timed with
  run_log.time_session / run_log.usage so cost-report.py can measure it.
- Every customer-facing outbound action through the approval gate — no
  approval, no send. Untrusted inputs go through core.run_python_in_sandbox.
- Idempotent: persist processed ids so a retry never double-acts.
"""

from __future__ import annotations

import time

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "{name}.yaml")


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {{**_cfg(), **(cfg or {{}})}}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    run_log.step("start", package="{name}")

    # -- examples -------------------------------------------------------------
    # Cloud browser (recorded, metered):
    #   session, page = await core.browser(recording=True)
    #   run_log.session("browser", session.id)
    #   t0 = time.monotonic()
    #   try:
    #       driver = as_driver(page); ...
    #   finally:
    #       await session.close()
    #       run_log.usage("browser", session.id, time.monotonic() - t0)
    #       run_log.session("browser", session.id,
    #                       replay_url=await core.replay_url(session.id))
    #
    # Sandbox on untrusted input:
    #   out = await core.run_python_in_sandbox(worker, {{local: "/tmp/in"}})
    #
    # Approval before anything customer-facing:
    #   ok = await gate.require("email.send", "gmail",
    #                           "one-line summary", payload={{...}},
    #                           requester="{name}")
    #   if ok: await GmailNotifier().send(...)
    #   else:  run_log.step("send", status="skipped", reason="denied")

    run_log.finish("ok")
    return {{"status": "ok"}}
'''

HEALTH = '''"""Selector-health checks for {title} — registered with healthcheck.py.

Each entry: {{"name", "html"|"html_text"|"fetch", "selectors"|[...],
"patterns"|{{label: regex}}}}. Mock entries check bundled fixtures; mark
live-only entries with "live": True.
"""

from __future__ import annotations


def checks(mode: str) -> list[dict]:
    return []
'''

README = '''# {name}

One line: the business job this automates.

## Pipeline

```
input -> steps -> approval gate -> output
```

## Why Solari

Which primitive (cloud browser / sandbox / desktop) and the real constraint
it solves — not decoration.

## Config — `config/{name}.yaml`

One line per knob.

## Approval points

Which actions are gated and what the request shows.

## Run it

```bash
python demo.py --mock --only {cli}
python demo.py --live --only {cli}
```

## Cost profile

Measured sessions/durations land in the run log — `runs/cost-report.py`.

## Known limitations

- Be honest.
'''

TEST = '''"""Tests for {name} — pure logic plus a deterministic end-to-end mock run."""

from __future__ import annotations

import asyncio
import json


def _run(coro):
    return asyncio.run(coro)


def test_mock_run_end_to_end(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    from packages.{name} import agent

    log = RunLog("{name}", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {{"mode": "mock"}}))
    assert result.get("status") == "ok"
'''

INIT = '"""{title} — a trades-ai use-case package."""\n'


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z][a-z0-9_-]*", sys.argv[1]):
        print("usage: python new_usecase.py <use-case-name>   (lowercase, dashes ok)")
        return 2
    name = sys.argv[1].replace("-", "_")
    title = name.replace("_", " ").title()
    pkg = ROOT / "packages" / name
    if pkg.exists():
        print(f"{pkg} already exists")
        return 1
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text(INIT.format(title=title))
    (pkg / "agent.py").write_text(AGENT.format(title=title, name=name))
    (pkg / "health.py").write_text(HEALTH.format(title=title))
    (pkg / "README.md").write_text(README.format(
        title=title, name=name, cli=sys.argv[1]))
    (pkg / "tests" / f"test_{name}.py").write_text(TEST.format(name=name))
    cfg = ROOT / "config" / f"{name}.yaml"
    cfg.write_text(f"# {title} config — one line per knob.\n")
    print(f"created {pkg}/ (+ config/{name}.yaml)")
    print("next: write your pipeline in agent.py, add the CLI name to "
          "PACKAGES in demo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
