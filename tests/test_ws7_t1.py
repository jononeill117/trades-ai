"""WS7 mediums T-1: approval gate integrity.

- CLI 'e' must run a real edit and approve only the edited payload.
- Slack file-drop decisions must carry the per-request token.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

from core.approvals import (ApprovalGate, ApprovalRequest, CliApprovalBackend,
                            SlackApprovalBackend)
from core.audit import RunLog
from core.notify.base import SendResult


def _run(coro):
    return asyncio.run(coro)


def _answers(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def _editor_script(tmp_path, body: str):
    script = tmp_path / "editor.py"
    script.write_text(body)
    return f"{sys.executable} {script}"


_EDIT_REPLY = (
    "import json, sys\n"
    "p = sys.argv[1]\n"
    "data = json.loads(open(p).read())\n"
    "data['reply'] = 'EDITED TEXT'\n"
    "open(p, 'w').write(json.dumps(data))\n"
)


# -- CLI edit (finding 1) ------------------------------------------------------

def test_cli_edit_approves_only_the_edited_payload(tmp_path):
    editor = _editor_script(tmp_path, _EDIT_REPLY)
    backend = CliApprovalBackend(input_fn=_answers("e", "y"), editor=editor)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is True
    assert gate.last_decision.edited is True
    # The caller's dict now holds exactly what the human approved.
    assert payload == {"reply": "EDITED TEXT"}


def test_cli_edit_without_editor_denies(tmp_path, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    backend = CliApprovalBackend(input_fn=_answers("e"))  # editor=None -> env
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is False                      # fail closed: no editor, no send
    assert payload == {"reply": "ORIGINAL"}


def test_cli_edit_not_confirmed_denies(tmp_path):
    editor = _editor_script(tmp_path, _EDIT_REPLY)
    backend = CliApprovalBackend(input_fn=_answers("e", "n"), editor=editor)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is False
    assert payload == {"reply": "ORIGINAL"}  # the unedited draft is not sent


def test_cli_edit_invalid_json_denies(tmp_path):
    editor = _editor_script(tmp_path,
                            "import sys\nopen(sys.argv[1], 'w').write('{nope')\n")
    backend = CliApprovalBackend(input_fn=_answers("e", "y"), editor=editor)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is False
    assert payload == {"reply": "ORIGINAL"}


def test_cli_edit_editor_failure_denies(tmp_path):
    editor = _editor_script(tmp_path, "import sys\nsys.exit(3)\n")
    backend = CliApprovalBackend(input_fn=_answers("e", "y"), editor=editor)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is False
    assert payload == {"reply": "ORIGINAL"}


def test_cli_plain_yes_still_approves_unchanged(tmp_path):
    backend = CliApprovalBackend(input_fn=_answers("y"))
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is True
    assert gate.last_decision.edited is False
    assert payload == {"reply": "ORIGINAL"}


# -- Slack token binding (finding 3) --------------------------------------------

class FakeSlack:
    """Captures the posted message; `on_send` plays the listener role."""

    def __init__(self, on_send=None):
        self.messages: list[str] = []
        self._on_send = on_send

    async def send(self, text):
        self.messages.append(text)
        if self._on_send:
            self._on_send(text)
        return SendResult("sent", "slack")


def _req_id(text: str) -> str:
    return re.search(r"`(apr-[0-9a-f]+)`", text).group(1)


def _token(text: str) -> str:
    return re.search(r'"token": "([^"]+)"', text).group(1)


def _write_decision(decisions_dir, text, **fields):
    (decisions_dir / f"{_req_id(text)}.json").write_text(json.dumps(fields))


def test_slack_forged_decision_file_is_ignored(tmp_path):
    decisions = tmp_path / "approvals"
    decisions.mkdir()
    # A forger who can write into out/approvals/ but never saw the Slack
    # message drops an approval with no token. It must not be honored.
    fake = FakeSlack(lambda text: _write_decision(
        decisions, text, approved=True, by="mallory"))
    backend = SlackApprovalBackend(fake, timeout_s=2, decisions_dir=decisions)
    req = ApprovalRequest(action="sms.send", channel="quo-sms", summary="s")
    dec = _run(backend.decide(req))
    assert dec.approved is False
    assert "no decision within" in dec.note


def test_slack_decision_with_token_is_honored(tmp_path):
    decisions = tmp_path / "approvals"
    decisions.mkdir()
    fake = FakeSlack(lambda text: _write_decision(
        decisions, text, approved=True, by="owner", token=_token(text)))
    backend = SlackApprovalBackend(fake, timeout_s=5, decisions_dir=decisions)
    req = ApprovalRequest(action="sms.send", channel="quo-sms", summary="s")
    dec = _run(backend.decide(req))
    assert dec.approved is True
    assert dec.decided_by == "slack:owner"


def test_slack_wrong_token_then_right_token(tmp_path):
    decisions = tmp_path / "approvals"
    decisions.mkdir()
    seen = {}

    def on_send(text):
        seen["text"] = text
        _write_decision(decisions, text, approved=True, by="mallory",
                        token="not-the-token")

    backend = SlackApprovalBackend(FakeSlack(on_send), timeout_s=10,
                                   decisions_dir=decisions)
    req = ApprovalRequest(action="sms.send", channel="quo-sms", summary="s")

    async def main():
        task = asyncio.create_task(backend.decide(req))
        await asyncio.sleep(0.3)
        # The real approver rewrites the file with the bound token.
        _write_decision(decisions, seen["text"], approved=True, by="owner",
                        token=_token(seen["text"]))
        return await task

    dec = _run(main())
    assert dec.approved is True
    assert dec.decided_by == "slack:owner"


def test_slack_edited_payload_replaces_the_draft(tmp_path):
    decisions = tmp_path / "approvals"
    decisions.mkdir()
    fake = FakeSlack(lambda text: _write_decision(
        decisions, text, approved=True, by="owner", token=_token(text),
        payload={"reply": "EDITED TEXT"}))
    backend = SlackApprovalBackend(fake, timeout_s=5, decisions_dir=decisions)
    gate = ApprovalGate(backend, run_log=RunLog("t", "mock", runs_dir=tmp_path))
    payload = {"reply": "ORIGINAL"}
    ok = _run(gate.require("review.publish", "gbp", "reply",
                           payload=payload, requester="test"))
    assert ok is True
    assert gate.last_decision.edited is True
    assert payload == {"reply": "EDITED TEXT"}
