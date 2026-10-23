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


# -- grant scopes (WS7 T-1: an `auto` grant must enforce its promised scope) ---

def test_scope_allows_rule_language():
    a = trust_mod.scope_allows
    assert a({"stars": [5]}, {"stars": 5}) is True
    assert a({"stars": [5]}, {"stars": 3}) is False
    assert a({"tone": "friendly"}, {"tone": "friendly"}) is True
    assert a({"tone": ["friendly"]}, {"tone": "final"}) is False
    assert a({"total_max": 500}, {"total": 499.99}) is True
    assert a({"total_max": 500}, {"total": 500}) is True
    assert a({"total_max": 500}, {"total": 501}) is False
    assert a({"total_min": 10}, {"total": 9}) is False
    # unverifiable never auto-passes
    assert a({"total_max": 500}, {"total": "500"}) is False
    assert a({"total_max": None}, {"total": 1}) is False    # ceiling unset
    assert a({"stars": [5]}, {}) is False                   # missing field
    assert a({"stars": [5]}, None) is False                 # no payload
    assert a({}, None) is True                              # unscoped grant
    # every rule must hold
    assert a({"stars": [5], "tone": ["friendly"]},
             {"stars": 5, "tone": "firm"}) is False


def test_scoped_auto_runs_only_inside_scope(tmp_path):
    cfg = _cfg(levels={"review.publish": "auto"},
               scopes={"review.publish": {"stars": [5]}})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("deny"),  # human would deny
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    log = gate.run_log
    # inside scope: auto-runs without asking the (denying) human
    assert _run(trust_mod.require(gate, t, log, "review.publish", "gbp",
                                  "s", payload={"stars": 5})) is True
    assert t.stats("review.publish")["auto"] == 1
    # outside scope: routed to the gate, which denies; no auto recorded
    assert _run(trust_mod.require(gate, t, log, "review.publish", "gbp",
                                  "s", payload={"stars": 2})) is False
    assert t.stats("review.publish")["auto"] == 1
    assert t.stats("review.publish")["denied"] == 1
    import json
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "trust_scope" for e in events)


def test_scoped_auto_out_of_scope_can_still_be_approved(tmp_path):
    cfg = _cfg(levels={"quote.deliver": "auto"},
               scopes={"quote.deliver": {"total_max": 500}})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("approve"),
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    # over the ceiling: a human approves it — recorded as approved, not auto
    assert _run(trust_mod.require(gate, t, gate.run_log, "quote.deliver",
                                  "gmail", "s", payload={"total": 900})) is True
    assert t.stats("quote.deliver")["approved"] == 1
    assert t.stats("quote.deliver")["auto"] == 0
    # under the ceiling: auto
    assert _run(trust_mod.require(gate, t, gate.run_log, "quote.deliver",
                                  "gmail", "s", payload={"total": 100})) is True
    assert t.stats("quote.deliver")["auto"] == 1


def test_scoped_auto_unverifiable_payload_gates(tmp_path):
    cfg = _cfg(levels={"review.publish": "auto"},
               scopes={"review.publish": {"stars": [5]}})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("deny"),
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    assert _run(trust_mod.require(gate, t, gate.run_log, "review.publish",
                                  "gbp", "s")) is False            # no payload
    assert _run(trust_mod.require(gate, t, gate.run_log, "review.publish",
                                  "gbp", "s", payload={})) is False
    assert t.stats("review.publish")["auto"] == 0


def test_shipped_config_scopes_enforce_the_promises(tmp_path):
    t = trust_mod.TrustLedger(path=tmp_path / "l.json")  # real autonomy.yaml
    assert t.scope("review.publish") == {"stars": [5]}
    assert trust_mod.scope_allows(t.scope("review.publish"), {"stars": 5})
    assert not trust_mod.scope_allows(t.scope("review.publish"), {"stars": 1})
    assert trust_mod.scope_allows(t.scope("invoice.reminder.email"),
                                  {"tone": "friendly"})
    assert not trust_mod.scope_allows(t.scope("invoice.reminder.sms"),
                                      {"tone": "final"})
    # the quote ceiling is unset in the shipped config, so nothing
    # auto-passes until the owner sets it
    assert not trust_mod.scope_allows(t.scope("quote.deliver"), {"total": 1})


def test_seed_imported_once(tmp_path):
    cfg = _cfg(seed={"review.publish": {"approved": 46, "denied": 1}})
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    s = t.stats("review.publish")
    assert s["attempts"] == 47 and s["approved"] == 46
    # seed is not re-applied on reload — the ledger file wins
    t2 = trust_mod.TrustLedger(path=tmp_path / "l.json",
                               cfg=_cfg(seed={"x.y": {"approved": 99}}))
    assert t2.stats("x.y")["attempts"] == 0
