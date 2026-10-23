"""Missed-call textback booking-truthfulness tests (WS7 T-2 follow-up).

The textback flow used to count results["booked"] — and ask the gate to
approve a "You're booked" SMS — straight after create_job, without ever
running the portal's confirm step: a job the portal still held as merely
"scheduled" (or pending_board) was counted and announced as booked.
These pins hold the flow to the T-2 dispatch standard:

  * a job whose portal confirmation status does not start with "booked"
    is never counted and never notified (the confirmation-SMS approval
    is not even requested);
  * when the portal DOES confirm, the count fires and the confirmation
    approval payload carries the real job_status;
  * a GUI-only confirm defers to the desktop fallback, and an unverified
    fallback result ("pending_board ...") is never counted;
  * two bookable calls in one run never land in the same tech/slot — a
    created job is recorded back into the run's availability.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.audit import RunLog
from core.mock_solari import MockSolari
from core.notify.base import SendResult
from packages.dispatch.models import NeedsDesktopError
from packages.missed_call_textback import agent
from packages.missed_call_textback.state import ProcessedState


class _RecordingGate:
    """Approves everything; remembers every request + payload."""

    def __init__(self):
        self.calls: list[dict] = []

    async def require(self, action, channel, summary, payload=None,
                      requester="agent", editable=True):
        self.calls.append({"action": action, "channel": channel,
                           "payload": payload or {}})
        return True


class _StubAdapter:
    """Portal adapter: create succeeds; confirm returns a fixed status
    or raises NeedsDesktopError, as configured."""

    name = "stub"

    def __init__(self, confirm_status: str = "booked", *,
                 needs_desktop: bool = False):
        self.confirm_status = confirm_status
        self.needs_desktop = needs_desktop
        self.bookings: list[tuple[str, object]] = []
        self.confirm_calls: list[str] = []

    async def login(self, driver) -> bool:
        return True

    async def create_job(self, driver, order, slot) -> str:
        self.bookings.append((order.source_id, slot))
        return f"http://portal.test/jobs/JOB-{len(self.bookings)}"

    async def confirm_booking(self, driver, job_ref) -> str:
        self.confirm_calls.append(job_ref)
        if self.needs_desktop:
            raise NeedsDesktopError(f"{job_ref} requires dispatch-board confirmation")
        return self.confirm_status


class _RecordingNotifier:
    channel = "quo-sms"

    def __init__(self):
        self.sent: list[tuple] = []

    async def send(self, *args, **kwargs):
        self.sent.append(args)
        return SendResult("sent", self.channel, "ok")


class _DummyServer:
    def shutdown(self):
        pass


def _bookable_call(cid: str, number: str, name: str) -> dict:
    return {"id": cid, "from": number, "caller_name": name,
            "voicemail_transcript":
                "Hi, my water heater is leaking all over the garage floor. "
                "Can someone come out today?"}


def _run_agent(tmp_path, monkeypatch, *, adapter, calls, fallback=None):
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(calls))

    gate = _RecordingGate()
    notifier = _RecordingNotifier()
    monkeypatch.setattr(agent, "ProcessedState",
                        lambda path: ProcessedState(tmp_path / "fresh.json"))
    monkeypatch.setattr(agent, "get_gate", lambda mode, run_log: gate)
    monkeypatch.setattr(agent, "serve",
                        lambda seed: (_DummyServer(), "http://portal.test"))
    monkeypatch.setattr(agent, "get_adapter", lambda name, url: adapter)
    monkeypatch.setattr(agent, "QuoNotifier", lambda: notifier)
    if fallback is not None:
        async def _confirm(core, run_log, board_url, portal_base_url=""):
            return fallback
        monkeypatch.setattr(agent, "desktop_fallback", SimpleNamespace(
            confirm_on_dispatch_board=_confirm))

    run_log = RunLog("missed-call-textback", "mock", runs_dir=tmp_path)
    result = asyncio.run(agent.run(MockSolari(), run_log, {
        "mode": "mock", "events": str(events_path),
    }))
    events = [json.loads(line)
              for line in Path(run_log.path).read_text().splitlines()]
    return result, gate, notifier, events


def test_unconfirmed_status_is_neither_counted_nor_notified(
        tmp_path, monkeypatch):
    adapter = _StubAdapter(confirm_status="pending_board")
    result, gate, notifier, events = _run_agent(
        tmp_path, monkeypatch, adapter=adapter,
        calls=[_bookable_call("c1", "+15555550100", "Rosa")])

    assert result["booked"] == 0
    assert result["processed"] == 1
    # The confirm step now runs and its real status is on the record.
    assert adapter.confirm_calls == ["http://portal.test/jobs/JOB-1"]
    confirm_steps = [e for e in events if e.get("step") == "confirm"]
    assert confirm_steps and \
        confirm_steps[-1]["detail"]["job_status"] == "pending_board"
    # The booking approval ran, but the "You're booked" SMS approval was
    # never even requested — and nothing beyond the first text was sent.
    actions = [c["action"] for c in gate.calls]
    assert actions == ["sms.textback", "booking.create"]
    assert len(notifier.sent) == 1
    assert "booked for" not in notifier.sent[0][1]


def test_confirmed_status_counts_and_notifies_with_job_status(
        tmp_path, monkeypatch):
    adapter = _StubAdapter(confirm_status="booked")
    result, gate, notifier, events = _run_agent(
        tmp_path, monkeypatch, adapter=adapter,
        calls=[_bookable_call("c1", "+15555550100", "Rosa")])

    assert result["booked"] == 1
    payloads = {c["action"]: c["payload"] for c in gate.calls}
    assert payloads["sms.booking_confirm"]["job_status"] == "booked"
    assert payloads["sms.booking_confirm"]["job_ref"] == \
        "http://portal.test/jobs/JOB-1"
    assert len(notifier.sent) == 2
    assert "You're booked for" in notifier.sent[1][1]


def test_desktop_fallback_unverified_is_not_counted(tmp_path, monkeypatch):
    adapter = _StubAdapter(needs_desktop=True)
    result, gate, notifier, events = _run_agent(
        tmp_path, monkeypatch, adapter=adapter,
        calls=[_bookable_call("c1", "+15555550100", "Rosa")],
        fallback="pending_board (unverified — check the recording)")

    assert result["booked"] == 0
    assert result["processed"] == 1
    assert "sms.booking_confirm" not in [c["action"] for c in gate.calls]
    assert len(notifier.sent) == 1


def test_desktop_fallback_confirmed_counts(tmp_path, monkeypatch):
    adapter = _StubAdapter(needs_desktop=True)
    result, gate, notifier, events = _run_agent(
        tmp_path, monkeypatch, adapter=adapter,
        calls=[_bookable_call("c1", "+15555550100", "Rosa")],
        fallback="booked")

    assert result["booked"] == 1
    assert len(notifier.sent) == 2


def test_two_bookable_calls_never_share_a_slot(tmp_path, monkeypatch):
    adapter = _StubAdapter(confirm_status="booked")
    result, gate, notifier, events = _run_agent(
        tmp_path, monkeypatch, adapter=adapter,
        calls=[_bookable_call("c1", "+15555550100", "Rosa"),
               _bookable_call("c2", "+15555550101", "Sam")])

    assert result["booked"] == 2
    slots = [(slot.tech, slot.start, slot.end)
             for _src, slot in adapter.bookings]
    assert len(slots) == 2
    assert slots[0] != slots[1], (
        f"second call was booked into the slot the first one took: {slots}")
