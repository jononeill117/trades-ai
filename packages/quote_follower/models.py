"""Data shapes for quote-follower."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class Quote:
    quote_id: str
    customer: str
    email: str = ""
    phone: str = ""
    amount: float = 0.0
    created: str = ""
    status: str = "draft"            # draft | sent | won | lost
    last_activity: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Quote":
        try:
            amount = float(str(row.get("amount", "0")).replace(",", "")
                           .replace("$", "") or 0)
        except ValueError:
            amount = 0.0
        return cls(
            quote_id=(row.get("quote_id") or "").strip(),
            customer=(row.get("customer") or "").strip(),
            email=(row.get("email") or "").strip(),
            phone=(row.get("phone") or "").strip(),
            amount=amount,
            created=(row.get("created") or "").strip(),
            status=(row.get("status") or "draft").strip().lower(),
            last_activity=(row.get("last_activity") or "").strip(),
        )

    def days_idle(self, today: date) -> int:
        ref = self.last_activity or self.created
        try:
            return max(0, (today - datetime.strptime(ref, "%Y-%m-%d").date()).days)
        except ValueError:
            return 0

    def bucket(self, today: date, idle_days: int) -> str:
        """never_sent vs idle — the two distinct problems this package solves."""
        if self.status in ("draft", "created", "unsent"):
            return "never_sent"
        if self.status == "sent" and self.days_idle(today) >= idle_days:
            return "sent_inactive"
        return "active"
