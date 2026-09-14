"""Tests for quote-follower: classification, sources, e2e mock."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from packages.quote_follower import agent
from packages.quote_follower.models import Quote
from packages.quote_follower.sources import BrowserQuoteSource, CsvQuoteSource
from core.config import repo_root


def _run(coro):
    return asyncio.run(coro)

TODAY = date(2026, 9, 13)


def test_buckets():
    draft = Quote(quote_id="Q1", customer="X", status="draft", created="2026-09-01")
    assert draft.bucket(TODAY, 5) == "never_sent"
    idle = Quote(quote_id="Q2", customer="X", status="sent",
                 created="2026-08-20", last_activity="2026-08-21")
    assert idle.bucket(TODAY, 5) == "sent_inactive"
    fresh = Quote(quote_id="Q3", customer="X", status="sent",
                  created="2026-09-10", last_activity="2026-09-11")
    assert fresh.bucket(TODAY, 5) == "active"
    won = Quote(quote_id="Q4", customer="X", status="won", created="2026-08-01")
    assert won.bucket(TODAY, 5) == "active"


def test_csv_source():
    src = CsvQuoteSource(repo_root() / "fixtures/quotes/quotes.csv")
    quotes = _run(src.fetch_quotes())
    assert len(quotes) == 5
    q = next(q for q in quotes if q.quote_id == "Q-3011")
    assert q.amount == 3850.00 and q.status == "sent"


def test_browser_source_fixture():
    from core.drivers import FixtureDriver
    html = (repo_root() / "fixtures/quote_portal/quotes.html").read_text()
    driver = FixtureDriver({"quotes.example": html})
    src = BrowserQuoteSource(driver, "https://quotes.example/quotes")
    quotes = _run(src.fetch_quotes())
    assert {q.quote_id for q in quotes} == {"Q-3011", "Q-3012", "Q-3013"}


def test_mock_run_end_to_end(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    log = RunLog("quote-follower", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log, {"mode": "mock"}))
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert result["flagged"] >= 2          # drafts + the idle sent quote
    assert result["followed_up"] >= 1
    assert any(e["step"] == "approval" for e in events)
    # never_sent and sent_inactive are reported as distinct buckets
    buckets = {e["detail"].get("bucket")
               for e in events if e["step"] == "classify"}
    assert "never_sent" in buckets and "sent_inactive" in buckets


def test_csv_source_mode(tmp_path):
    from core.audit import RunLog
    from core.mock_solari import MockSolari

    log = RunLog("quote-follower", "mock", runs_dir=tmp_path)
    result = _run(agent.run(MockSolari(), log,
                            {"mode": "mock", "source": "csv"}))
    assert result["quotes"] == 5 and result["flagged"] >= 2
