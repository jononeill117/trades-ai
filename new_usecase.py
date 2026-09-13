#!/usr/bin/env python3
"""Scaffold a new use-case package:  python new_usecase.py review-responder

Creates packages/<name>/ with the skeleton every package follows — an
agent.py that takes (core, run_log, cfg), a README, a test file, and a config
stub. Fill in the steps; the core gives you browsers, sandboxes, desktops,
notifications, and the run log for free.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent

AGENT = '''"""{title} — describe the workflow here.

Pipeline shape (keep it): steps land in the run log as they happen, Solari
sessions are created through `core` (recording on by default), and everything
untrusted is processed inside a sandbox.
"""

from __future__ import annotations

from core.audit import RunLog


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = cfg or {{}}
    run_log.step("start", package="{name}")
    # TODO: your pipeline here. Examples:
    #   session, page = await core.browser(recording=True)   # cloud browser
    #   out = await core.run_python_in_sandbox(worker, files) # untrusted input
    #   desk = await core.desktop(record=True)               # GUI fallback
    #   await SlackNotifier().send("...")                    # notify
    run_log.finish("ok")
    return {{"status": "ok"}}
'''

README = '''# {title}

One line: what it does for a shop owner.

## How it works

1. Step one (plain language)
2. Step two

## Config

`config/{name}.yaml` — describe each knob in one line.

## Run it

```bash
python demo.py --mock        # bundled fixtures, no keys
python demo.py --live        # real Solari sessions (needs .env)
```
'''

TEST = '''"""Tests for {name} — keep pure logic (parsing, scheduling, math) here."""


def test_placeholder():
    assert True
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
    (pkg / "README.md").write_text(README.format(title=title, name=name))
    (pkg / "tests" / f"test_{name}.py").write_text(TEST.format(name=name))
    cfg = ROOT / "config" / f"{name}.yaml"
    cfg.write_text(f"# {title} config — one line per knob.\n")
    print(f"created {pkg}/ (+ config/{name}.yaml)")
    print("next: write your pipeline in agent.py, wire it into demo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
