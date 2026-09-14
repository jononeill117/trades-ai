"""Pricebook — configuration, not model knowledge.

The iron rule: **the model never invents pricing.** Line items come only from
this CSV with stable item IDs; the model/matcher may *propose* which IDs
apply, and anything uncertain is flagged for a human. Totals are computed
from pricebook prices — never generated.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Item:
    item_id: str
    description: str
    trade: str
    unit: str
    unit_price: float
    taxable: bool = True


def load_pricebook(path: Path) -> dict[str, Item]:
    items = {}
    for row in csv.DictReader(path.read_text().splitlines()):
        if not (row.get("item_id") or "").strip():
            continue
        items[row["item_id"].strip()] = Item(
            item_id=row["item_id"].strip(),
            description=(row.get("description") or "").strip(),
            trade=(row.get("trade") or "any").strip(),
            unit=(row.get("unit") or "each").strip(),
            unit_price=float(row.get("unit_price") or 0),
            taxable=str(row.get("taxable", "true")).lower() in ("true", "1", "yes"),
        )
    return items


KEYWORDS = {
    "WH-50-NG": ["water heater", "50 gal", "50 gallon", "hot water tank"],
    "WH-EXP-TANK": ["expansion tank"],
    "DRAIN-AUGER": ["drain", "clog", "main line", "backup"],
    "FLAPPER": ["flapper", "toilet keeps running", "running toilet"],
    "AC-RECHARGE": ["refrigerant", "recharge", "not cooling", "low on freon"],
    "AC-CAPACITOR": ["capacitor", "condenser won't start", "fan not spinning"],
    "FURNACE-TUNE": ["furnace tune", "tune-up", "co test", "maintenance"],
    "GFCI-OUTLET": ["gfci", "outlet"],
    "PANEL-100A": ["panel upgrade", "100a", "service panel"],
    "EV-50A": ["ev charger", "50a", "car charger"],
    "CEIL-FAN": ["ceiling fan"],
    "DISPATCH-FEE": [],   # never auto-matched; add explicitly
}


def match_items(description: str, pricebook: dict[str, Item],
                explicit_qty: dict[str, int] | None = None) -> tuple[list[dict], list[dict]]:
    """Propose line items from the job description.

    Returns (matched, uncertain):
      matched   — [{item_id, qty, reason}] confident keyword hits
      uncertain — [{item_id, reason}] weak/ambiguous hits for human selection

    Explicit quantities in the job record always win (the human already chose).
    """
    text = description.lower()
    matched, uncertain = [], []
    for item_id, keywords in KEYWORDS.items():
        if item_id not in pricebook:
            continue
        hits = [k for k in keywords if k in text]
        if not hits and not (explicit_qty or {}).get(item_id):
            continue
        entry = {"item_id": item_id,
                 "qty": (explicit_qty or {}).get(item_id, 1),
                 "reason": f"keywords: {', '.join(hits)}" if hits
                          else "explicit quantity in job record"}
        # A single weak keyword ("outlet") is a suggestion, not a line item.
        if hits and all(len(h.split()) < 2 for h in hits) and len(hits) == 1:
            uncertain.append(entry)
        else:
            matched.append(entry)
    return matched, uncertain
