#!/usr/bin/env python3
"""trades-ai demo runner.

    python demo.py --mock    runs ALL packages on bundled fixtures.
                             No API keys, no accounts — the same pipeline code
                             runs, with local stand-ins for Solari sessions.

    python demo.py --live    runs for real against Solari (needs
                             SOLARI_API_KEY in .env, plus whatever notify
                             channels you want).

    python demo.py --mock --only dispatch
    python demo.py --live --only procurement
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # run from anywhere

from core.config import load_dotenv

# Registry: CLI name -> python module under packages/. CLI names use dashes;
# module dirs use underscores.
PACKAGES = [
    "dispatch",
    "procurement",
    "missed-call-textback",
    "review-responder",
    "invoice-chaser",
    "quote-follower",
    "weekly-brief",
    "quote-builder",
    "photo-marketer",
]


async def run_package(name: str, mode: str) -> Path:
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    from core.solari_client import SolariCore

    module = importlib.import_module(f"packages.{name.replace('-', '_')}.agent")
    run_log = RunLog(name, mode)
    core = SolariCore() if mode == "live" else MockSolari()
    try:
        await module.run(core, run_log, {"mode": mode})
    finally:
        await core.aclose()
    return run_log.path


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="no keys needed (default)")
    group.add_argument("--live", action="store_true", help="real Solari sessions")
    parser.add_argument("--only", choices=PACKAGES, default=None)
    args = parser.parse_args()

    load_dotenv()
    mode = "live" if args.live else "mock"

    banner = (
        "\n=== trades-ai | mode: MOCK (local stand-ins, no keys) ===\n"
        if mode == "mock"
        else "\n=== trades-ai | mode: LIVE (real Solari sessions) ===\n"
    )
    print(banner)

    selected = [args.only] if args.only else PACKAGES
    results: dict[str, str] = {}
    for name in selected:
        try:
            path = await run_package(name, mode)
            results[name] = f"ok — {path}"
        except Exception as exc:
            results[name] = f"FAILED — {exc}"
        print(f"\n{name} run log: {results[name]}")

    print("\n=== summary ===")
    failures = 0
    for name, res in results.items():
        ok = res.startswith("ok")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {res if not ok else ''}")
    print("\nDone. Each line above is a step; 'plan' lines are what live mode "
          "would send. Run logs are in ./runs/ as JSONL.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
