"""Dispatch agent booking-truthfulness tests.

Three pins:
  * a job whose portal status is not "booked" is never counted and never
    notified (no Slack/email/SMS approval is even requested);
  * when the portal DOES confirm, the notification approvals carry the
    real job_status in their payloads for the human approver to see;
  * two orders in one run never land in the same tech/slot — a completed
    booking is recorded back into the run's availability.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.audit import RunLog
from core.mock_solari import MockSolari
from core.notify.base import SendResult
from packages.dispatch import agent
from packages.dispatch.models import WorkOrder
from packages.dispatch.schedule import load_availability, propose_slots

AVAILABILITY_YAML = """\
slot_granularity_minutes: 60
technicians:
  - name: Ray
    trades: [plumbing]
    shifts:
      - days: [mon, tue, wed, thu, fri, sat, sun]
        start: "08:00"
        end: "17:00"
blackouts: []
existing_jobs: []
"""


class _RecordingGate:
    """Approves everything; remembers every request + payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def require(self, action, channel, summary, payload=None,
                      requester="agent"):
        self.calls.append({"action": action, "channel": channel,
                           "payload": payload or {}})
        return True


class _StubAdapter:
    """Portal adapter whose confirm returns a fixed status."""

    name = "stub"

    def __init__(self, status: str):
        self.status = status
        self.bookings: list[tuple[str, object]] = []

    async def login(self, driver) -> bool:
        return True

    async def create_job(self, driver, order, slot) -> str:
        self.bookings.append((order.source_id, slot))
        return f"http://portal.test/jobs/JOB-{len(self.bookings)}"

    async def confirm_booking(self, driver, job_ref) -> str:
        return self.status


class _RecordingNotifier:
    channel = "stub"

    def __init__(self):
        self.sent: list[tuple] = []

    def configured(self) -> bool:
        return True

    async def send(self, *args, **kwargs):
        self.sent.append(args)
        return SendResult("sent", self.channel, "ok")


def _order(source_id: str) -> WorkOrder:
    return WorkOrder(source_id=source_id, customer_name="Cust Co",
                     site_address="1 Main St", tenant_name="Tenant",
                     tenant_phone="+15555550100",
                     tenant_email="tenant@example.com", trade="plumbing",
                     priority="normal", duration_minutes=60)


def _run_agent(tmp_path, monkeypatch, *, confirm_status, orders):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in range(len(orders)):
        (inbox / f"wo-{i}.eml").write_text(
            f"From: a@example.com\nSubject: WO\nMessage-ID: <t{i}@x>\n\nbody\n")
    avail_path = tmp_path / "availability.yaml"
    avail_path.write_text(AVAILABILITY_YAML)

    gate = _RecordingGate()
    adapter = _StubAdapter(confirm_status)
    notifiers = {name: _RecordingNotifier()
                 for name in ("SlackNotifier", "GmailNotifier", "QuoNotifier")}
    queue = list(orders)

    async def fake_parse(core, path):
        return queue.pop(0)

    monkeypatch.setattr(agent, "get_gate", lambda mode, run_log: gate)
    monkeypatch.setattr(agent, "get_adapter", lambda name, url: adapter)
    monkeypatch.setattr(agent, "parse_email_in_sandbox", fake_parse)
    for name, stub in notifiers.items():
        monkeypatch.setattr(agent, name, lambda stub=stub: stub)

    run_log = RunLog("dispatch", "mock", runs_dir=tmp_path)
    result = asyncio.run(agent.run(MockSolari(), run_log, {
        "mode": "mock",
        "fixture_dir": str(inbox),
        "availability": str(avail_path),
    }))
    events = [json.loads(line)
              for line in Path(run_log.path).read_text().splitlines()]
    return result, gate, adapter, notifiers, events


def test_unbooked_status_is_never_counted_or_notified(tmp_path, monkeypatch):
    result, gate, adapter, notifiers, events = _run_agent(
        tmp_path, monkeypatch, confirm_status="pending_board",
        orders=[_order("WO-1")])

    assert result["booked"] == 0
    assert result["orders"][0]["status"] == "pending_board"
    # The booking approval ran, but no notification approval was requested.
    actions = [c["action"] for c in gate.calls]
    assert actions == ["dispatch.book"]
    # ...and no notifier was even invoked.
    assert all(not n.sent for n in notifiers.values())
    # The silence is on the record.
    assert any(e.get("step") == "notify" and e.get("status") == "skipped"
               for e in events)


def test_booked_status_notifies_and_payloads_carry_job_status(
        tmp_path, monkeypatch):
    result, gate, adapter, notifiers, events = _run_agent(
        tmp_path, monkeypatch, confirm_status="booked",
        orders=[_order("WO-1")])

    assert result["booked"] == 1
    payloads = {c["action"]: c["payload"] for c in gate.calls}
    assert payloads["dispatch.confirm.email"]["job_status"] == "booked"
    assert payloads["dispatch.confirm.sms"]["job_status"] == "booked"
    assert all(len(n.sent) == 1 for n in notifiers.values())


def test_two_orders_in_one_run_never_share_a_slot(tmp_path, monkeypatch):
    result, gate, adapter, notifiers, events = _run_agent(
        tmp_path, monkeypatch, confirm_status="booked",
        orders=[_order("WO-1"), _order("WO-2")])

    assert result["booked"] == 2
    slots = [(slot.tech, slot.start, slot.end)
             for _src, slot in adapter.bookings]
    assert len(slots) == 2
    assert slots[0] != slots[1], (
        f"second order was booked into the slot the first one took: {slots}")


def test_record_booking_removes_slot_from_later_proposals():
    avail = load_availability({
        "slot_granularity_minutes": 60,
        "technicians": [{
            "name": "Ray", "trades": ["plumbing"],
            "shifts": [{"days": ["mon", "tue", "wed", "thu", "fri", "sat",
                                 "sun"],
                        "start": "08:00", "end": "17:00"}],
        }],
        "blackouts": [], "existing_jobs": [],
    })
    from datetime import datetime

    from packages.dispatch.schedule import record_booking
    order = _order("WO-1")
    first = propose_slots(order, avail, now=datetime(2026, 9, 14, 7, 0))[0]
    record_booking(avail, first, "JOB-1")
    later = propose_slots(order, avail, now=datetime(2026, 9, 14, 7, 0))
    assert all((s.tech, s.start, s.end) != (first.tech, first.start, first.end)
               for s in later)
