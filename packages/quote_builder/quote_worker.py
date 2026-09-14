"""Sandbox worker — assemble the quote from matched items + the pricebook.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Takes the
job record, the matched/uncertain item lists, and the pricebook; computes
line totals, subtotal, and tax. Prices come ONLY from the pricebook rows —
this worker has no other source of numbers. Prints JSON after @@RESULT@@.
"""

from __future__ import annotations

import csv
import io
import json
import sys

MARKER = "@@RESULT@@"


def main() -> None:
    job_path, items_path, book_path = sys.argv[1], sys.argv[2], sys.argv[3]
    job = json.loads(open(job_path).read())
    selection = json.loads(open(items_path).read())
    book = {r["item_id"]: r for r in csv.DictReader(open(book_path))
            if (r.get("item_id") or "").strip()}

    tax_rate = float(selection.get("tax_rate", 0))
    lines = []
    for sel in selection.get("matched", []):
        row = book.get(sel["item_id"])
        if row is None:
            lines.append({"item_id": sel["item_id"], "error": "not in pricebook"})
            continue
        qty = int(sel.get("qty", 1))
        unit = float(row["unit_price"])
        taxable = str(row.get("taxable", "true")).lower() in ("true", "1", "yes")
        lines.append({
            "item_id": row["item_id"], "description": row["description"],
            "qty": qty, "unit": row["unit"], "unit_price": unit,
            "taxable": taxable, "line_total": round(qty * unit, 2),
            "reason": sel.get("reason", ""),
        })
    subtotal = round(sum(l.get("line_total", 0) for l in lines), 2)
    taxable_sub = round(sum(l.get("line_total", 0) for l in lines
                            if l.get("taxable")), 2)
    tax = round(taxable_sub * tax_rate, 2)
    print(MARKER + json.dumps({
        "job": job, "lines": lines, "uncertain": selection.get("uncertain", []),
        "subtotal": subtotal, "taxable_subtotal": taxable_sub,
        "tax_rate": tax_rate, "tax": tax,
        "total": round(subtotal + tax, 2),
        "exclusions": selection.get("exclusions", []),
        "assumptions": selection.get("assumptions", []),
    }))


if __name__ == "__main__":
    main()
