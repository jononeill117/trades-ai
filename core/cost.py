"""Cost-per-run instrumentation.

Every run logs what it actually consumed — which Solari primitives, how many
sessions, and how long each session lived — and separately an *estimated* cost
computed from configurable pricing assumptions in `config/pricing.yaml`.

The split matters: measured usage is fact and is always logged. Estimated
cost is only as good as the configured rates — when a rate isn't configured
the estimate is reported as `unavailable`, never invented. Solari pricing can
change; treat pricing.yaml as "what we believe a unit costs", verify it
against https://getsolari.com docs before quoting it to anyone.

JSONL schema note: usage and cost events are additive — old logs without them
still parse, and old parsers ignore unknown `step` values.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import load_yaml, repo_root

PRICING_CONFIG = "config/pricing.yaml"


class Pricing:
    """Configured per-minute rates by Solari primitive.

    A rate of `null` (or a missing key) means "we don't know the unit price" —
    estimates for that primitive come back as None -> "unavailable".
    """

    def __init__(self, rates: dict[str, float | None], currency: str = "USD",
                 note: str = ""):
        self.rates = rates
        self.currency = currency
        self.note = note

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Pricing":
        cfg = load_yaml(path or repo_root() / PRICING_CONFIG)
        rates = cfg.get("rates_per_minute", {}) or {}
        return cls(
            {k: rates.get(k) for k in ("browser", "sandbox", "desktop")},
            currency=cfg.get("currency", "USD"),
            note=cfg.get("note", ""),
        )

    def estimate_minutes(self, kind: str, minutes: float) -> float | None:
        rate = self.rates.get(kind)
        if rate is None:
            return None
        return rate * minutes


class UsageRecord:
    """One measured Solari session."""

    def __init__(self, kind: str, session_id: str, seconds: float):
        self.kind = kind
        self.session_id = session_id
        self.seconds = seconds

    def to_dict(self) -> dict[str, Any]:
        return {"primitive": self.kind, "session_id": self.session_id,
                "seconds": round(self.seconds, 2)}


class CostTracker:
    """Collects UsageRecords for a run and turns them into a cost summary.

    Lives on the RunLog (`run_log.cost`). Packages either time sessions
    themselves (`run_log.usage(kind, id, seconds)`) or use the `timed()`
    context manager.
    """

    def __init__(self, pricing: Pricing | None = None):
        self.pricing = pricing if pricing is not None else Pricing.load()
        self.records: list[UsageRecord] = []

    def record(self, kind: str, session_id: str, seconds: float) -> UsageRecord:
        rec = UsageRecord(kind, session_id, seconds)
        self.records.append(rec)
        return rec

    @contextmanager
    def timed(self, kind: str, session_id: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            self.record(kind, session_id, time.monotonic() - start)

    # -- reporting -------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Measured usage always; estimated cost only where a rate exists."""
        by_kind: dict[str, dict[str, Any]] = {}
        for rec in self.records:
            bucket = by_kind.setdefault(rec.kind, {"sessions": 0, "seconds": 0.0})
            bucket["sessions"] += 1
            bucket["seconds"] += rec.seconds

        estimated: dict[str, Any] = {}
        total_est = 0.0
        total_known = True
        for kind, bucket in by_kind.items():
            minutes = bucket["seconds"] / 60.0
            est = self.pricing.estimate_minutes(kind, minutes)
            if est is None:
                estimated[kind] = "unavailable"
                total_known = False
            else:
                estimated[kind] = round(est, 4)
                total_est += est

        return {
            "currency": self.pricing.currency,
            "sessions": {k: v["sessions"] for k, v in by_kind.items()},
            "seconds": {k: round(v["seconds"], 2) for k, v in by_kind.items()},
            "estimated_cost": estimated,
            "estimated_total": round(total_est, 4) if total_known and by_kind else (
                "unavailable" if by_kind else 0.0),
            "pricing_note": self.pricing.note,
        }


def summarize_run(events: list[dict[str, Any]], pricing: Pricing) -> dict[str, Any]:
    """Rebuild a run's cost summary from its JSONL events.

    Used by runs/cost-report.py on historical logs. Prefers the run's own
    `cost_summary` event; falls back to `usage`/`session` events so older
    logs still report session counts.
    """
    sessions: dict[str, int] = {}
    session_ids: dict[str, set] = {}
    seconds: dict[str, float] = {}
    est_total: Any = None
    run_id = ""
    status = ""
    for ev in events:
        run_id = run_id or ev.get("run", "")
        if ev.get("step") == "usage":
            d = ev.get("detail", {})
            kind = d.get("primitive", "unknown")
            sessions[kind] = sessions.get(kind, 0) + 1
            seconds[kind] = seconds.get(kind, 0.0) + float(d.get("seconds", 0))
        elif ev.get("step") == "session":
            kind = ev.get("detail", {}).get("kind", "unknown")
            if ev.get("session_id"):
                session_ids.setdefault(kind, set()).add(ev["session_id"])
        elif ev.get("step") == "cost_summary":
            est_total = ev.get("detail", {}).get("estimated_total")
            for kind, s in (ev.get("detail", {}).get("seconds") or {}).items():
                seconds.setdefault(kind, float(s))
        elif ev.get("step") == "run_finish":
            status = ev.get("status", "")
    # Fill gaps for old logs that only have `session` events (open+close for
    # the same id): count unique session ids per kind.
    for kind, ids in session_ids.items():
        sessions.setdefault(kind, len(ids))
    # `session` events fire on open and again on close — unique session ids
    # are the truth for counts where ids were logged. Keep it simple: the
    # usage events are the measured counts; session events only fill gaps.
    est: dict[str, Any] = {}
    if est_total is None and sessions:
        total = 0.0
        known = True
        for kind, secs in seconds.items():
            e = pricing.estimate_minutes(kind, secs / 60.0)
            if e is None:
                known = False
                est[kind] = "unavailable"
            else:
                est[kind] = round(e, 4)
                total += e
        est_total = round(total, 4) if known else "unavailable"
    return {
        "run": run_id,
        "status": status,
        "sessions": sessions,
        "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "estimated_total": est_total if est_total is not None else "unavailable",
    }
