"""Data shapes for missed-call textback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MissedCall:
    """A missed-call event — from a phone-system webhook or a fixture file."""
    id: str
    from_number: str
    ts: str = ""
    caller_name: str = ""
    voicemail_transcript: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MissedCall":
        return cls(
            id=d["id"], from_number=d.get("from", ""),
            ts=d.get("ts", ""), caller_name=d.get("caller_name", ""),
            voicemail_transcript=d.get("voicemail_transcript", ""),
        )


@dataclass
class Qualification:
    """What the qualifier decided about the call."""
    trade: str = ""                    # plumbing | hvac | electrical | ""
    urgency: str = "normal"            # emergency | high | normal
    bookable: bool = False             # enough signal to book without a human
    escalate: bool = False
    escalate_reason: str = ""
    summary: str = ""                  # one-line need summary for the booking
    matched_keywords: list[str] = field(default_factory=list)
