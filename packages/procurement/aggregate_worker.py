"""Quote aggregation — runs INSIDE the sandbox boundary.

Self-contained (stdlib only) so it can be copied into a Solari sandbox:
takes a JSON payload on argv[1] — {parts, offers, baseline_supplier} — and
prints @@RESULT@@{quote}. Scraped supplier HTML is untrusted input; only the
normalized quote crosses back out.

Run directly:  python aggregate_worker.py payload.json
"""

from __future__ import annotations

import json
import sys

MARKER = "@@RESULT@@"


def aggregate(parts: list[dict], offers: list[dict], baseline_supplier: str) -> dict:
    """Pick the cheapest compliant offer per part; total it; compute savings
    vs the baseline supplier.

    Compliant = has a price, is in stock, and is not login-gated. A login-
    gated offer can't be trusted as 'the price', so it never wins — it's
    reported as an alternative with a note.
    """
    by_sku: dict[str, list[dict]] = {}
    for o in offers:
        by_sku.setdefault(o["part_sku"], []).append(o)

    lines = []
    total = 0.0
    baseline_total = 0.0
    for part in parts:
        sku = part["sku"]
        qty = int(part.get("qty", 1))
        candidates = by_sku.get(sku, [])
        priced = [o for o in candidates
                  if o.get("unit_price") is not None and o.get("in_stock", True)
                  and not o.get("login_required")]
        gated = [o for o in candidates if o.get("login_required")]

        winner = min(priced, key=lambda o: o["unit_price"], default=None)
        base_offer = next((o for o in priced if o["supplier"] == baseline_supplier), None)
        if base_offer is None and priced:
            # Baseline supplier had no usable price — compare against the
            # most expensive option so savings is honest (never inflated).
            base_offer = max(priced, key=lambda o: o["unit_price"])

        line_total = (winner["unit_price"] * qty) if winner else 0.0
        base_line = (base_offer["unit_price"] * qty) if base_offer else line_total
        note = ""
        if winner is None:
            note = "no compliant price found" + (" (all login-gated)" if gated else "")
        elif gated:
            note = "a login-gated supplier might beat this — sign in to check"

        lines.append({
            "part": part,
            "winner": winner,
            "offers": candidates,
            "line_total": round(line_total, 2),
            "baseline_total": round(base_line, 2),
            "savings": round(base_line - line_total, 2),
            "note": note,
        })
        total += line_total
        baseline_total += base_line

    return {
        "lines": lines,
        "total": round(total, 2),
        "baseline_total": round(baseline_total, 2),
        "savings": round(baseline_total - total, 2),
        "baseline_supplier": baseline_supplier,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: aggregate_worker.py <payload.json>", file=sys.stderr)
        return 2
    with open(argv[1]) as fh:
        payload = json.load(fh)
    quote = aggregate(payload["parts"], payload["offers"], payload["baseline_supplier"])
    print(MARKER + json.dumps(quote))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
