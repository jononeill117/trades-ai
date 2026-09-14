"""Render the leader's briefing — one markdown doc, every claim cited."""

from __future__ import annotations

from typing import Any


def briefing_md(week: str, agg: dict, themes: list[dict],
                improvements: list[dict], win_lines: list[str],
                coaching: dict[str, list[str]],
                talking_points: list[str]) -> str:
    t = agg["totals"]
    L = [f"# Weekly tech-meeting brief — {week}", "",
         f"_{t['jobs']} jobs · ${t['revenue']:,.0f} sold · "
         f"close rate {t['close_rate']:.0%} · {t['callbacks']} callbacks · "
         f"{t['reviews']} reviews, {t['surveys']} surveys_", ""]

    L.append("## Wins to celebrate")
    L += [f"- {w}" for w in win_lines] or ["- (none detected this week)"]

    L.append("\n## Friction to address")
    if themes:
        for th in themes:
            L.append(f"- **{th['theme']}** — {th['count']} mentions")
            L += [f"  - {e}" for e in th["evidence"]]
    else:
        L.append("- (no recurring friction themes detected)")

    L.append("\n## Areas of improvement")
    L += [f"- **{i['area']}:** {i['detail']}" for i in improvements] or \
         ["- (nothing below threshold this week)"]

    L.append("\n## Talking points for the meeting")
    L += [f"{i}. {p}" for i, p in enumerate(talking_points, 1)] or \
         ["1. Steady week — use the time on training backlog"]

    L.append("\n## Per-tech coaching notes")
    for tech, notes in coaching.items():
        pt = agg["per_tech"][tech]
        stats = (f"quota {pt['attainment']:.0%}" if pt.get("attainment") is not None
                 else "no quota")
        if pt.get("close_rate") is not None:
            stats += f" · close {pt['close_rate']:.0%}"
        stats += (f" · {pt['callbacks']} callbacks · "
                  f"{pt['five_star']} 5★")
        L.append(f"- **{tech}** ({stats})")
        L += [f"  - {n}" for n in notes]

    L.append("\n---")
    L.append("_Every number above is computed from the week's exports in a "
             "sandbox; themes cite the callback reasons, reviews, surveys, "
             "and lost-quote notes they came from._")
    return "\n".join(L) + "\n"
