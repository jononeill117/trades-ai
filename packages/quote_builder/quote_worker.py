"""Sandbox worker — assemble the quote from matched items + price bands.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Input:
the job record, the matched/uncertain item lists, and the price bands
computed from this shop's quote history. Every line is priced at the
median of its historical band (and clamped inside the band) — this worker
has no other source of numbers. Prints JSON after @@RESULT@@.
"""

from __future__ import annotations

import json
import statistics
import sys

MARKER = "@@RESULT@@"

# Backstop: a quote never ships with an empty exclusions list, even if the
# config is cleared. These are the exclusions nearly every job carries.
DEFAULT_EXCLUSIONS = [
    "Permits and inspection fees unless listed as a line item",
    "Drywall, paint, flooring, or finish repair",
    "Utility-side work (meter, service drop, main) — the utility schedules "
    "their own work",
    "Pre-existing code violations unrelated to this scope",
    "Anything hidden behind walls or underground until it is exposed",
]


def main() -> None:
    job_path, sel_path, bands_path = sys.argv[1], sys.argv[2], sys.argv[3]
    job = json.loads(open(job_path).read())
    sel = json.loads(open(sel_path).read())
    bands = json.loads(open(bands_path).read())

    lines = []
    for m in sel.get("matched", []):
        band = bands.get(m["item"])
        if band is None or not band.get("prices"):
            lines.append({"item": m["item"], "error": "no price history"})
            continue
        qty = int(m.get("qty", 1))
        price = round(statistics.median(band["prices"]), 2)
        price = min(max(price, min(band["prices"])), max(band["prices"]))
        lines.append({
            "item": m["item"], "description": band["description"],
            "qty": qty, "unit": band["unit"], "unit_price": price,
            "line_total": round(qty * price, 2),
            "reason": m.get("reason", ""),
            "band": {"lo": min(band["prices"]), "hi": max(band["prices"])},
            "priced_from": sorted(set(band.get("refs", []))),
        })
    subtotal = round(sum(l.get("line_total", 0) for l in lines), 2)
    tax_rate = float(sel.get("tax_rate", 0))
    tax = round(subtotal * tax_rate, 2)

    exclusions = [e for e in (sel.get("exclusions") or []) if str(e).strip()]
    if not exclusions:
        exclusions = list(DEFAULT_EXCLUSIONS)

    print(MARKER + json.dumps({
        "job": job, "lines": lines, "uncertain": sel.get("uncertain", []),
        "subtotal": subtotal, "tax_rate": tax_rate, "tax": tax,
        "total": round(subtotal + tax, 2),
        "inclusions": sel.get("inclusions", []),
        "exclusions": exclusions,
        "exploratory": sel.get("exploratory", []),
        "concerns": sel.get("concerns", []),
        "terms": sel.get("terms", {}),
    }))


if __name__ == "__main__":
    main()
