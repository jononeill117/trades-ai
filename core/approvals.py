"""Approval gate — every customer-facing outbound action passes through here.

The rule: no approval, no send. Email, SMS, review replies, quote delivery,
and social/GBP posts all call `gate.require(...)` first and act only on an
approved decision.

Backends (config/approvals.yaml):

- `mock`   — mock mode. Prints the exact request that would be submitted and
             decides per `mock_decision` (default approve, so demos flow).
- `cli`    — prints the payload on the terminal and waits for y/n. The
             default live backend. Answering `e` opens the payload in
             $EDITOR; only the edited payload can then be approved, and
             only after a second confirmation of exactly what will be
             sent. No editor, a failed edit, or no confirmation -> denied.
             Actions whose payload is a structured object the sender does
             not consume as fields (the booking gates) pass
             `editable=False`: `e` is then refused with an explanation
             and the request is denied — never silently approved with
             the edit ignored.
- `slack`  — posts the request to the ops webhook, then waits for a decision
             file at out/approvals/<id>.json ({"approved": true, "by": "..."})
             until `slack_timeout_s`. True interactive Slack buttons need a
             Slack app with an interactivity endpoint; the file-drop contract
             is the v1 seam — a small listener can write those files from
             Slack actions. Decision files must carry the per-request
             `token` printed in the Slack message (only its hash is kept,
             in memory); a file without the matching token is ignored, so
             merely being able to write into out/approvals/ cannot forge a
             decision. Times out -> denied (fail closed).

Every request and decision lands in the run log: requester, action, payload
summary, decision, timestamp, and the resulting action taken.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import load_yaml, repo_root

APPROVALS_CONFIG = "config/approvals.yaml"


@dataclass
class ApprovalRequest:
    action: str                 # e.g. "sms.send", "review.publish", "quote.deliver"
    channel: str                # e.g. "quo-sms", "gmail", "gbp"
    summary: str                # one line a human can approve on
    payload: dict[str, Any] = field(default_factory=dict)
    requester: str = "agent"
    # False when the caller cannot honor an edited payload (the booking
    # gates: the send proceeds from order/slot objects, not from payload
    # fields). Backends must refuse edits for such requests — approving
    # while dropping the edit is the blind-approval bug, in reverse.
    editable: bool = True
    id: str = field(default_factory=lambda: f"apr-{uuid.uuid4().hex[:8]}")
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))


@dataclass
class ApprovalDecision:
    approved: bool
    decided_by: str
    note: str = ""
    edited: bool = False        # approver changed the payload before approving
    # The payload as approved, when the approver edited it. The gate copies
    # this into the caller's payload dict in place, so what gets sent is
    # what the human actually saw and confirmed — never the pre-edit draft.
    payload: dict[str, Any] | None = None
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))


class ApprovalBackend(Protocol):
    name: str
    async def decide(self, req: ApprovalRequest) -> ApprovalDecision: ...


class MockApprovalBackend:
    """Mock mode: print the exact request, decide per config."""
    name = "mock"

    def __init__(self, decision: str = "approve"):
        self._approved = decision != "deny"
        self._edited = decision == "edit"

    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        print(f"[plan] approval required ({req.id})")
        print(f"[plan]   action:    {req.action} via {req.channel}")
        print(f"[plan]   requester: {req.requester}")
        print(f"[plan]   summary:   {req.summary}")
        if req.payload:
            preview = json.dumps(req.payload, default=str)
            print(f"[plan]   payload:   {preview[:300]}")
        verdict = "approve" if self._approved else "deny"
        print(f"[plan]   mock decision: {verdict} (config/approvals.yaml: mock_decision)")
        return ApprovalDecision(self._approved, decided_by="mock",
                                note="auto per config",
                                edited=self._edited)


class CliApprovalBackend:
    """Terminal y/n — the live-mode default.

    `e` is a real edit, not an approval: the payload opens in $EDITOR (or
    $VISUAL), and afterwards the approver must confirm the EDITED payload
    as displayed. The decision then carries the edited payload, and the
    gate hands exactly that to the sender. No editor configured, an editor
    error, invalid edited JSON, or no confirmation all deny — the original
    draft is never sent on an edit answer. For requests marked
    `editable=False` (the booking gates), `e` is refused with an
    explanation and the request denied, for the same reason.
    """
    name = "cli"

    def __init__(self, input_fn=None, editor: str | None = None):
        # Injectable for tests; defaults are the real terminal and $EDITOR.
        self._input = input_fn or input
        self._editor = editor

    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        print(f"\n=== APPROVAL REQUIRED ({req.id}) ===", file=sys.stderr)
        print(f"  action:    {req.action} via {req.channel}", file=sys.stderr)
        print(f"  requester: {req.requester}", file=sys.stderr)
        print(f"  summary:   {req.summary}", file=sys.stderr)
        if req.payload:
            for k, v in req.payload.items():
                print(f"  {k}: {v}", file=sys.stderr)
        prompt = (f"Approve {req.action}? [y/e(dit)/N] " if req.editable
                  else f"Approve {req.action}? [y/N] ")
        try:
            answer = await asyncio.to_thread(self._input, prompt)
        except (EOFError, OSError):
            return ApprovalDecision(False, decided_by="cli", note="no tty — fail closed")
        a = answer.strip().lower()
        if a in ("e", "edit", "edited"):
            if not req.editable:
                # The caller sends from structured objects, not from this
                # payload, so an edit here could never reach the send.
                # Refuse loudly and fail closed: the operator approves
                # the booking exactly as shown, or denies it and fixes
                # the order upstream. Silently approving the unedited
                # payload on an edit answer is the one forbidden outcome.
                print(f"  edit refused: {req.action} is not editable — its "
                      f"payload is a structured booking, not send fields. "
                      f"Approve it exactly as shown, or deny it and fix "
                      f"the order upstream.", file=sys.stderr)
                return ApprovalDecision(
                    False, decided_by="cli",
                    note="edit refused — action is not editable; "
                         "nothing sent")
            return await self._edit_flow(req)
        ok = a in ("y", "yes")
        return ApprovalDecision(ok, decided_by="cli", note=answer.strip())

    async def _edit_flow(self, req: ApprovalRequest) -> ApprovalDecision:
        edited = await asyncio.to_thread(self._edit_payload, req.payload)
        if edited is None:
            return ApprovalDecision(
                False, decided_by="cli",
                note="edit unavailable or aborted — fail closed, nothing sent")
        print(f"\n=== EDITED PAYLOAD ({req.id}) — this is what will be sent ===",
              file=sys.stderr)
        for k, v in edited.items():
            print(f"  {k}: {v}", file=sys.stderr)
        try:
            answer = await asyncio.to_thread(
                self._input, f"Send the EDITED {req.action} payload? [y/N] ")
        except (EOFError, OSError):
            return ApprovalDecision(False, decided_by="cli",
                                    note="no tty at edit confirmation — fail closed")
        if answer.strip().lower() not in ("y", "yes"):
            return ApprovalDecision(False, decided_by="cli",
                                    note="edited payload not confirmed — nothing sent")
        return ApprovalDecision(True, decided_by="cli", edited=True,
                                note="edited in $EDITOR and confirmed",
                                payload=edited)

    def _edit_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Open the payload JSON in the editor; return the edited dict, or
        None when editing is unavailable/fails/aborted (caller denies)."""
        editor = self._editor or os.environ.get("VISUAL") \
            or os.environ.get("EDITOR")
        if not editor:
            return None
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", prefix="approval-edit-",
                    delete=False) as fh:
                json.dump(payload, fh, indent=2, default=str)
                path = Path(fh.name)
            res = subprocess.run([*shlex.split(editor), str(path)],
                                 check=False)
            if res.returncode != 0:
                return None
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


class SlackApprovalBackend:
    """Posts the request to Slack, then waits for a decision file.

    Incoming webhooks can't receive button clicks, so v1 uses a file-drop
    contract: a listener (or a human) writes out/approvals/<id>.json —
    {"approved": true|false, "by": "<name>", "token": "<token>"}.

    The token is the binding between request and decision: a fresh
    unguessable token is minted per request, printed only in the Slack
    message, and only its SHA-256 hash is kept here (in memory). A
    decision file without the matching token is ignored — write access to
    out/approvals/ alone cannot forge a decision; the writer must also
    have seen the Slack message. Timeout -> deny.
    """
    name = "slack"

    def __init__(self, notifier, timeout_s: int = 300, decisions_dir: Path | None = None):
        self._slack = notifier
        self._timeout = timeout_s
        self._dir = decisions_dir or repo_root() / "out" / "approvals"
        self._token_hashes: dict[str, str] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        token = secrets.token_urlsafe(24)
        self._token_hashes[req.id] = self._hash(token)
        try:
            text = (f":lock: *Approval needed* `{req.id}`\n"
                    f"*{req.action}* via `{req.channel}` — requested by {req.requester}\n"
                    f"{req.summary}\n"
                    f"Reply by writing `out/approvals/{req.id}.json` "
                    f'({"{"}"approved": true, "by": "you", '
                    f'"token": "{token}"{"}"}) — the token binds the '
                    f"decision to this request; without it the file is "
                    f"ignored. Or deny by doing nothing.")
            res = await self._slack.send(text)
            if res.status == "skipped":
                return ApprovalDecision(False, decided_by="slack",
                                        note=f"slack not configured: {res.detail}")
            decision_file = self._dir / f"{req.id}.json"
            deadline = time.monotonic() + self._timeout
            warned: str | None = None  # content hash already warned about
            while time.monotonic() < deadline:
                if decision_file.exists():
                    raw = decision_file.read_text()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        return ApprovalDecision(
                            False, decided_by="slack",
                            note="unparseable decision file — fail closed")
                    presented = data.get("token")
                    if not isinstance(presented, str) or not hmac.compare_digest(
                            self._hash(presented), self._token_hashes[req.id]):
                        # Unbound file: a forgery attempt or a stale drop.
                        # Never honored; keep waiting for the real decision
                        # (denying here would let a forger veto approvals).
                        digest = self._hash(raw)
                        if digest != warned:
                            warned = digest
                            print(f"[approvals] ignoring decision file for "
                                  f"{req.id}: missing/wrong token",
                                  file=sys.stderr)
                        await asyncio.sleep(2)
                        continue
                    edited_payload = data.get("payload")
                    if isinstance(edited_payload, dict) and not req.editable:
                        # Same rule as the CLI backend: an edit this
                        # caller cannot honor must never be dropped
                        # silently while the original is approved.
                        return ApprovalDecision(
                            False, decided_by=f"slack:{data.get('by', '?')}",
                            note="edited payload refused — action is not "
                                 "editable; nothing sent")
                    edited = isinstance(edited_payload, dict) \
                        and bool(data.get("approved"))
                    return ApprovalDecision(
                        bool(data.get("approved")),
                        decided_by=f"slack:{data.get('by', '?')}",
                        note=str(data.get("note", "")),
                        edited=edited,
                        payload=edited_payload if edited else None)
                await asyncio.sleep(2)
            return ApprovalDecision(
                False, decided_by="slack",
                note=f"no decision within {self._timeout}s — fail closed")
        finally:
            self._token_hashes.pop(req.id, None)


class ApprovalGate:
    """The thing packages call. Fail closed: exceptions and missing backends
    both mean denied."""

    def __init__(self, backend: ApprovalBackend, run_log=None,
                 preapproved: list[str] | None = None):
        self.backend = backend
        self.run_log = run_log
        # Action patterns a deployer has pre-approved (e.g. a transactional
        # SMS template that is legally safe to send unattended). Exact-match
        # on action name; the payload is still logged either way.
        self.preapproved = set(preapproved or [])
        self.last_decision: ApprovalDecision | None = None

    async def require(self, action: str, channel: str, summary: str,
                      payload: dict[str, Any] | None = None,
                      requester: str = "agent",
                      editable: bool = True) -> bool:
        """Ask for approval. Returns True only when approved.

        Payload contract: pass the dict you will send from. If the
        approver edits the payload, the approved (edited) payload is
        copied back into that same dict in place before this returns —
        so send from the dict, never from values captured before the
        call, or you would send a draft the human never approved.

        Callers that cannot send from payload fields (the booking gates,
        whose sends proceed from structured order/slot objects) must
        pass `editable=False`: edit answers are then refused with an
        explanation and the request denied, and a backend that returns
        an edited payload anyway fails closed here — an edit is never
        silently dropped while the original goes out.
        """
        payload = payload if payload is not None else {}
        req = ApprovalRequest(action=action, channel=channel, summary=summary,
                              payload=payload, requester=requester,
                              editable=editable)
        if action in self.preapproved:
            decision = ApprovalDecision(True, decided_by="preapproved",
                                        note=f"{action} is in preapproved_actions")
        else:
            if self.run_log is not None:
                self.run_log.step("approval_request", status="would", id=req.id,
                                  action=action, channel=channel, summary=summary[:200])
            try:
                decision = await self.backend.decide(req)
            except Exception as exc:
                decision = ApprovalDecision(False, decided_by=self.backend.name,
                                            note=f"backend error: {exc} — fail closed")
        if decision.approved and decision.payload is not None \
                and not editable:
            # Defense in depth: a backend returned an edit for an action
            # whose caller cannot apply it. Approving would send the
            # original while the human believes the edit went out.
            decision = ApprovalDecision(
                False, decided_by=decision.decided_by,
                note="backend returned an edited payload for a "
                     "non-editable action — fail closed, nothing sent")
        if decision.approved and decision.payload is not None:
            # The human approved the edited payload, not the draft: swap
            # it into the caller's dict so the send uses what was seen.
            payload.clear()
            payload.update(decision.payload)
        if self.run_log is not None:
            self.run_log.step(
                "approval",
                status="ok" if decision.approved else "skipped",
                id=req.id, action=action, channel=channel,
                requester=req.requester,
                approved=decision.approved, decided_by=decision.decided_by,
                edited=decision.edited,
                note=decision.note, requested_at=req.ts, decided_at=decision.ts,
            )
        self.last_decision = decision
        return decision.approved


def get_gate(mode: str, run_log=None, cfg: dict | None = None) -> ApprovalGate:
    """Build the gate for this run from config/approvals.yaml.

    backend: auto (default) -> mock in mock mode, cli in live mode.
    """
    merged = {**load_yaml(repo_root() / APPROVALS_CONFIG), **(cfg or {})}
    backend_name = merged.get("backend", "auto")
    if backend_name == "auto":
        backend_name = "mock" if mode == "mock" else "cli"

    if backend_name == "mock":
        backend: ApprovalBackend = MockApprovalBackend(merged.get("mock_decision", "approve"))
    elif backend_name == "cli":
        backend = CliApprovalBackend()
    elif backend_name == "slack":
        from .notify import SlackNotifier

        backend = SlackApprovalBackend(
            SlackNotifier(), timeout_s=int(merged.get("slack_timeout_s", 300)))
    else:
        # Unknown backend -> fail closed, but loudly.
        class _DenyAll:
            name = f"unknown:{backend_name}"
            async def decide(self, req):
                return ApprovalDecision(False, decided_by=self.name,
                                        note="unknown backend — fail closed")
        backend = _DenyAll()

    return ApprovalGate(backend, run_log=run_log,
                        preapproved=merged.get("preapproved_actions") or [])
