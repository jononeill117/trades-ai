"""Progressive trust: confidence math, proposals, grants, revocation."""

from __future__ import annotations

import asyncio

from core import trust as trust_mod
from core.approvals import ApprovalGate, MockApprovalBackend
from core.audit import RunLog


def _run(coro):
    return asyncio.run(coro)


def _cfg(**over):
    base = {
        "levels": {},
        "thresholds": {"min_attempts": 10, "min_confidence": 0.9},
        "labels": {"review.publish": "review replies"},
        "proposals": {"review.publish":
                      "auto-post 5-star replies, keep gating 1-3 stars."},
    }
    base.update(over)
    return base


def test_confidence_counts_unchanged_approvals(tmp_path):
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=_cfg())
    for _ in range(9):
        t.record("a.b", "approved")
    t.record("a.b", "edited")
    assert t.confidence("a.b") == 0.9
    t.record("a.b", "denied")
    assert abs(t.confidence("a.b") - 9 / 11) < 1e-6
    # auto runs don't feed confidence
    t.record("a.b", "auto")
    assert abs(t.confidence("a.b") - 9 / 11) < 1e-6


def test_threshold_crossing_fires_proposal(tmp_path):
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=_cfg())
    for _ in range(10):
        t.record("review.publish", "approved")
    prop = t.check_proposal("review.publish")
    assert prop and "you approved 10 unchanged" in prop["text"]
    assert "auto-post 5-star replies" in prop["text"]
    # one open proposal per action — no spam
    assert t.check_proposal("review.publish") is None


def test_below_threshold_no_proposal(tmp_path):
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=_cfg())
    for _ in range(9):
        t.record("x.y", "approved")
    assert t.check_proposal("x.y") is None          # too few attempts
    for _ in range(3):
        t.record("x.y", "denied")
    assert t.confidence("x.y") < 0.9                # 9/12 — denials drag it down
    assert t.check_proposal("x.y") is None


def test_denial_withdraws_open_proposal(tmp_path):
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=_cfg())
    for _ in range(10):
        t.record("x.y", "approved")
    assert t.check_proposal("x.y") is not None
    t.record("x.y", "denied")
    assert "x.y" not in t.proposals


def test_grant_auto_skips_gate_and_logs(tmp_path):
    cfg = _cfg(levels={"x.y": "auto"})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("deny"),  # would deny if asked
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    log = gate.run_log
    ok = _run(trust_mod.require(gate, t, log, "x.y", "gmail", "send it"))
    assert ok is True
    assert t.stats("x.y")["auto"] == 1
    import json
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "trust_auto" for e in events)
    assert not any(e["step"] == "approval_request" for e in events)


def test_revoke_restores_gating(tmp_path):
    cfg = _cfg(levels={"x.y": "auto"})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("approve"),
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    assert _run(trust_mod.require(gate, t, gate.run_log,
                                "x.y", "gmail", "s")) is True
    # owner revokes -> gate again
    t.cfg["levels"]["x.y"] = "gate"
    assert _run(trust_mod.require(gate, t, gate.run_log,
                                "x.y", "gmail", "s")) is True
    assert t.stats("x.y")["approved"] == 1     # human decision recorded


def test_seed_imported_once(tmp_path):
    cfg = _cfg(seed={"review.publish": {"approved": 46, "denied": 1}})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    s = t.stats("review.publish")
    assert s["attempts"] == 47 and s["approved"] == 46
    # seed is not re-applied on reload — the ledger file wins
    t2 = trust_mod.TrustLedger(path=tmp_path / "l.json",
                               cfg=_cfg(seed={"x.y": {"approved": 99}}))
    assert t2.stats("x.y")["attempts"] == 0
