"""Detect wins, friction, and improvement areas from the aggregates.

Every detector is a deterministic rule over the sandbox-produced numbers —
each finding cites the job ids, techs, or counts behind it so a leader can
verify every claim before walking into the meeting.
"""

from __future__ import annotations

import re
from typing import Any

# Friction themes: recurring reasons customers come back unhappy. Each theme
# is a keyword bucket over callback reasons and <=3-star review/survey text.
THEMES = {
    "pricing & quote accuracy": r"price|quote|invoice|fee|charge|blindsided",
    "water-heater installs": r"water heater|pilot|flue|relief valve",
    "scheduling & communication": r"schedul|call|reach|window|follow.?up|told|warn",
    "cleanup & care of the home": r"mud|mess|dust|cleanup|left|tracked",
    "repeat failures": r"again|still|keeps|second|not working|leak",
}


def friction_themes(agg: dict) -> list[dict[str, Any]]:
    """Rank recurring complaint themes with counts + example evidence."""
    corpus = ([("callback", t) for t in agg.get("callback_reasons", [])]
              + [("review/survey", t) for t in agg.get("low_star_texts", [])]
              + [("lost quote", t) for t in agg.get("failed_reasons", [])])
    themes = []
    for label, pattern in THEMES.items():
        hits = [(src, txt) for src, txt in corpus
                if re.search(pattern, txt, re.I)]
        if hits:
            themes.append({"theme": label, "count": len(hits),
                           "evidence": [f"{src}: {t[:110]}" for src, t in hits[:3]]})
    themes.sort(key=lambda t: -t["count"])
    return themes


def improvement_areas(agg: dict, cfg: dict) -> list[dict[str, str]]:
    """Company + per-tech improvement signals with the number behind each."""
    out = []
    quota_min = float(cfg.get("quota_attainment_min", 0.9))
    close_min = float(cfg.get("close_rate_min", 0.5))
    cb_max = float(cfg.get("callback_rate_max", 0.15))

    totals = agg["totals"]
    if totals.get("callback_rate") is not None and totals["callback_rate"] > cb_max:
        out.append({"area": "callback rate",
                    "detail": f"{totals['callback_rate']:.0%} of jobs came back "
                              f"({totals['callbacks']} of {totals['jobs']}) — "
                              f"above the {cb_max:.0%} line"})
    if totals.get("close_rate") is not None and totals["close_rate"] < close_min:
        out.append({"area": "company close rate",
                    "detail": f"{totals['close_rate']:.0%} overall — "
                              f"${totals['failed_value']:,.0f} quoted and lost"})

    for tech, t in sorted(agg["per_tech"].items()):
        if t.get("attainment") is not None and t["attainment"] < quota_min:
            out.append({"area": f"{tech} — quota",
                        "detail": f"{t['attainment']:.0%} of quota "
                                  f"(${t['actual']:,.0f} of ${t['quota']:,.0f})"})
        if t.get("close_rate") is not None and t["close_rate"] < close_min:
            out.append({"area": f"{tech} — close rate",
                        "detail": f"{t['close_rate']:.0%} ({t['sold']} of "
                                  f"{t['quoted']} quoted jobs sold)"})
        if t["callbacks"] >= 2:
            out.append({"area": f"{tech} — repeat callbacks",
                        "detail": f"{t['callbacks']} callbacks this week: "
                                  + "; ".join(t["callback_reasons"][:3])})
        if t["low_star"] >= 2:
            out.append({"area": f"{tech} — review trend",
                        "detail": f"{t['low_star']} reviews at 3 stars or below"})
    return out


def wins(agg: dict) -> list[str]:
    """Concrete things to celebrate — top closer, streaks, big tickets."""
    out = []
    per = agg["per_tech"]
    closers = [(t["close_rate"], name) for name, t in per.items()
               if t.get("close_rate") is not None and t["quoted"] >= 3]
    if closers:
        rate, name = max(closers)
        out.append(f"{name} closed {rate:.0%} of quoted work "
                   f"(${per[name]['revenue']:,.0f} sold)")
    for name, t in sorted(per.items()):
        if t["five_star"] >= 2:
            out.append(f"{name} earned {t['five_star']} five-star reviews "
                       f"this week")
        for w in t["win_notes"]:
            if w["kind"] in ("big_ticket", "upsell"):
                out.append(f"{name}: {w['note']} ({w['job_id']}, "
                           f"${w['amount']:,.0f})")
    return out


def coaching_notes(agg: dict, cfg: dict) -> dict[str, list[str]]:
    """Per-tech notes a leader can use in one-on-ones."""
    notes: dict[str, list[str]] = {}
    close_min = float(cfg.get("close_rate_min", 0.5))
    for tech, t in sorted(agg["per_tech"].items()):
        n = []
        if t.get("close_rate") is not None and t["close_rate"] < close_min:
            n.append(f"Close rate {t['close_rate']:.0%} — ride along on the "
                     f"next quoted job; review how options are presented")
        if t["callbacks"] >= 2:
            n.append(f"{t['callbacks']} callbacks — retrain on the recurring "
                     f"failure mode before next install")
        if t["low_star"] >= 2:
            n.append("Multiple low reviews — listen to the call recordings "
                     "and reset expectations on price communication")
        if t["five_star"] >= 2:
            n.append("On a 5-star streak — have them walk the team through "
                     "their handoff/explanation routine")
        if not n:
            n.append("Holding steady — no flags this week")
        notes[tech] = n
    return notes


def talking_points(agg: dict, themes: list[dict],
                   improvements: list[dict]) -> list[str]:
    """Concrete, per-topic lines to open with in the meeting."""
    pts = []
    for th in themes[:3]:
        pts.append(f"{th['theme'].capitalize()}: {th['count']} mentions this "
                   f"week — e.g. {th['evidence'][0]}")
    below = [i for i in improvements if "quota" in i["area"]
             or "close rate" in i["area"]]
    if below:
        pts.append("Sales: " + "; ".join(i["detail"] for i in below[:3]))
    cb = agg["totals"].get("callbacks", 0)
    if cb:
        pts.append(f"{cb} callbacks this week — pick one recurring reason "
                   f"and assign a retraining owner before Friday")
    return pts
