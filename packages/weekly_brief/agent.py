"""Weekly brief — the week's shop data in, a leader's meeting brief out.

Pipeline:
    ingest the week's exports (reviews, surveys, quotas vs actuals,
    callbacks/warranty calls, failed quotes, wins)
    -> aggregate (sandbox) -> detect friction themes, improvement areas,
    wins -> render the leader's briefing (Markdown) -> Slack digest

Solari primitive: SANDBOX — exports are untrusted files; aggregation runs
inside a disposable VM and only computed JSON comes back out.

This package is INTERNAL: it never contacts a customer, so no approval gate
is needed for the brief itself. (If it ever drafts external messages those
go through the gate like everything else.) Idempotent: one brief per week
label — a re-run for the same week rewrites the doc but does not re-notify.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.notify import SlackNotifier

from .aggregate_worker import MARKER
from .insights import (coaching_notes, friction_themes, improvement_areas,
                       talking_points, wins)
from .render import briefing_md

WORKER = Path(__file__).parent / "aggregate_worker.py"
FIXTURES = Path(__file__).parent / "fixtures"

INPUTS = ["jobs.csv", "quotas.csv", "callbacks.csv", "failed_jobs.csv",
          "wins.csv", "reviews.json", "surveys.csv"]
STATE = "out/weekly_brief_state.json"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "weekly_brief.yaml")


def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"briefs_delivered": []}


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    week = cfg.get("week", "2026-W37")
    src = repo_root() / cfg.get("fixtures", "packages/weekly_brief/fixtures")
    state_path = repo_root() / STATE
    state = _load_state(state_path)

    # -- ingest ------------------------------------------------------------------
    paths = {name: src / name for name in INPUTS}
    for name, p in paths.items():
        if not p.exists():
            raise FileNotFoundError(f"weekly-brief input missing: {p}")
    run_log.step("ingest", files=list(paths), source=str(src))

    # -- aggregate in the sandbox -------------------------------------------------
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(
        str(WORKER), {str(p): f"/tmp/{name}" for name, p in paths.items()})
    run_log.usage("sandbox", "weekly-aggregate", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"aggregate worker produced no marker: {stdout!r}")
    agg = json.loads(stdout[idx + len(MARKER):])
    run_log.step("aggregate", **{k: v for k, v in agg["totals"].items()
                                 if not isinstance(v, dict)},
                 techs=sorted(agg["per_tech"]))

    # -- detect -------------------------------------------------------------------
    themes = friction_themes(agg)
    improvements = improvement_areas(agg, cfg)
    win_lines = wins(agg)
    coaching = coaching_notes(agg, cfg)
    points = talking_points(agg, themes, improvements)
    run_log.step("detect", themes=[t["theme"] for t in themes],
                 improvements=[i["area"] for i in improvements],
                 wins=len(win_lines))

    # -- render --------------------------------------------------------------------
    md = briefing_md(week, agg, themes, improvements, win_lines,
                     coaching, points)
    out_dir = repo_root() / "out"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / f"weekly-brief-{week}-{run_log.run_id}.md"
    md_path.write_text(md)
    run_log.step("render", file=md_path.name)

    # -- notify (internal; idempotent per week) -------------------------------------
    already = week in state["briefs_delivered"]
    if already:
        run_log.step("notify_slack", status="skipped",
                     reason=f"brief for {week} already delivered — doc rewritten, "
                            "no duplicate send")
    else:
        res = await SlackNotifier().send(
            f":clipboard: Weekly brief {week} ready — "
            f"{len(win_lines)} wins, {len(themes)} friction themes, "
            f"{len(improvements)} improvement areas")
        run_log.step("notify_slack", status=res.status)
        state["briefs_delivered"].append(week)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))

    run_log.finish("ok", week=week, brief=str(md_path), notified=not already,
                   friction_themes=len(themes),
                   improvement_areas=len(improvements))
    return {"week": week, "brief": str(md_path), "aggregates": agg,
            "themes": themes, "improvements": improvements,
            "wins": win_lines, "coaching": coaching,
            "notified": not already}
