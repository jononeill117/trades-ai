"""Tests for photo-marketer: metadata stripping, scoring, captions, e2e."""

from __future__ import annotations

import asyncio
import base64
import json
import struct

from core.config import repo_root
from packages.photo_marketer import agent
from packages.photo_marketer.captions import variants
from packages.photo_marketer.media_worker import (png_dims, process_file,
                                                  strip_metadata)


def _run(coro):
    return asyncio.run(coro)


def test_png_dims():
    data = (repo_root() / "fixtures/photos/job-rivera/before.png").read_bytes()
    assert png_dims(data) == (64, 48)


def test_strip_metadata_drops_text_chunks():
    data = (repo_root() / "fixtures/photos/job-rivera/after.png").read_bytes()
    assert b"GPS" in data                      # fixture carries fake metadata
    cleaned = strip_metadata(data)
    assert b"GPS" not in cleaned and len(cleaned) < len(data)
    assert png_dims(cleaned) == (64, 48)       # still a valid image


def test_captions_are_platform_specific():
    caps = variants("job-x", "new water heater", {"shop_name": "S"})
    assert len({caps["gbp"], caps["facebook"], caps["instagram"]}) == 3
    assert "#" in caps["instagram"]


def test_mock_run_end_to_end(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    log = RunLog("photo-marketer", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    # job-rivera usable -> 3 platform posts; job-solo skipped (no pair)
    assert result["posted"] == 3 and result["skipped"] == 1
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    approvals = [e for e in events if e["step"] == "approval"]
    assert len(approvals) == 3
    assert {e["detail"]["channel"] for e in approvals} == \
        {"gbp", "facebook", "instagram"}
    # cleaned images written with metadata removed
    cleaned = (repo_root() / "out/photo-marketer/job-rivera-after.png").read_bytes()
    assert b"GPS" not in cleaned


def test_never_publishes_without_approval(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.photo_marketer.agent as ag

    monkeypatch.setattr(ag, "get_gate",
                        lambda m, r: ApprovalGate(MockApprovalBackend("deny"), run_log=r))
    log = RunLog("photo-marketer", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["posted"] == 0 and result["denied"] == 3
