#!/usr/bin/env python3
"""trades-ai demo runner.

    python demo.py --mock    runs BOTH use cases on bundled fixtures.
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # run from anywhere

from core.config import load_dotenv


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="no keys needed (default)")
    group.add_argument("--live", action="store_true", help="real Solari sessions")
    parser.add_argument("--only", choices=["dispatch", "procurement"], default=None)
    args = parser.parse_args()

    load_dotenv()
    mode = "live" if args.live else "mock"

    banner = (
        "\n=== trades-ai | mode: MOCK (local stand-ins, no keys) ===\n"
        if mode == "mock"
        else "\n=== trades-ai | mode: LIVE (real Solari sessions) ===\n"
    )
    print(banner)

    ran = []
    if args.only in (None, "dispatch"):
        from packages.dispatch import agent as dispatch_agent
        from core.audit import RunLog
        from core.mock_solari import MockSolari
        from core.solari_client import SolariCore

        run_log = RunLog("dispatch", mode)
        core = SolariCore() if mode == "live" else MockSolari()
        try:
            await dispatch_agent.run(core, run_log, {"mode": mode})
        finally:
            await core.aclose()
        print(f"\ndispatch run log: {run_log.path}")
        ran.append(run_log.path)

    if args.only in (None, "procurement"):
        from packages.procurement import agent as procurement_agent
        from core.audit import RunLog
        from core.mock_solari import MockSolari
        from core.solari_client import SolariCore

        run_log = RunLog("procurement", mode)
        core = SolariCore() if mode == "live" else MockSolari()
        try:
            await procurement_agent.run(core, run_log, {"mode": mode})
        finally:
            await core.aclose()
        print(f"\nprocurement run log: {run_log.path}")
        ran.append(run_log.path)

    print("\nDone. Each line above is a step; 'plan' lines are what live mode "
          "would send. Run logs are in ./runs/ as JSONL.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
