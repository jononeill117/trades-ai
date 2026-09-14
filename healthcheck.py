#!/usr/bin/env python3
"""Selector healthcheck — are the adapters' selectors still alive?

    python healthcheck.py            # mock mode: bundled fixtures only
    python healthcheck.py --live     # also check live targets that are configured
    python healthcheck.py --json out/health.json

Dead selectors are the gap between a demo and a deployable automation. Every
browser adapter registers checks in its package's `health.py`: a list of
entries {"name", "html"|"fetch", "selectors"|[...], "patterns"|{...}}.

- Mock mode (default): checks bundled fixture pages and the local mock
  portal. No credentials needed — this runs in CI nightly.
- Live mode (--live): additionally runs checks marked live=True against real
  targets, only when the adapter's config has a base_url (and any required
  env). Live checks are opt-in; unconfigured targets are reported "skipped".

Exit code is non-zero when any required check fails.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.drivers import _Page  # the same parser HttpDriver resolves against
from core.config import repo_root


def selector_resolves(page: _Page, selector: str) -> bool:
    """Mirror HttpDriver's resolution: #id, link text/href, form field."""
    if selector.startswith("#"):
        want = selector[1:]
        if want in page.by_id:
            return True
        for form in page.forms:
            for field in form["fields"].values():
                if field["id"] == want:
                    return True
        return False
    if selector.startswith("name="):
        name = selector[5:]
        return any(name in form["fields"] for form in page.forms)
    return any(l["text"] == selector or l["href"] == selector
               for l in page.links)


def check_entry(entry: dict, mode: str) -> dict:
    """Run one check entry -> {name, status, failures[]}."""
    name = entry.get("name", "?")
    live_only = entry.get("live", False)
    if live_only and mode != "live":
        return {"name": name, "status": "skipped", "reason": "live-only check", "failures": []}
    if entry.get("requires") and mode != "live":
        # e.g. needs a configured base_url — skip in mock
        return {"name": name, "status": "skipped", "reason": entry["requires"], "failures": []}

    try:
        if "fetch" in entry:
            html = entry["fetch"]()
        elif "html_text" in entry:
            html = entry["html_text"]
        else:
            p = Path(entry["html"])
            html = (p if p.is_absolute() else repo_root() / p).read_text()
    except Exception as exc:
        return {"name": name, "status": "error",
                "reason": f"could not load target: {exc}", "failures": ["<fetch>"]}

    page = _Page()
    page.feed(html)
    failures: list[str] = []
    for sel in entry.get("selectors", []):
        if not selector_resolves(page, sel):
            failures.append(sel)
    for label, pattern in (entry.get("patterns") or {}).items():
        if not re.search(pattern, html, re.S | re.I):
            failures.append(f"pattern:{label}")
    return {"name": name, "status": "fail" if failures else "ok",
            "failures": failures}


def discover_checks(mode: str) -> list[dict]:
    """Import every packages/*/health.py and collect its checks(mode)."""
    checks = []
    pkg_dir = repo_root() / "packages"
    for child in sorted(pkg_dir.iterdir()):
        health = child / "health.py"
        if not (child.is_dir() and health.exists()):
            continue
        try:
            mod = importlib.import_module(f"packages.{child.name}.health")
            for entry in mod.checks(mode):
                entry.setdefault("package", child.name)
                checks.append(entry)
        except Exception as exc:
            checks.append({"name": f"{child.name}:load", "package": child.name,
                           "status": "error", "reason": str(exc), "failures": ["<load>"]})
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="also run live-target checks that are configured")
    ap.add_argument("--json", dest="json_out", help="write machine-readable report here")
    args = ap.parse_args()
    mode = "live" if args.live else "mock"

    results = [check_entry(e, mode) for e in discover_checks(mode)]
    failed = [r for r in results if r["status"] in ("fail", "error")]
    skipped = [r for r in results if r["status"] == "skipped"]
    passed = [r for r in results if r["status"] == "ok"]

    for r in results:
        if r["status"] == "ok":
            print(f"  ok   {r['name']}")
        elif r["status"] == "skipped":
            print(f"  --   {r['name']} ({r.get('reason', 'skipped')})")
        else:
            print(f" FAIL  {r['name']}: {', '.join(r['failures'])}"
                  + (f" — {r.get('reason')}" if r.get("reason") else ""))
    print(f"\n{len(passed)} ok, {len(skipped)} skipped, {len(failed)} failed"
          f"  (mode={mode})")

    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mode": mode,
              "ok": len(passed), "skipped": len(skipped), "failed": len(failed),
              "checks": results}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
        print(f"report written to {args.json_out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
