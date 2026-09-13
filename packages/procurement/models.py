"""Data shapes for the procurement pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Part:
    sku: str
    description: str
    qty: int = 1
    # e.g. "must be in stock", "name-brand only" — adapters may use these
    requirements: dict[str, Any] = field(default_factory=dict)


@dataclass
class Offer:
    """One supplier's price for one part."""
    supplier: str
    part_sku: str
    unit_price: Optional[float]      # None when we couldn't get a price
    currency: str = "USD"
    url: str = ""
    in_stock: bool = True
    login_required: bool = False     # pricing sits behind a login
    matched_desc: str = ""           # what the site actually showed
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuoteLine:
    part: Part
    winner: Optional[Offer]
    offers: list[Offer]
    line_total: float
    baseline_total: float            # what we'd pay at the baseline supplier
    savings: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["part"] = asdict(self.part)
        d["winner"] = asdict(self.winner) if self.winner else None
        d["offers"] = [o.to_dict() for o in self.offers]
        return d
