"""WS7 mediums T-1 fix-up: caller integration for the approval gate.

QA failed finding 1 on the first build: the gate mechanism swapped an
approved edit into the caller's dict, but all 11 production call sites
passed throwaway dict literals and sent from pre-approval captures, so
no real send ever saw an edit. These tests drive the packages end to
end (mock mode) with a scripted CLI approver who edits every editable
payload, and assert the EDITED text is what the (stub) sender receives
— and that the original draft is never sent.

Also covered: the booking-gate decision (WS7 T-1 fix-up (b)) — the two
booking gates pass `editable=False` because their sends proceed from
order/slot objects, not payload fields, so the gate refuses `e` with an
explanation and denies rather than silently approving the unedited
booking; and the trust-scope guard (c) — a non-numeric rule value from
YAML (a quoted ceiling) routes to the human gate instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from core import trust as trust_mod
from core.approvals import (ApprovalDecision, ApprovalGate, ApprovalRequest,
                            CliApprovalBackend, MockApprovalBackend,
                            SlackApprovalBackend)
from core.audit import RunLog
from core.mock_solari import MockSolari
from core.notify.base import SendResult


def _run(coro):
    return asyncio.run(coro)


# -- scripted approver + recording senders -------------------------------------

_MARKER_KEYS = ("body", "subject", "text", "caption", "reply")


def _marker_editor(tmp_path):
    """An $EDITOR that rewrites every send-field to a marker string."""
    script = tmp_path / "editor.py"
    script.write_text(
        "import json, sys\n"
        "p = sys.argv[1]\n"
        "data = json.loads(open(p).read())\n"
        f"for k in {_MARKER_KEYS!r}:\n"
        "    if isinstance(data.get(k), str):\n"
        "        data[k] = 'EDITED ' + k\n"
        "open(p, 'w').write(json.dumps(data))\n")
    return f"{sys.executable} {script}"


def _scripted_input(plain_actions=()):
    """Approver: confirm every edit ('y' at the EDITED prompt), edit
    every editable action, plain-approve the actions in plain_actions."""
    def fn(prompt=""):
        if "EDITED" in prompt:
            return "y"
        action = prompt.split("Approve ", 1)[1].split("?")[0] \
            if "Approve " in prompt else ""
        return "y" if action in plain_actions else "e"
    return fn


def _cli_gate(tmp_path, plain_actions=()):
    backend = CliApprovalBackend(
        input_fn=_scripted_input(plain_actions),
        editor=_marker_editor(tmp_path))
    return lambda mode, run_log: ApprovalGate(backend, run_log=run_log)


class RecGmail:
    def __init__(self, sink): self.sink = sink

    async def send(self, to, subject, body, sender=None):
        self.sink.append(("gmail", to, subject, body))
        return SendResult("sent", "fake")


class RecQuo:
    def __init__(self, sink): self.sink = sink

    async def send(self, to, text):
        self.sink.append(("quo", to, text))
        return SendResult("sent", "fake")


class RecSlack:
    def __init__(self, sink): self.sink = sink

    async def send(self, text):
        self.sink.append(("slack", text))
        return SendResult("sent", "fake")


# -- dispatch: booking gate is plain-approved, both confirms edited ---------

def test_dispatch_confirm_sends_use_edited_payload(tmp_path, monkeypatch):
    import packages.dispatch.agent as ag

    wos = tmp_path / "wos"
    wos.mkdir()
    today = date.today()
    (wos / "wo-9001.eml").write_text(
        'From: "Maple Property Group" <workorders@maplepm.example>\n'
        "To: dispatch@yourshop.example\n"
        "Subject: Work Order WO-9001 — Plumbing — 12 Birch Ln Unit 3\n"
        "Message-ID: <wo-9001@maplepm.example>\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "Work Order: WO-9001\n"
        "Customer: Maple Property Group\n"
        "Site: 12 Birch Ln Unit 3, Dayton OH 45410\n"
        "Tenant: Rosa Delgado — 937-555-0184 — rosa.delgado@example.com\n"
        "Trade: Plumbing\n"
        "Priority: High\n"
        f"SLA: {today.isoformat()} 08:00 to "
        f"{(today + timedelta(days=3)).isoformat()} 17:00\n"
        "Notes: Kitchen sink drain backing up; standing water.\n")

    sink: list = []
    monkeypatch.setattr(ag, "get_gate",
                        _cli_gate(tmp_path, plain_actions=("dispatch.book",)))
    monkeypatch.setattr(ag, "GmailNotifier", lambda: RecGmail(sink))
    monkeypatch.setattr(ag, "QuoNotifier", lambda: RecQuo(sink))
    monkeypatch.setattr(ag, "SlackNotifier", lambda: RecSlack(sink))

    log = RunLog("dispatch", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log,
                         {"mode": "mock", "fixture_dir": str(wos)}))
    assert result["booked"] == 1

    gmail = [s for s in sink if s[0] == "gmail"]
    quo = [s for s in sink if s[0] == "quo"]
    assert len(gmail) == 1 and len(quo) == 1
    # The edited subject/body/text are what went out...
    assert gmail[0][2] == "EDITED subject" and gmail[0][3] == "EDITED body"
    assert quo[0][2] == "EDITED text"
    # ...and the pre-approval drafts were never sent.
    assert "You're booked for" not in gmail[0][3]
    assert "visit is booked" not in quo[0][2]


# -- missed-call textback: both SMS sends edited ------------------------------

def _patch_textback(ag, tmp_path, monkeypatch, sink, plain_actions):
    from packages.missed_call_textback.state import ProcessedState
    monkeypatch.setattr(ag, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "tb.json"))
    monkeypatch.setattr(ag, "get_gate",
                        _cli_gate(tmp_path, plain_actions=plain_actions))
    monkeypatch.setattr(ag, "QuoNotifier", lambda: RecQuo(sink))
    monkeypatch.setattr(ag, "SlackNotifier", lambda: RecSlack(sink))


def test_textback_sends_use_edited_payload(tmp_path, monkeypatch):
    import packages.missed_call_textback.agent as ag

    sink: list = []
    _patch_textback(ag, tmp_path, monkeypatch, sink,
                    plain_actions=("booking.create",))
    log = RunLog("textback", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["booked"] == 3

    quo = [s for s in sink if s[0] == "quo"]
    # 4 first texts (one per call) + 3 booking confirmations.
    assert len(quo) == 7
    assert all(s[2] == "EDITED text" for s in quo)
    assert not any("Sorry we missed your call" in s[2] for s in quo)
    assert not any("You're booked for" in s[2] for s in quo)


def test_textback_booking_edit_is_refused_end_to_end(tmp_path, monkeypatch):
    """Answering 'e' at the (non-editable) booking gate must NOT book the
    unedited job: the gate refuses, the booking is skipped, and no
    confirmation SMS follows. The first texts still send (edited)."""
    import packages.missed_call_textback.agent as ag

    sink: list = []
    _patch_textback(ag, tmp_path, monkeypatch, sink, plain_actions=())
    log = RunLog("textback", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["booked"] == 0

    quo = [s for s in sink if s[0] == "quo"]
    assert len(quo) == 4                       # first texts only, no confirms
    assert all(s[2] == "EDITED text" for s in quo)


# -- invoice chaser: email + SMS reminders edited ------------------------------

def test_invoice_chaser_sends_use_edited_payload(tmp_path, monkeypatch):
    import packages.invoice_chaser.agent as ag

    today = date.today()
    csv_path = tmp_path / "aging.csv"
    csv_path.write_text(
        "invoice_id,customer,email,phone,amount,issued,due,status\n"
        f"INV-A,Alpha Co,alpha@x.example,+15555550901,100.00,"
        f"{(today - timedelta(days=30)).isoformat()},"
        f"{(today - timedelta(days=1)).isoformat()},open\n"
        f"INV-B,Beta Co,beta@x.example,+15555550902,200.00,"
        f"{(today - timedelta(days=50)).isoformat()},"
        f"{(today - timedelta(days=20)).isoformat()},open\n")

    sink: list = []
    monkeypatch.setattr(ag, "_load_state", lambda p: {"invoices": {}})
    monkeypatch.setattr(ag, "_save_state", lambda p, s: None)
    monkeypatch.setattr(ag, "get_gate", _cli_gate(tmp_path))
    monkeypatch.setattr(ag, "GmailNotifier", lambda: RecGmail(sink))
    monkeypatch.setattr(ag, "QuoNotifier", lambda: RecQuo(sink))
    monkeypatch.setattr(ag, "SlackNotifier", lambda: RecSlack(sink))

    log = RunLog("chaser", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log,
                         {"mode": "mock", "aging_csv": str(csv_path),
                          "payments": ""}))
    assert result["reminded"] == 2

    gmail = [s for s in sink if s[0] == "gmail"]   # INV-A: step-1 email
    quo = [s for s in sink if s[0] == "quo"]       # INV-B: step-3 SMS
    assert len(gmail) == 1 and len(quo) == 1
    assert gmail[0][2] == "EDITED subject" and gmail[0][3] == "EDITED body"
    assert quo[0][2] == "EDITED body"
    assert "friendly reminder" not in gmail[0][2]
    assert "just a nudge" not in gmail[0][3]


# -- photo marketer: the published caption is the edited one -------------------

def test_photo_marketer_publish_uses_edited_caption(tmp_path, monkeypatch):
    import packages.photo_marketer.agent as ag

    seen: list = []

    async def fake_publish(core, cfg, platform, folder, caption, run_log):
        seen.append((platform, caption))
        return "recorded"

    monkeypatch.setattr(ag, "_publish", fake_publish)
    monkeypatch.setattr(ag, "get_gate", _cli_gate(tmp_path))
    log = RunLog("photos", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["posted"] == 3
    assert len(seen) == 3
    assert all(caption == "EDITED caption" for _, caption in seen)


# -- quote builder: the delivered body/subject are the edited ones -------------

def test_quote_builder_delivery_uses_edited_payload(tmp_path, monkeypatch):
    import packages.quote_builder.agent as ag

    sink: list = []
    monkeypatch.setattr(ag, "STATE", tmp_path / "qb_state.json")
    monkeypatch.setattr(ag, "get_gate", _cli_gate(tmp_path))
    monkeypatch.setattr(ag, "GmailNotifier", lambda: RecGmail(sink))

    log = RunLog("qb", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["delivered"] is True

    assert len(sink) == 1
    _, _to, subject, body = sink[0]
    assert subject == "EDITED subject" and body == "EDITED body"
    # The pre-approval rendered quote is NOT what was emailed.
    assert body != Path(result["markdown"]).read_text()


# -- quote follower: follow-up sends edited --------------------------------------

def test_quote_follower_sends_use_edited_payload(tmp_path, monkeypatch):
    import packages.quote_follower.agent as ag

    sink: list = []
    monkeypatch.setattr(ag, "get_gate", _cli_gate(tmp_path))
    monkeypatch.setattr(ag, "GmailNotifier", lambda: RecGmail(sink))
    monkeypatch.setattr(ag, "QuoNotifier", lambda: RecQuo(sink))
    monkeypatch.setattr(ag, "SlackNotifier", lambda: RecSlack(sink))

    log = RunLog("qf", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["followed_up"] == 3

    gmail = [s for s in sink if s[0] == "gmail"]
    assert len(gmail) == 3
    assert all(s[2] == "EDITED subject" and s[3] == "EDITED body"
               for s in gmail)
    assert not any("Still interested" in s[2] for s in gmail)


# -- review responder: the published reply is the edited one ---------------------

def test_review_responder_publish_uses_edited_reply(tmp_path, monkeypatch):
    import packages.review_responder.agent as ag
    from packages.missed_call_textback.state import ProcessedState

    seen: list = []

    class RecAdapter:
        def review_url(self, review):
            return f"https://reviews.example/reviews/{review.id}"

        async def publish(self, driver, review, draft):
            seen.append((review.id, draft.text))
            return "recorded"

    monkeypatch.setattr(ag, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "rr.json"))
    monkeypatch.setattr(ag, "ReviewPortalAdapter",
                        lambda *a, **k: RecAdapter())
    monkeypatch.setattr(ag, "get_gate", _cli_gate(tmp_path))
    monkeypatch.setattr(ag, "SlackNotifier", lambda: RecSlack([]))
    monkeypatch.setattr(ag, "GmailNotifier", lambda: RecGmail([]))

    log = RunLog("rr", "mock", runs_dir=tmp_path)
    result = _run(ag.run(MockSolari(), log, {"mode": "mock"}))
    assert result["published"] == 5
    assert len(seen) == 5
    assert all(text == "EDITED reply" for _, text in seen)


# -- booking gates: edit refused, never silently dropped (fix-up (b)) ------------

def test_cli_edit_refused_for_non_editable_request(tmp_path):
    editor = _marker_editor(tmp_path)
    backend = CliApprovalBackend(input_fn=lambda prompt="": "e",
                                 editor=editor)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"order": {"id": "WO-1"}, "slot": {"tech": "Ray"}}
    ok = _run(gate.require("dispatch.book", "portal", "book it",
                           payload=payload, requester="test",
                           editable=False))
    assert ok is False
    assert "not editable" in gate.last_decision.note
    assert payload == {"order": {"id": "WO-1"}, "slot": {"tech": "Ray"}}


def test_gate_fails_closed_when_backend_edits_non_editable(tmp_path):
    class EditBackend:
        name = "edit-always"

        async def decide(self, req):
            return ApprovalDecision(True, decided_by="test", edited=True,
                                    payload={"text": "EDITED text"})

    log = RunLog("t", "mock", runs_dir=tmp_path)
    gate = ApprovalGate(EditBackend(), run_log=log)
    payload = {"text": "ORIGINAL"}
    ok = _run(gate.require("booking.create", "portal", "book it",
                           payload=payload, requester="test",
                           editable=False))
    assert ok is False                       # the edit cannot be honored
    assert payload == {"text": "ORIGINAL"}  # and nothing was swapped in

    # Control: the same backend against an editable action approves and
    # the caller's dict receives the edit.
    payload2 = {"text": "ORIGINAL"}
    ok2 = _run(gate.require("sms.send", "quo-sms", "text it",
                            payload=payload2, requester="test"))
    assert ok2 is True and payload2 == {"text": "EDITED text"}


class _FakeSlack:
    def __init__(self, on_send=None):
        self.messages: list[str] = []
        self._on_send = on_send

    async def send(self, text):
        self.messages.append(text)
        if self._on_send:
            self._on_send(text)
        return SendResult("sent", "slack")


def test_slack_edited_payload_refused_for_non_editable(tmp_path):
    decisions = tmp_path / "approvals"
    decisions.mkdir()

    def on_send(text):
        req_id = re.search(r"`(apr-[0-9a-f]+)`", text).group(1)
        token = re.search(r'"token": "([^"]+)"', text).group(1)
        (decisions / f"{req_id}.json").write_text(json.dumps(
            {"approved": True, "by": "owner", "token": token,
             "payload": {"text": "EDITED text"}}))

    backend = SlackApprovalBackend(_FakeSlack(on_send), timeout_s=5,
                                   decisions_dir=decisions)
    req = ApprovalRequest(action="booking.create", channel="portal",
                          summary="book it", payload={"text": "ORIGINAL"},
                          editable=False)
    dec = _run(backend.decide(req))
    assert dec.approved is False
    assert "not editable" in dec.note


# -- trust scope: a quoted (non-numeric) rule routes to the gate (fix-up (c)) -----

def test_quoted_ceiling_rule_is_unverifiable_not_an_error(tmp_path):
    # scope_allows itself must not raise on a quoted YAML bound.
    assert trust_mod.scope_allows({"total_max": "500"},
                                  {"total": 100}) is False
    assert trust_mod.scope_allows({"total_min": "10"},
                                  {"total": 100}) is False

    cfg = {"levels": {"quote.deliver": "auto"},
           "scopes": {"quote.deliver": {"total_max": "500"}},
           "thresholds": {"min_attempts": 25, "min_confidence": 0.95},
           "labels": {}, "proposals": {}}
    t = trust_mod.TrustLedger(path=tmp_path / "l.json", cfg=cfg)
    gate = ApprovalGate(MockApprovalBackend("deny"),
                        run_log=RunLog("t", "mock", runs_dir=tmp_path))
    log = gate.run_log
    # No exception, no auto-run: the human gate decides (and denies here).
    ok = _run(trust_mod.require(gate, t, log, "quote.deliver", "gmail",
                                "deliver", payload={"total": 100}))
    assert ok is False
    assert t.stats("quote.deliver")["auto"] == 0
    assert t.stats("quote.deliver")["denied"] == 1
    events = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert any(e["step"] == "trust_scope" for e in events)
