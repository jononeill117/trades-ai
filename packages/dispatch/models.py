"""Shared data shapes for the dispatch pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class RawEmail:
    """An email as it arrived — untrusted input."""
    message_id: str
    sender: str
    subject: str
    body: str
    path: str = ""                 # set when it came from an .eml file

    def to_eml(self) -> str:
        return f"From: {self.sender}\nSubject: {self.subject}\nMessage-ID: {self.message_id}\n\n{self.body}"


@dataclass
class WorkOrder:
    """A normalized work order — the thing the rest of the pipeline trusts."""
    source_id: str                       # e.g. the sender's work-order number
    customer_name: str
    site_address: str
    tenant_name: str = ""
    tenant_phone: str = ""
    tenant_email: str = ""
    trade: str = ""                      # plumbing | hvac | electrical | ...
    priority: str = "normal"             # emergency | high | normal | low
    sla_start: str = ""                  # ISO "YYYY-MM-DD HH:MM"
    sla_end: str = ""
    notes: str = ""
    duration_minutes: int = 120
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    raw_message_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkOrder":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Slot:
    """A proposed or booked appointment."""
    tech: str
    start: str                           # ISO "YYYY-MM-DD HH:MM"
    end: str

    def label(self) -> str:
        return f"{self.start}–{self.end.split(' ')[-1]} with {self.tech}"


class NeedsDesktopError(Exception):
    """Raised by a portal adapter when a step can't be done in a cloud browser
    and needs a real desktop (GUI app, OS dialog, hardware prompt)."""
