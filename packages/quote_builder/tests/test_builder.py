"""Tests for quote-builder: pricebook matching, totals, gate, e2e."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.config import repo_root
from packages.quote_builder import agent
from packages.quote_builder.pricebook import load_pricebook, match_items


def _run(coro):
    return asyncio.run(coro)

BOOK = load_pricebook(repo_root() / "fixtures/pricebook/pricebook.csv")


def test_match_confident_items():
    matched, uncertain = match_items(
        "replace the 50 gallon water heater and add an expansion tank", BOOK)
    ids = {m["item_id"] for m in matched}
    assert {"WH-50-NG", "WH-EXP-TANK"} <= ids


def test_weak_keyword_is_uncertain_not_matched():
    matched, uncertain = match_items("add an outlet in the garage", BOOK)
    assert not any(m["item_id"] == "GFCI-OUTLET" for m in matched)
    assert any(u["item_id"] == "GFCI-OUTLET" for u in uncertain)


def test_explicit_qty_wins():
    matched, _ = match_items("tune up the furnace", BOOK,
                             explicit_qty={"FURNACE-TUNE": 2})
    assert next(m for m in matched if m["item_id"] == "FURNACE-TUNE")["qty"] == 2


def test_dispatch_fee_never_auto_matched():
    matched, _ = match_items("diagnostic visit for everything", BOOK)
    assert not any(m["item_id"] == "DISPATCH-FEE" for m in matched)


def test_mock_run_end_to_end(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    log = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    q = result["quote"]
    # 1650 + 285 = 1935 subtotal, taxable -> 7% tax = 135.45
    assert q["subtotal"] == 1935.00 and q["tax"] == 135.45
    assert q["total"] == 2070.45
    assert result["delivered"] is True
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "approval" and e["detail"]["action"] == "quote.deliver"
               for e in events)


def test_denied_delivery_renders_but_never_sends(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.quote_builder.agent as ag

    monkeypatch.setattr(ag, "get_gate",
                        lambda m, r: ApprovalGate(MockApprovalBackend("deny"), run_log=r))
    log = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["delivered"] is False
    assert Path(result["markdown"]).exists()   # still rendered for the human
