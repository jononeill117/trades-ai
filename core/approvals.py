"""Approval gate — every customer-facing outbound action passes through here.

The rule: no approval, no send. Email, SMS, review replies, quote delivery,
and social/GBP posts all call `gate.require(...)` first and act only on an
approved decision.

Backends (config/approvals.yaml):

- `mock`   — mock mode. Prints the exact request that would be submitted and
             decides per `mock_decision` (default approve, so demos flow).
- `cli`    — prints the payload on the terminal and waits for y/n. The
             default live backend.
- `slack`  — posts the request to the ops webhook, then waits for a decision
             file at out/approvals/<id>.json ({"approved": true, "by": "..."})
             until `slack_timeout_s`. True interactive Slack buttons need a
             Slack app with an interactivity endpoint; the file-drop contract
             is the v1 seam — a small listener can write those files from
             Slack actions. Times out -> denied (fail closed).

Every request and decision lands in the run log: requester, action, payload
summary, decision, timestamp, and the resulting action taken.
"""

from __future__ import annotations

import asyncio
import json
import sys
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
    id: str = field(default_factory=lambda: f"apr-{uuid.uuid4().hex[:8]}")
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))


@dataclass
class ApprovalDecision:
    approved: bool
    decided_by: str
    note: str = ""
    ts: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))


class ApprovalBackend(Protocol):
    name: str
    async def decide(self, req: ApprovalRequest) -> ApprovalDecision: ...


class MockApprovalBackend:
    """Mock mode: print the exact request, decide per config."""
    name = "mock"

    def __init__(self, decision: str = "approve"):
        self._approved = decision != "deny"

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
        return ApprovalDecision(self._approved, decided_by="mock", note="auto per config")


class CliApprovalBackend:
    """Terminal y/n — the live-mode default."""
    name = "cli"

    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        print(f"\n=== APPROVAL REQUIRED ({req.id}) ===", file=sys.stderr)
        print(f"  action:    {req.action} via {req.channel}", file=sys.stderr)
        print(f"  requester: {req.requester}", file=sys.stderr)
        print(f"  summary:   {req.summary}", file=sys.stderr)
        if req.payload:
            for k, v in req.payload.items():
                print(f"  {k}: {v}", file=sys.stderr)
        try:
            answer = await asyncio.to_thread(
                input, f"Approve {req.action}? [y/N] ")
        except (EOFError, OSError):
            return ApprovalDecision(False, decided_by="cli", note="no tty — fail closed")
        ok = answer.strip().lower() in ("y", "yes")
        return ApprovalDecision(ok, decided_by="cli", note=answer.strip())


class SlackApprovalBackend:
    """Posts the request to Slack, then waits for a decision file.

    Incoming webhooks can't receive button clicks, so v1 uses a file-drop
    contract: a listener (or a human) writes out/approvals/<id>.json —
    {"approved": true|false, "by": "<name>"}. Timeout -> deny.
    """
    name = "slack"

    def __init__(self, notifier, timeout_s: int = 300, decisions_dir: Path | None = None):
        self._slack = notifier
        self._timeout = timeout_s
        self._dir = decisions_dir or repo_root() / "out" / "approvals"

    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        text = (f":lock: *Approval needed* `{req.id}`\n"
                f"*{req.action}* via `{req.channel}` — requested by {req.requester}\n"
                f"{req.summary}\n"
                f"Reply by writing `out/approvals/{req.id}.json` "
                f'({"{"}"approved": true, "by": "you"{"}"}) — or deny by doing nothing.')
        res = await self._slack.send(text)
        if res.status == "skipped":
            return ApprovalDecision(False, decided_by="slack",
                                    note=f"slack not configured: {res.detail}")
        decision_file = self._dir / f"{req.id}.json"
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if decision_file.exists():
                try:
                    data = json.loads(decision_file.read_text())
                    return ApprovalDecision(bool(data.get("approved")),
                                            decided_by=f"slack:{data.get('by', '?')}",
                                            note=str(data.get("note", "")))
                except json.JSONDecodeError:
                    return ApprovalDecision(False, decided_by="slack",
                                            note="unparseable decision file — fail closed")
            await asyncio.sleep(2)
        return ApprovalDecision(False, decided_by="slack",
                                note=f"no decision within {self._timeout}s — fail closed")


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

    async def require(self, action: str, channel: str, summary: str,
                      payload: dict[str, Any] | None = None,
                      requester: str = "agent") -> bool:
        req = ApprovalRequest(action=action, channel=channel, summary=summary,
                              payload=payload or {}, requester=requester)
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
        if self.run_log is not None:
            self.run_log.step(
                "approval",
                status="ok" if decision.approved else "skipped",
                id=req.id, action=action, channel=channel,
                requester=req.requester,
                approved=decision.approved, decided_by=decision.decided_by,
                note=decision.note, requested_at=req.ts, decided_at=decision.ts,
            )
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
