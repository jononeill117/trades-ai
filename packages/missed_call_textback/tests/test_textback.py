"""Tests for missed-call-textback: qualification rules, idempotency, gate."""

from __future__ import annotations

import asyncio
import json

from packages.missed_call_textback.models import MissedCall
from packages.missed_call_textback.qualify import qualify
from packages.missed_call_textback.state import ProcessedState
from packages.missed_call_textback import agent


def _run(coro):
    return asyncio.run(coro)


def call(text: str, **kw) -> MissedCall:
    return MissedCall(id=kw.pop("id", "c1"), from_number="+15555550100",
                      voicemail_transcript=text, **kw)


# -- qualification -------------------------------------------------------------

def test_emergency_escalates():
    q = qualify(call("There's a gas smell near the furnace"))
    assert q.escalate and q.urgency == "emergency" and not q.bookable


def test_plumbing_leak_is_bookable():
    q = qualify(call("my water heater is leaking all over the garage"))
    assert q.trade == "plumbing" and q.bookable and not q.escalate
    assert q.urgency in ("high", "emergency")


def test_electrical_job_bookable():
    q = qualify(call("quote on installing two ceiling fans and a new outlet"))
    assert q.trade == "electrical" and q.bookable


def test_unknown_trade_escalates():
    q = qualify(call("just calling to chat about my bill"))
    assert q.escalate and not q.bookable


def test_empty_transcript_escalates():
    q = qualify(call(""))
    assert q.escalate and "no voicemail" in q.escalate_reason


# -- idempotency -----------------------------------------------------------------

def test_state_dedupes(tmp_path):
    st = ProcessedState(tmp_path / "state.json")
    assert not st.seen("c1")
    st.mark("c1")
    st2 = ProcessedState(tmp_path / "state.json")  # reload from disk
    assert st2.seen("c1") and not st2.seen("c2")


# -- end-to-end mock -------------------------------------------------------------

def test_mock_run_end_to_end(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    monkeypatch.setattr(agent, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "fresh.json"))
    log = RunLog("missed-call-textback", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    steps = {e["step"] for e in events}
    assert "textback" in steps and "qualify" in steps
    assert any(e["step"] == "approval" for e in events)
    assert result["processed"] >= 1
    assert result["escalated"] >= 1          # the gas-smell call
    assert result["booked"] >= 1             # at least one qualified call booked


def test_denied_approval_sends_nothing(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.missed_call_textback.agent as ag

    monkeypatch.setattr(ag, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "fresh.json"))
    monkeypatch.setattr(ag, "get_gate",
                        lambda mode, run_log: ApprovalGate(
                            MockApprovalBackend("deny"), run_log=run_log))

    log = RunLog("missed-call-textback", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["booked"] == 0
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert not any(e["step"] == "textback" and e["status"] in ("ok", "sent")
                   for e in events)
