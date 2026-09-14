"""Tests for meeting-prep: flags, render, e2e mock."""

from __future__ import annotations

import asyncio
import json

from packages.meeting_prep import agent
from packages.meeting_prep.flags import coach_notes, flag_job
from packages.meeting_prep.render import shop_summary_md, tech_brief_md


def _run(coro):
    return asyncio.run(coro)


def test_flag_rules():
    f = {f["flag"] for f in flag_job({
        "job_id": "J1", "status": "callback",
        "notes": "compressor under warranty, customer complained about dust"})}
    assert {"callback", "warranty_risk", "complaint"} <= f


def test_missing_notes_flagged():
    flags = flag_job({"job_id": "J2", "status": "complete", "notes": "",
                      "amount": 100})
    assert any(f["flag"] == "missing_notes" for f in flags)


def test_zero_amount_flag():
    flags = flag_job({"job_id": "J3", "status": "complete", "notes": "done",
                      "amount": 0})
    assert any(f["flag"] == "zero_amount" for f in flags)


def test_coaching_multiple_callbacks():
    jobs = [{"job_id": "A", "tech": "T", "status": "callback", "notes": "x",
             "amount": 0},
            {"job_id": "B", "tech": "T", "status": "callback", "notes": "y",
             "amount": 0}]
    flags = [f for j in jobs for f in flag_job(j)]
    notes = coach_notes("T", jobs, flags)
    assert any("callback" in n.lower() for n in notes)


def test_brief_cites_sources():
    jobs = [{"job_id": "JOB-9", "tech": "Dee", "summary": "s", "notes": "",
             "amount": 10, "status": "complete", "customer": "C"}]
    flags = [f for j in jobs for f in flag_job(j)]
    md = tech_brief_md("Dee", jobs, flags)
    assert "JOB-9" in md and "missing_notes" in md and "source: JOB-9" in md
    summary = shop_summary_md(jobs, flags)
    assert "Dee" in summary and "missing_notes" in summary


def test_mock_run_end_to_end(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    log = RunLog("meeting-prep", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    assert result["flags"] and result["techs"] == ["Dee", "Marcus", "Priya"]
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "usage" and e["detail"]["primitive"] == "sandbox"
               for e in events)
