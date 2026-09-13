"""Scheduling — pure functions, no I/O, fully unit-tested.

Given a work order and the shop's availability rules (who works when, which
trades they cover, blackout windows, jobs already booked), propose slots that
fit inside the SLA window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .models import Slot, WorkOrder

FMT = "%Y-%m-%d %H:%M"
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# How far ahead we look for a slot, by priority.
_HORIZON_DAYS = {"emergency": 1, "high": 3, "normal": 7, "low": 14}


@dataclass
class Shift:
    days: list[str]          # ["mon", ...]
    start: str               # "08:00"
    end: str                 # "17:00"


@dataclass
class Tech:
    name: str
    trades: list[str]
    shifts: list[Shift]


@dataclass
class Window:
    start: datetime
    end: datetime
    label: str = ""


@dataclass
class Availability:
    techs: list[Tech]
    blackouts: list[Window]
    bookings: list[Window]   # label = job ref
    slot_minutes: int = 60


def _dt(value: str) -> datetime:
    return datetime.strptime(value, FMT)


def load_availability(cfg: dict[str, Any]) -> Availability:
    """config/dispatch.availability.yaml -> Availability."""
    techs = [
        Tech(
            name=t["name"],
            trades=[str(x).lower() for x in t.get("trades", [])],
            shifts=[Shift(days=[d.lower() for d in s["days"]], start=s["start"], end=s["end"])
                    for s in t.get("shifts", [])],
        )
        for t in cfg.get("technicians", [])
    ]
    blackouts = [
        Window(_dt(b["start"]), _dt(b["end"]), b.get("reason", "blackout"))
        for b in cfg.get("blackouts", [])
    ]
    bookings = [
        Window(_dt(j["start"]), _dt(j["end"]), j.get("ref", "job"))
        for j in cfg.get("existing_jobs", [])
        # bookings are per-tech; store tech in label prefix
    ]
    # keep tech name on booking windows
    for job, win in zip(cfg.get("existing_jobs", []), bookings):
        win.label = f"{job.get('tech', '')}|{win.label}"
    return Availability(
        techs=techs,
        blackouts=blackouts,
        bookings=bookings,
        slot_minutes=int(cfg.get("slot_granularity_minutes", 60)),
    )


def _overlaps(a_start: datetime, a_end: datetime, b: Window) -> bool:
    return a_start < b.end and b.start < a_end


def _tech_bookings(avail: Availability, tech: str) -> list[Window]:
    return [w for w in avail.bookings if w.label.split("|")[0] in ("", tech)]


def slot_conflicts(slot: Slot, avail: Availability) -> list[str]:
    """Why a slot doesn't work: blackout overlaps, or the tech is booked."""
    start, end = _dt(slot.start), _dt(slot.end)
    reasons = []
    for b in avail.blackouts:
        if _overlaps(start, end, b):
            reasons.append(f"blackout: {b.label}")
    for j in _tech_bookings(avail, slot.tech):
        if _overlaps(start, end, j):
            reasons.append(f"conflict: {j.label.split('|')[-1]}")
    return reasons


def _shift_windows(tech: Tech, day: datetime) -> list[Window]:
    name = _DAYS[day.weekday()]
    out = []
    for s in tech.shifts:
        if name in s.days:
            sh, sm = map(int, s.start.split(":"))
            eh, em = map(int, s.end.split(":"))
            out.append(Window(
                day.replace(hour=sh, minute=sm, second=0, microsecond=0),
                day.replace(hour=eh, minute=em, second=0, microsecond=0),
                tech.name,
            ))
    return out


def propose_slots(
    order: WorkOrder,
    avail: Availability,
    *,
    now: Optional[datetime] = None,
    count: int = 3,
) -> list[Slot]:
    """Earliest-fit slot search: each tech who covers the trade, each working
    day inside the priority horizon, each granularity step inside their shift.

    Respects: shift windows, blackouts, existing bookings, and the SLA window
    (a slot must start no earlier than sla_start and end no later than
    sla_end, when those are set).
    """
    now = now or datetime.now()
    horizon = _HORIZON_DAYS.get(order.priority, 7)
    duration = timedelta(minutes=order.duration_minutes)
    step = timedelta(minutes=avail.slot_minutes)

    sla_start = _dt(order.sla_start) if order.sla_start else now
    sla_end = _dt(order.sla_end) if order.sla_end else None
    earliest = max(now, sla_start)

    techs = [t for t in avail.techs if not order.trade or order.trade in t.trades]
    if not techs:
        return []

    found: list[Slot] = []
    for offset in range(horizon + 1):
        day = (earliest + timedelta(days=offset)).replace(hour=0, minute=0)
        for tech in techs:
            for shift in _shift_windows(tech, day):
                t = max(shift.start, earliest)
                # align to granularity
                mins = t.hour * 60 + t.minute
                r = mins % avail.slot_minutes
                if r:
                    t += timedelta(minutes=avail.slot_minutes - r)
                while t + duration <= shift.end:
                    if sla_end and t + duration > sla_end:
                        break
                    if t + duration < sla_start:
                        t += step
                        continue
                    slot = Slot(tech=tech.name, start=t.strftime(FMT),
                                end=(t + duration).strftime(FMT))
                    if not slot_conflicts(slot, avail):
                        found.append(slot)
                        if len(found) >= count:
                            return found
                    t += step
    return found
