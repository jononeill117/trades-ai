"""Tests for review-responder: lanes, drafting, normalization, e2e mock."""

from __future__ import annotations

import asyncio
import json

from packages.review_responder import agent
from packages.review_responder.draft import TemplateProvider, draft_reply
from packages.review_responder.models import Review
from packages.review_responder.normalize_worker import normalize_review


def _run(coro):
    return asyncio.run(coro)


def review(stars=5, **kw) -> Review:
    return Review(id=kw.pop("id", "r1"), author=kw.pop("author", "A. Person"),
                  stars=stars, text=kw.pop("text", "great job"), **kw)


def test_lanes():
    assert review(5).lane == "fast"
    assert review(4).lane == "fast"
    assert review(3).lane == "human_edit"
    assert review(1).lane == "human_edit"


def test_template_draft_uses_voice():
    d = draft_reply(review(5, author="Marisol V."),
                    {"signoff": "the crew", "praise_line": "glad it helped"},
                    TemplateProvider())
    assert "Marisol" in d.text and "the crew" in d.text
    assert d.lane == "fast"


def test_low_star_draft_flagged():
    d = draft_reply(review(1), {}, TemplateProvider())
    assert d.lane == "human_edit" and d.warnings


def test_unknown_provider_rejected():
    from packages.review_responder.draft import get_provider
    try:
        get_provider({"provider": "nope"})
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_normalize_strips_markup_and_flags_links():
    out = normalize_review({"id": "x", "author": "A<b>", "stars": "3",
                            "text": "ok work <script>alert(1)</script> see http://x.example",
                            "ts": "", "job_ref": ""})
    assert "<" not in out["text"] and "contains_link" in out["flags"]


def test_mock_run_publishes_after_approval(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.review_responder.agent as ag
    from packages.missed_call_textback.state import ProcessedState

    monkeypatch.setattr(ag, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "s.json"))
    log = RunLog("review-responder", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["published"] == 5 and result["drafted"] == 5
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    approvals = [e for e in events if e["step"] == "approval"]
    assert len(approvals) == 5
    # low-star reviews must go through the human-edit lane
    reqs = [e for e in events if e["step"] == "approval_request"]
    lanes = [e["detail"].get("summary", "") for e in reqs]
    assert any("human_edit" in s for s in lanes)


def test_denied_publish_is_fail_closed(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.review_responder.agent as ag
    from packages.missed_call_textback.state import ProcessedState

    monkeypatch.setattr(ag, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "s.json"))
    monkeypatch.setattr(ag, "get_gate",
                        lambda m, r: ApprovalGate(MockApprovalBackend("deny"), run_log=r))
    log = RunLog("review-responder", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["published"] == 0 and result["denied"] == 5
