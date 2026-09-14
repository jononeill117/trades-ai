"""Rule-based flags — the first pass over each completed job.

Flags are rules, not vibes: every flag carries the job_id it came from so a
manager can verify the claim against the source record. A model-assisted
summary layer can sit on top (see render.py) but the flags stay deterministic
and auditable.
"""

from __future__ import annotations

import re
from typing import Any

CALLBACK_RE = re.compile(r"\bcallback\b|return visit|status.*callback", re.I)
COMPLAINT_RE = re.compile(r"complain|unhappy|disappointed|mud|mess|dust|"
                          r"rude|late|no.?show", re.I)
WARRANTY_RE = re.compile(r"warrant", re.I)


def flag_job(job: dict[str, Any]) -> list[dict[str, str]]:
    """Rules over one normalized job record -> [{flag, detail, job_id}]."""
    flags = []
    jid = job.get("job_id", "?")
    notes = job.get("notes", "") or ""
    status = (job.get("status") or "").lower()

    if status == "callback" or CALLBACK_RE.search(notes):
        flags.append({"flag": "callback", "job_id": jid,
                      "detail": "status=callback or callback language in notes"})
    if COMPLAINT_RE.search(notes):
        flags.append({"flag": "complaint", "job_id": jid,
                      "detail": "complaint language in notes"})
    if WARRANTY_RE.search(notes):
        flags.append({"flag": "warranty_risk", "job_id": jid,
                      "detail": "warranty mentioned — confirm coverage before billing"})
    if not notes.strip():
        flags.append({"flag": "missing_notes", "job_id": jid,
                      "detail": "no job notes — can't verify what was done"})
    if job.get("amount", 0) == 0 and status != "callback":
        flags.append({"flag": "zero_amount", "job_id": jid,
                      "detail": "$0 job that isn't a callback — pricing error?"})
    return flags


def coach_notes(tech: str, jobs: list[dict], flags: list[dict]) -> list[str]:
    """Coaching opportunities for one tech — patterns across their jobs."""
    out = []
    mine = [f for f in flags if any(j.get("job_id") == f["job_id"] and
                                    j.get("tech") == tech for j in jobs)]
    if sum(1 for f in mine if f["flag"] == "callback") >= 2:
        out.append("Multiple callbacks this week — worth a ride-along or a "
                   "root-cause review.")
    if any(f["flag"] == "missing_notes" for f in mine):
        out.append("Job notes missing on at least one job — ask for a "
                   "two-line note before leaving site.")
    if any(f["flag"] == "complaint" for f in mine):
        out.append("A customer complaint this week — debrief what happened.")
    return out
