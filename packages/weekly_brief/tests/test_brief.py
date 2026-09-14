"""Tests for weekly-brief: aggregation math, detection rules, idempotency."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from packages.weekly_brief import agent
from packages.weekly_brief.insights import (coaching_notes, friction_themes,
                                          improvement_areas, wins)


def _run(coro):
    return asyncio.run(coro)


def test_aggregation_math(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    log = RunLog("weekly-brief", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    agg = result["aggregates"]

    t = agg["totals"]
    assert t["jobs"] == 18 and t["sold"] == 13
    assert t["revenue"] == round(1890+240+1450+3250+1680+189+780+3200+1645+210+295+520+415, 2)
    assert t["close_rate"] == round(13 / 18, 3)
    assert t["callbacks"] == 5 and t["warranty_calls"] == 4
    assert t["sentiment"] == {"1": 1, "2": 1, "3": 1, "4": 1, "5": 3}

    priya = agg["per_tech"]["Priya"]
    assert priya["quoted"] == 6 and priya["sold"] == 6
    assert priya["close_rate"] == 1.0
    assert priya["attainment"] == round(7569.0 / 6000, 3)
    assert priya["five_star"] == 3

    marcus = agg["per_tech"]["Marcus"]
    assert marcus["close_rate"] == round(2 / 6, 3)
    assert marcus["callbacks"] == 3
    assert marcus["failed_quotes"] == 4


def test_detection_rules(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    log = RunLog("weekly-brief", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    agg = result["aggregates"]
    cfg = {"quota_attainment_min": 0.9, "close_rate_min": 0.5,
           "callback_rate_max": 0.15}

    themes = {t["theme"] for t in friction_themes(agg)}
    assert "water-heater installs" in themes
    assert "pricing & quote accuracy" in themes

    areas = {i["area"] for i in improvement_areas(agg, cfg)}
    assert "Marcus — quota" in areas
    assert "Marcus — close rate" in areas
    assert "Marcus — repeat callbacks" in areas
    assert not any(a.startswith("Priya — close rate") for a in areas)

    win_lines = wins(agg)
    assert any("Priya closed 100%" in w or "Priya" in w and "closed" in w
               for w in win_lines)
    assert any("five-star" in w.lower() for w in win_lines)

    coaching = coaching_notes(agg, cfg)
    assert any("Close rate" in n for n in coaching["Marcus"])
    assert any("5-star" in n for n in coaching["Priya"])


def test_brief_has_all_sections(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    log = RunLog("weekly-brief", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    md = Path(result["brief"]).read_text()
    for section in ("Wins to celebrate", "Friction to address",
                    "Areas of improvement", "Talking points for the meeting",
                    "Per-tech coaching notes"):
        assert section in md


def test_rerun_is_idempotent(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    # Point the state file at tmp_path so the test is hermetic.
    monkeypatch.setattr(agent, "STATE", tmp_path / "state.json")

    log1 = RunLog("weekly-brief", "mock", runs_dir=tmp_path)
    r1 = _run(agent.run(MockSolari(), log1, {"mode": "mock"}))
    assert r1["notified"] is True

    log2 = RunLog("weekly-brief", "mock", runs_dir=tmp_path)
    r2 = _run(agent.run(MockSolari(), log2, {"mode": "mock"}))
    assert r2["notified"] is False
    events = [json.loads(l) for l in log2.path.read_text().splitlines()]
    sk = next(e for e in events if e["step"] == "notify_slack")
    assert sk["status"] == "skipped"
