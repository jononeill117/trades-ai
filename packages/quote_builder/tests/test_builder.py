"""Tests for quote-builder: history matching, band pricing, gate, e2e."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.config import repo_root
from packages.quote_builder import agent
from packages.quote_builder.history import (concerns_for, exploratory_for,
                                            load_history, match_description)


def _run(coro):
    return asyncio.run(coro)


HIST_DIR = repo_root() / "packages/quote_builder/fixtures/history"
BANDS, JOBS = load_history(HIST_DIR)


def test_bands_have_refs_and_prices():
    assert "wh_50_ng_install" in BANDS
    band = BANDS["wh_50_ng_install"]
    assert len(band.prices) >= 3 and len(band.refs) == len(band.prices)
    assert band.lo <= band.median <= band.hi


def test_match_from_plain_english():
    matched, uncertain = match_description(
        "customer wants their 50-gal gas water heater replaced, it's in the "
        "garage, current unit is from 2009, they asked about going tankless, "
        "haul away the old unit, include an expansion tank", BANDS)
    ids = {m["item"] for m in matched}
    assert {"wh_50_ng_install", "expansion_tank", "haul_away"} <= ids
    # "tankless" is a single weak keyword — a suggestion for the human,
    # not a priced line.
    assert any(u["item"] == "tankless_install" for u in uncertain)
    assert "tankless_install" not in ids


def test_haul_away_bundled_on_replacement():
    matched, _ = match_description(
        "replace the leaking 50 gallon water heater", BANDS)
    assert any(m["item"] == "haul_away" for m in matched)


def test_explicit_qty_wins():
    matched, _ = match_description("tune up the furnace", BANDS,
                                   explicit_qty={"furnace_tune": 2})
    assert next(m for m in matched
                if m["item"] == "furnace_tune")["qty"] == 2


def test_exploratory_and_concerns_from_history_and_text():
    matched, _ = match_description(
        "replace the 50-gal water heater, unit is from 2009, asked about "
        "tankless", BANDS)
    expl = exploratory_for(matched, JOBS,
                           "replace the 50-gal water heater, unit is from "
                           "2009, asked about tankless")
    assert any("flue" in e or "vent" in e for e in expl)
    assert any("2009" in e for e in expl)
    concerns = concerns_for(matched, JOBS,
                            "replace the 50-gal water heater, asked about "
                            "tankless")
    assert any("tankless" in c for c in concerns)


def test_mock_run_end_to_end(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    monkeypatch.setattr(agent, "STATE", tmp_path / "qb_state.json")

    log = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    q = result["quote"]

    # wh_50_ng_install median of {1580,1625,1690,1725} = 1657.50
    # expansion_tank median of {260,275,295} = 275
    # haul_away median of {75,80,80,85,90,95} = 82.5
    prices = {l["item"]: l["unit_price"] for l in q["lines"]}
    assert prices["wh_50_ng_install"] == 1657.50
    assert prices["expansion_tank"] == 275.00
    assert prices["haul_away"] == 82.50

    # every priced line sits inside its historical band and cites refs
    for l in q["lines"]:
        band = BANDS[l["item"]]
        assert band.lo <= l["unit_price"] <= band.hi
        assert l["priced_from"]

    assert q["exclusions"]            # never empty
    assert q["exploratory"] and q["concerns"]
    assert q["terms"]["deposit"] and q["terms"]["validity"]
    assert result["delivered"] is True

    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "approval"
               and e["detail"]["action"] == "quote.deliver" for e in events)

    md = Path(result["markdown"]).read_text()
    for section in ("NOT included", "can't know until work starts",
                    "Concerns & risks", "If the job grows", "Terms"):
        assert section in md


def test_exclusions_never_empty(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    monkeypatch.setattr(agent, "STATE", tmp_path / "qb_state.json")
    log = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log,
                            {"mode": "mock", "exclusions": []}))
    assert result["quote"]["exclusions"]


def test_denied_delivery_renders_but_never_sends(tmp_path, monkeypatch):
    from core.approvals import ApprovalGate, MockApprovalBackend
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    import packages.quote_builder.agent as ag

    monkeypatch.setattr(ag, "get_gate",
                        lambda m, r: ApprovalGate(MockApprovalBackend("deny"),
                                                  run_log=r))
    monkeypatch.setattr(ag, "STATE", tmp_path / "qb_state.json")
    log = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["delivered"] is False
    assert Path(result["markdown"]).exists()


def test_identical_quote_not_redelivered(tmp_path, monkeypatch):
    from core.audit import RunLog
    from core.mock_solari import MockSolari
    monkeypatch.setattr(agent, "STATE", tmp_path / "qb_state.json")

    log1 = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    r1 = _run(agent.run(MockSolari(), log1, {"mode": "mock"}))
    assert r1["delivered"] is True

    log2 = RunLog("quote-builder", "mock", runs_dir=tmp_path)
    r2 = _run(agent.run(MockSolari(), log2, {"mode": "mock"}))
    assert r2["duplicate"] is True and r2["delivered"] is False
    events = [json.loads(l) for l in log2.path.read_text().splitlines()]
    assert not any(e["step"] == "approval_request" for e in events)
