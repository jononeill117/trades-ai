"""Data shapes for the invoice chaser.

Invoice lifecycle states: aging -> contacted -> promised -> paid.
Side exits: disputed, escalated. A state only moves to `paid` when a supplied
payment/status event says so — the pipeline never claims recovered revenue on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATES = ("aging", "contacted", "promised", "paid", "disputed", "escalated")


@dataclass
class Invoice:
    invoice_id: str
    customer: str
    email: str = ""
    phone: str = ""
    amount: float = 0.0
    issued: str = ""
    due: str = ""
    status: str = "aging"          # from the AR file; see STATES
    warnings: list[str] = field(default_factory=list)

    def days_overdue(self, today) -> int:
        from datetime import datetime
        try:
            due = datetime.strptime(self.due, "%Y-%m-%d").date()
        except ValueError:
            return 0
        return max(0, (today - due).days)


@dataclass
class Reminder:
    """The next touch an invoice is due for."""
    invoice_id: str
    step: int                       # 1..N from the configured sequence
    channel: str                    # email | sms
    subject: str
    body: str
    tone: str
