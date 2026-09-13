"""Scheduler tests — pure functions over config/dispatch.availability.yaml."""

from datetime import datetime

from core.config import load_yaml, repo_root
from packages.dispatch.models import Slot, WorkOrder
from packages.dispatch.schedule import (
    load_availability, propose_slots, slot_conflicts,
)

NOW = datetime(2026, 9, 13, 12, 0)  # Sunday


def _avail():
    return load_availability(load_yaml(repo_root() / "config" / "dispatch.availability.yaml"))


def _order(**kw):
    base = dict(source_id="WO-T", customer_name="Test Co", site_address="1 Main St",
                trade="plumbing", priority="high",
                sla_start="2026-09-14 08:00", sla_end="2026-09-16 17:00")
    return WorkOrder(**{**base, **kw})


def test_proposes_slot_within_sla_and_shift():
    slots = propose_slots(_order(), _avail(), now=NOW)
    assert slots, "expected at least one proposal"
    s = slots[0]
    assert s.tech == "Ray"                       # plumbing -> Ray
    assert s.start >= "2026-09-14"               # not on Sunday
    assert s.start == "2026-09-14 11:00"         # after Ray's 9-11 booking
    assert s.end == "2026-09-14 13:00"           # 2h job


def test_routes_around_existing_booking_and_blackout():
    # Ray is booked Mon 9-11; a 2h job can't start before 11:00.
    slots = propose_slots(_order(), _avail(), now=NOW)
    assert not any(s.start == "2026-09-14 09:00" for s in slots)
    # Tuesday 12:00-13:30 is a blackout — no slot may overlap it.
    for s in slots:
        if s.start.startswith("2026-09-15"):
            assert not ("12:00" <= s.start.split()[1] < "13:30")


def test_emergency_searches_immediately():
    order = _order(priority="emergency", sla_start="", sla_end="")
    slots = propose_slots(order, _avail(), now=NOW)
    assert slots[0].start.startswith("2026-09-14")  # next working day


def test_trade_routing():
    slots = propose_slots(_order(trade="hvac"), _avail(), now=NOW)
    assert all(s.tech == "Dee" for s in slots)


def test_no_tech_for_unknown_trade():
    assert propose_slots(_order(trade="roofing"), _avail(), now=NOW) == []


def test_conflict_detection():
    avail = _avail()
    bad = Slot(tech="Ray", start="2026-09-14 10:00", end="2026-09-14 12:00")
    reasons = slot_conflicts(bad, avail)
    assert any("conflict" in r for r in reasons)
    blackout = Slot(tech="Dee", start="2026-09-15 12:00", end="2026-09-15 14:00")
    assert any("blackout" in r for r in slot_conflicts(blackout, avail))
