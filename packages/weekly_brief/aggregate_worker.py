"""Sandbox worker — aggregate the week's shop data.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Reads the
six untrusted exports (jobs, quotas, callbacks, failed jobs, wins, reviews,
surveys) and computes every number the brief cites: revenue, close rates,
callback and warranty rates, quota attainment, review sentiment. Only
normalized JSON printed after @@RESULT@@ crosses back out — raw exports
stay inside the VM.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict

MARKER = "@@RESULT@@"
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(v):
    return CONTROL_RE.sub(" ", str(v)).strip() if v is not None else ""


def _rows(path):
    return [{k: _clean(v) for k, v in r.items()}
            for r in csv.DictReader(open(path, newline=""))]


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    # argv order: jobs quotas callbacks failed wins reviews.json surveys
    jobs = _rows(sys.argv[1])
    quotas = _rows(sys.argv[2])
    callbacks = _rows(sys.argv[3])
    failed = _rows(sys.argv[4])
    wins = _rows(sys.argv[5])
    reviews = json.loads(open(sys.argv[6]).read())
    surveys = _rows(sys.argv[7])

    sold = [j for j in jobs if j.get("sold") in ("1", "true", "yes")]
    revenue = round(sum(_f(j.get("amount")) for j in sold), 2)
    sentiment = Counter(str(int(_f(r.get("stars")))) for r in reviews
                        if r.get("stars"))
    low_texts = [_clean(r.get("text")) for r in reviews
                 if _f(r.get("stars")) <= 3]
    low_texts += [_clean(s.get("comment")) for s in surveys
                  if _f(s.get("rating")) <= 3]

    per_tech: dict[str, dict] = defaultdict(lambda: {
        "quoted": 0, "sold": 0, "revenue": 0.0, "quota": 0.0,
        "actual": 0.0, "callbacks": 0, "warranty": 0, "failed_quotes": 0,
        "failed_value": 0.0, "five_star": 0, "low_star": 0,
        "review_count": 0, "review_stars": 0.0, "wins": 0,
        "callback_reasons": [], "win_notes": []})

    for j in jobs:
        t = per_tech[_clean(j.get("tech")) or "?"]
        if j.get("quoted") in ("1", "true", "yes"):
            t["quoted"] += 1
        if j.get("sold") in ("1", "true", "yes"):
            t["sold"] += 1
            t["revenue"] += _f(j.get("amount"))
    for q in quotas:
        t = per_tech[_clean(q.get("tech")) or "?"]
        t["quota"] = _f(q.get("weekly_quota"))
        t["actual"] = _f(q.get("actual"))
    for c in callbacks:
        t = per_tech[_clean(c.get("tech")) or "?"]
        t["callbacks"] += 1
        if c.get("warranty") in ("1", "true", "yes"):
            t["warranty"] += 1
        t["callback_reasons"].append(_clean(c.get("reason")))
    for f in failed:
        t = per_tech[_clean(f.get("tech")) or "?"]
        t["failed_quotes"] += 1
        t["failed_value"] += _f(f.get("quote_amount"))
    for w in wins:
        t = per_tech[_clean(w.get("tech")) or "?"]
        t["wins"] += 1
        t["win_notes"].append({"job_id": _clean(w.get("job_id")),
                               "amount": _f(w.get("amount")),
                               "kind": _clean(w.get("kind")),
                               "note": _clean(w.get("note"))})
    for r in reviews:
        t = per_tech[_clean(r.get("tech")) or "?"]
        stars = _f(r.get("stars"))
        t["review_count"] += 1
        t["review_stars"] += stars
        if stars == 5:
            t["five_star"] += 1
        elif stars <= 3:
            t["low_star"] += 1

    techs = {}
    for name, t in per_tech.items():
        t["revenue"] = round(t["revenue"], 2)
        t["failed_value"] = round(t["failed_value"], 2)
        t["close_rate"] = round(t["sold"] / t["quoted"], 3) if t["quoted"] else None
        t["attainment"] = (round(t["actual"] / t["quota"], 3)
                           if t["quota"] else None)
        t["avg_stars"] = (round(t["review_stars"] / t["review_count"], 2)
                          if t["review_count"] else None)
        techs[name] = t

    print(MARKER + json.dumps({
        "totals": {
            "jobs": len(jobs), "quoted": sum(1 for j in jobs
                                            if j.get("quoted") in ("1", "true", "yes")),
            "sold": len(sold), "revenue": revenue,
            "close_rate": round(len(sold) / len(jobs), 3) if jobs else None,
            "callbacks": len(callbacks),
            "callback_rate": round(len(callbacks) / len(jobs), 3) if jobs else None,
            "warranty_calls": sum(1 for c in callbacks
                                  if c.get("warranty") in ("1", "true", "yes")),
            "failed_quotes": len(failed),
            "failed_value": round(sum(_f(f.get("quote_amount"))
                                      for f in failed), 2),
            "reviews": len(reviews),
            "surveys": len(surveys),
            "sentiment": dict(sorted(sentiment.items())),
        },
        "per_tech": techs,
        "callback_reasons": [_clean(c.get("reason")) for c in callbacks],
        "failed_reasons": [_clean(f.get("reason")) for f in failed],
        "low_star_texts": low_texts,
    }))


if __name__ == "__main__":
    main()
