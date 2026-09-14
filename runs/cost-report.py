#!/usr/bin/env python3
"""Cost report — summarize measured Solari usage and estimated cost per run.

    python runs/cost-report.py                # table, all runs in runs/
    python runs/cost-report.py --package dispatch
    python runs/cost-report.py --json         # machine-readable

Reads every runs/*.jsonl, reports session counts and session-seconds per
primitive (measured, always available) plus an estimated cost when the rates
in config/pricing.yaml are configured. Rates left as null -> "unavailable".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cost import Pricing, summarize_run


def iter_runs(runs_dir: Path, package: str | None = None):
    for path in sorted(runs_dir.glob("*.jsonl")):
        if package and not path.name.startswith(package + "-"):
            continue
        events = []
        for line in path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if events:
            yield events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", help="only this use case (e.g. dispatch)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--runs-dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args()

    pricing = Pricing.load()
    runs = []
    for events in iter_runs(Path(args.runs_dir), args.package):
        summary = summarize_run(events, pricing)
        summary["use_case"] = events[0].get("use_case", "?")
        summary["mode"] = events[0].get("detail", {}).get("mode", "?")
        runs.append(summary)

    if args.json:
        print(json.dumps({"pricing": {"currency": pricing.currency,
                                      "rates_per_minute": pricing.rates,
                                      "note": pricing.note},
                          "runs": runs}, indent=2))
        return 0

    if pricing.note:
        print(f"pricing: {pricing.note}")
    print(f"{'run':<44} {'mode':<6} {'sessions':<28} {'seconds':<28} {'est. cost'}")
    print("-" * 116)
    for r in runs:
        sess = ", ".join(f"{k}x{v}" for k, v in sorted(r["sessions"].items())) or "-"
        secs = ", ".join(f"{k}:{v}s" for k, v in sorted(r["seconds"].items())) or "-"
        est = r["estimated_total"]
        est = f"${est}" if isinstance(est, (int, float)) else str(est)
        print(f"{r['run']:<44} {r['mode']:<6} {sess:<28} {secs:<28} {est}")
    if not runs:
        print("(no run logs found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
