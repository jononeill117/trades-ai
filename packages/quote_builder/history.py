"""History — the corpus of past quotes and invoices this shop actually ran.

The iron rule stays: **the model never invents pricing.** Line items are
proposed by keyword rules and priced from the band of prices this shop
actually charged for the same item — each line cites the historical jobs
behind it. Anything ambiguous is flagged for a human, not priced by a guess.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Band:
    item: str
    description: str
    unit: str
    prices: list[float] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)

    @property
    def lo(self) -> float:
        return min(self.prices)

    @property
    def hi(self) -> float:
        return max(self.prices)

    @property
    def median(self) -> float:
        return round(statistics.median(self.prices), 2)


def load_history(directory: Path) -> tuple[dict[str, Band], list[dict]]:
    """Read every historical job -> ({item_key: Band}, [job records])."""
    bands: dict[str, Band] = {}
    jobs = []
    for path in sorted(directory.glob("*.json")):
        job = json.loads(path.read_text())
        jobs.append(job)
        for li in job.get("line_items", []):
            key = li["item"]
            band = bands.setdefault(key, Band(key, li.get("description", key),
                                              li.get("unit", "each")))
            band.prices.append(float(li.get("unit_price", 0)))
            if job.get("history_id"):
                band.refs.append(job["history_id"])
    return bands, jobs


# Keyword rules: plain-English description -> historical item key.
# Multi-word hits are confident; a lone generic word is only a suggestion.
KEYWORDS = {
    "wh_50_ng_install": ["water heater", "50 gal", "50-gal", "50 gallon",
                         "hot water tank"],
    "tankless_install": ["tankless"],
    "expansion_tank": ["expansion tank"],
    "haul_away": ["haul", "dispose", "remove the old", "old unit"],
    "angle_stops_supply": ["supply line", "shutoff", "angle stop"],
    "drain_pan": ["drain pan", "pan under"],
    "gas_line_upsize": ["gas line"],
    "seismic_straps": ["seismic", "strap"],
    "drain_auger": ["drain", "clog", "backup", "main line"],
    "furnace_tune": ["tune-up", "furnace tune", "maintenance"],
    "capacitor": ["capacitor"],
    "gfci": ["gfci", "outlet"],
    "panel_100a": ["panel upgrade", "100a", "service panel"],
    "ev_50a": ["ev charger", "car charger"],
    "flapper": ["flapper", "running toilet"],
}

# Item keys that are only ever an explicit human choice — never auto-matched.
NEVER_AUTO: set[str] = set()

# Items a single weak keyword can only *suggest* (human picks yes/no).
WEAK_SINGLE = {"tankless_install", "gfci", "drain_pan", "gas_line_upsize"}

REPLACE_RE = re.compile(r"\breplace|replacement|swap|leak|failed|dead\b", re.I)


def match_description(text: str, bands: dict[str, Band],
                      explicit_qty: dict[str, int] | None = None
                      ) -> tuple[list[dict], list[dict]]:
    """Plain-English description -> (matched, uncertain) item proposals.

    matched   — [{item, qty, reason}] confident hits, priced from history
    uncertain — [{item, reason}] ambiguous hits for a human to confirm
    """
    text_l = text.lower()
    matched, uncertain = [], []
    for item, keywords in KEYWORDS.items():
        if item not in bands or item in NEVER_AUTO:
            continue
        hits = [k for k in keywords if k in text_l]
        qty = (explicit_qty or {}).get(item)
        if not hits and not qty:
            continue
        entry = {"item": item, "qty": qty or 1,
                 "reason": f"keywords: {', '.join(hits)}" if hits
                          else "explicit quantity in job record"}
        if qty:
            matched.append(entry)      # the human already chose
        elif item in WEAK_SINGLE and len(hits) == 1:
            uncertain.append(entry)    # a suggestion, not a line item
        else:
            matched.append(entry)

    # Bundling rule learned from history: a replacement install nearly
    # always carries haul-away — add it automatically when the job is
    # clearly a swap and the history supports the item.
    install_keys = {"wh_50_ng_install", "tankless_install"}
    if (any(m["item"] in install_keys for m in matched)
            and REPLACE_RE.search(text)
            and "haul_away" in bands
            and not any(m["item"] == "haul_away" for m in matched)):
        matched.append({"item": "haul_away", "qty": 1,
                        "reason": "standard with a replacement — bundled in "
                                  + ", ".join(bands["haul_away"].refs[:3])})
    return matched, uncertain


def exploratory_for(matched: list[dict], history_jobs: list[dict],
                    text: str) -> list[str]:
    """Things nobody can know until work starts — gathered from the
    historical jobs that contained the matched items, plus the text."""
    items = {m["item"] for m in matched}
    found: list[str] = []
    for job in history_jobs:
        if items & {li["item"] for li in job.get("line_items", [])}:
            for e in job.get("exploratory", []):
                if e not in found:
                    found.append(e)
    if "tankless" in text.lower() and "wh_50_ng_install" in items:
        found.append("gas meter capacity for a tankless upgrade — utility "
                     "measures this, not us")
    year = re.search(r"\b(19|20)\d{2}\b", text)
    if year:
        age_note = (f"unit dated {year.group(0)} — condition of shutoff "
                    f"valve, supply lines, and venting unknown until the "
                    f"old unit is pulled")
        if age_note not in found:
            found.append(age_note)
    return found


def concerns_for(matched: list[dict], history_jobs: list[dict],
                 text: str) -> list[str]:
    """Plain-spoken risks about this specific project."""
    items = {m["item"] for m in matched}
    found: list[str] = []
    for job in history_jobs:
        if items & {li["item"] for li in job.get("line_items", [])}:
            for c in job.get("concerns", []):
                if c not in found:
                    found.append(c)
    text_l = text.lower()
    if "tankless" in text_l and "tankless_install" not in items:
        found.append("customer asked about tankless — a conversion adds "
                     "venting and possibly a gas-line upsize; quoted here "
                     "as a like-for-like swap, tankless priced separately "
                     "on request")
    if re.search(r"\b(19|20)\d{2}\b", text_l):
        found.append("unit is old enough that connected parts (valves, "
                     "fittings, vent sections) may fail when disturbed — "
                     "any added work follows the scope-change policy below")
    return found
