"""Render — one page per tech (Markdown + printable HTML) and a shop summary.

Every flag cites its job_id; every section lists the jobs it summarizes so a
manager can verify every claim against the source records.
"""

from __future__ import annotations

import html
from typing import Any

from .flags import coach_notes


def tech_brief_md(tech: str, jobs: list[dict], flags: list[dict]) -> str:
    mine = [j for j in jobs if j.get("tech") == tech]
    my_flags = [f for f in flags if f["job_id"] in {j["job_id"] for j in mine}]
    lines = [f"# Tech brief — {tech}",
             f"_{len(mine)} jobs this week — "
             f"${sum(j.get('amount', 0) for j in mine):,.2f} billed_\n"]
    lines.append("## Jobs")
    for j in mine:
        lines.append(f"- **{j['job_id']}** ({j.get('date','')}) "
                     f"{j.get('summary','')} — {j.get('customer','')} — "
                     f"${j.get('amount',0):,.2f} — status: {j.get('status','')}")
    if my_flags:
        lines.append("\n## Flags")
        for f in my_flags:
            lines.append(f"- `{f['flag']}` — {f['detail']} _(source: {f['job_id']})_")
    coaching = coach_notes(tech, jobs, flags)
    if coaching:
        lines.append("\n## Coaching")
        lines += [f"- {c}" for c in coaching]
    return "\n".join(lines) + "\n"


def shop_summary_md(jobs: list[dict], flags: list[dict]) -> str:
    techs = sorted({j.get("tech", "?") for j in jobs})
    lines = ["# Shop summary — week in review",
             f"_{len(jobs)} jobs · {len(techs)} techs · "
             f"${sum(j.get('amount', 0) for j in jobs):,.2f} billed_\n",
             "| Tech | Jobs | Billed | Flags |",
             "| --- | --- | --- | --- |"]
    for t in techs:
        mine = [j for j in jobs if j.get("tech") == t]
        my_flags = [f for f in flags
                    if f["job_id"] in {j["job_id"] for j in mine}]
        lines.append(f"| {t} | {len(mine)} | "
                     f"${sum(j.get('amount',0) for j in mine):,.2f} | "
                     f"{', '.join(sorted({f['flag'] for f in my_flags})) or '—'} |")
    return "\n".join(lines) + "\n"


def to_html(md: str, title: str = "meeting prep") -> str:
    """Dead-simple printable HTML wrapper — enough for a one-pager."""
    body = []
    for line in md.splitlines():
        e = html.escape(line)
        if e.startswith("# "):
            body.append(f"<h1>{e[2:]}</h1>")
        elif e.startswith("## "):
            body.append(f"<h2>{e[3:]}</h2>")
        elif e.startswith("- "):
            body.append(f"<li>{e[2:]}</li>")
        elif e.startswith("|"):
            body.append(f"<pre>{e}</pre>")
        elif e.strip():
            body.append(f"<p>{e}</p>")
    return ("<!doctype html><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font-family:system-ui;max-width:720px;margin:2em auto;"
            "padding:0 1em} li{margin:.3em 0} pre{margin:0}</style>"
            + "\n".join(body))
