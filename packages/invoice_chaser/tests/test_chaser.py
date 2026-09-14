"""Tests for invoice-chaser: sequencing, normalization, honesty rule, e2e."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from packages.invoice_chaser import agent
from packages.invoice_chaser.models import Invoice
from packages.invoice_chaser.normalize_worker import normalize_row
from packages.invoice_chaser.sequence import next_step, should_escalate


def _run(coro):
    return asyncio.run(coro)

TODAY = date(2026, 9, 13)


def inv(**kw) -> Invoice:
    d = dict(invoice_id="INV-1", customer="Test Co", email="t@x.example",
             phone="+15555550100", amount=500.0, due="2026-09-01",
             issued="2026-08-01")
    d.update(kw)
    return Invoice(**d)


# -- sequencing ------------------------------------------------------------------

def test_step1_due_when_1_day_over():
    r = next_step(inv(due="2026-09-10"), 0, {}, TODAY)
    assert r and r.step == 1 and r.channel == "email" and r.tone == "friendly"


def test_latest_due_step_wins():
    r = next_step(inv(due="2026-08-20"), 0, {}, TODAY)   # 24 days over
    assert r.step == 3 and r.channel == "sms"


def test_sent_steps_not_repeated():
    # 24 days over but step 3 already sent -> nothing due
    assert next_step(inv(due="2026-08-20"), 3, {}, TODAY) is None
    # step 1 sent, now past step-2 threshold -> step 2
    r = next_step(inv(due="2026-09-01"), 1, {}, TODAY)
    assert r.step == 2


def test_not_yet_due_is_none():
    assert next_step(inv(due="2026-09-13"), 0, {}, TODAY) is None


def test_paid_disputed_never_reminded():
    for st in ("paid", "disputed", "escalated"):
        assert next_step(inv(status=st, due="2026-08-01"), 0, {}, TODAY) is None


def test_channel_falls_back_when_no_email():
    r = next_step(inv(email="", due="2026-09-10"), 0, {}, TODAY)
    assert r.channel == "sms"
    assert next_step(inv(email="", phone="", due="2026-09-10"), 0, {}, TODAY) is None


def test_escalation_after_exhausted_sequence():
    old = inv(due="2026-07-01")
    assert should_escalate(old, 3, {}, TODAY)
    assert not should_escalate(old, 0, {}, TODAY)          # still has steps to send
    assert not should_escalate(inv(status="paid", due="2026-07-01"), 3, {}, TODAY)


# -- normalization -----------------------------------------------------------------

def test_normalize_bad_rows_warn_not_crash():
    out = normalize_row({"invoice_id": "", "customer": "X", "amount": "abc",
                          "email": "", "phone": "", "status": "weird"})
    assert "missing invoice_id" in out["warnings"]
    assert any("amount" in w for w in out["warnings"])
    assert out["status"] == "aging"      # unknown -> aging
    assert "no contact channel" in out["warnings"]


def test_normalize_open_becomes_aging():
    assert normalize_row({"invoice_id": "I", "status": "open"})["status"] == "aging"


# -- e2e ----------------------------------------------------------------------------

def test_mock_run_end_to_end(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.invoice_chaser.agent as ag

    monkeypatch.setattr(ag, "_load_state", lambda p: {"invoices": {}})
    monkeypatch.setattr(ag, "_save_state", lambda p, s: None)
    log = RunLog("invoice-chaser", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["invoices"] == 6
    assert result["reminded"] >= 1
    # INV-2205 was supplied as paid -> counted as recovered, never reminded
    assert result["recovered_revenue"] == 2210.75
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    reminded_ids = {e["detail"].get("invoice")
                    for e in events if e["step"] == "remind"}
    assert "INV-2205" not in reminded_ids
    assert any(e["step"] == "approval" for e in events)


def test_denied_reminders_send_nothing(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.invoice_chaser.agent as ag

    monkeypatch.setattr(ag, "_load_state", lambda p: {"invoices": {}})
    monkeypatch.setattr(ag, "_save_state", lambda p, s: None)
    monkeypatch.setattr(ag, "get_gate",
                        lambda m, r: ApprovalGate(MockApprovalBackend("deny"), run_log=r))
    log = RunLog("invoice-chaser", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["reminded"] == 0
