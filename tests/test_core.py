"""Core framework tests: cost tracking, approval gate, healthcheck."""

from __future__ import annotations

import asyncio
import json

from core.approvals import (ApprovalGate, ApprovalRequest, CliApprovalBackend,
                            MockApprovalBackend, get_gate)
from core.audit import RunLog
from core.cost import Pricing, summarize_run
from healthcheck import check_entry, selector_resolves
from core.drivers import _Page


def _run(coro):
    return asyncio.run(coro)


# -- cost ---------------------------------------------------------------------

def test_pricing_unavailable_when_rate_null(tmp_path):
    p = Pricing({"browser": None, "sandbox": 0.5, "desktop": None})
    assert p.estimate_minutes("browser", 2) is None
    assert p.estimate_minutes("sandbox", 2) == 1.0


def test_runlog_usage_and_cost_summary(tmp_path):
    log = RunLog("t", "mock", runs_dir=tmp_path)
    log.cost.pricing = Pricing({"browser": 0.10, "sandbox": None, "desktop": None})
    log.usage("browser", "s1", 60.0)
    log.usage("sandbox", "s2", 30.0)
    log.finish("ok")
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    summary = next(e for e in events if e["step"] == "cost_summary")
    assert summary["detail"]["sessions"] == {"browser": 1, "sandbox": 1}
    assert summary["detail"]["estimated_cost"]["browser"] == 0.1
    assert summary["detail"]["estimated_cost"]["sandbox"] == "unavailable"
    assert summary["detail"]["estimated_total"] == "unavailable"


def test_time_session_records_seconds(tmp_path):
    import time
    log = RunLog("t", "mock", runs_dir=tmp_path)
    with log.time_session("browser", "s1"):
        time.sleep(0.01)
    assert log.cost.records[0].seconds > 0
    assert log.cost.records[0].kind == "browser"


def test_summarize_run_counts_unique_sessions(tmp_path):
    log = RunLog("t", "mock", runs_dir=tmp_path)
    log.session("browser", "abc")
    log.session("browser", "abc")   # open + close events, same id
    log.finish("ok")
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    s = summarize_run(events, Pricing({"browser": None, "sandbox": None, "desktop": None}))
    assert s["sessions"]["browser"] == 1


# -- approvals -----------------------------------------------------------------

def test_mock_gate_approves_and_logs(tmp_path):
    log = RunLog("t", "mock", runs_dir=tmp_path)
    gate = get_gate("mock", run_log=log)
    ok = _run(gate.require("sms.send", "quo-sms", "text +1555...",
                           payload={"to": "+15555550100"}, requester="test"))
    assert ok
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    dec = next(e for e in events if e["step"] == "approval")
    assert dec["detail"]["approved"] is True
    assert dec["detail"]["requester"] == "test"
    assert dec["detail"]["action"] == "sms.send"


def test_mock_gate_deny_blocks(tmp_path):
    log = RunLog("t", "mock", runs_dir=tmp_path)
    gate = get_gate("mock", run_log=log, cfg={"mock_decision": "deny"})
    ok = _run(gate.require("email.send", "gmail", "send quote"))
    assert not ok


def test_gate_fails_closed_on_backend_error(tmp_path):
    class Boom:
        name = "boom"
        async def decide(self, req):
            raise RuntimeError("backend down")
    gate = ApprovalGate(Boom(), run_log=RunLog("t", "mock", runs_dir=tmp_path))
    assert _run(gate.require("sms.send", "quo-sms", "x")) is False


def test_preapproved_action_skips_human(tmp_path):
    gate = ApprovalGate(MockApprovalBackend("deny"),
                        run_log=RunLog("t", "mock", runs_dir=tmp_path),
                        preapproved=["sms.textback_v1"])
    assert _run(gate.require("sms.textback_v1", "quo-sms", "x")) is True
    assert _run(gate.require("sms.send", "quo-sms", "y")) is False


def test_cli_gate_no_tty_denies():
    backend = CliApprovalBackend()
    req = ApprovalRequest(action="email.send", channel="gmail", summary="s")
    # input() hits EOFError under pytest (no tty) -> fail closed
    assert _run(backend.decide(req)).approved is False


# -- healthcheck ---------------------------------------------------------------

def test_selector_resolves_ids_and_links():
    page = _Page()
    page.feed('<form><input id="email" name="email"></form>'
              '<a href="/jobs">Jobs</a><div id="status">ok</div>')
    assert selector_resolves(page, "#email")
    assert selector_resolves(page, "#status")
    assert selector_resolves(page, "Jobs")
    assert selector_resolves(page, "/jobs")
    assert not selector_resolves(page, "#missing")


def test_check_entry_fail_and_pass(tmp_path):
    html = tmp_path / "p.html"
    html.write_text('<div id="a"></div><span class="price">$9.99</span>')
    ok = check_entry({"name": "t", "html": str(html), "selectors": ["#a"],
                      "patterns": {"price": r"\$[0-9]"}}, "mock")
    assert ok["status"] == "ok"
    bad = check_entry({"name": "t", "html": str(html), "selectors": ["#zzz"]}, "mock")
    assert bad["status"] == "fail" and bad["failures"] == ["#zzz"]
