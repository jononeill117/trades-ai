"""Sandbox worker — parse and normalize an untrusted AR aging CSV.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Reads the
CSV, validates/normalizes each row, and prints JSON after @@RESULT@@.
Malformed rows come back with warnings, not crashes — one bad export row
shouldn't kill the whole chase run.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys

MARKER = "@@RESULT@@"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_STATES = {"open", "aging", "contacted", "promised", "paid",
                "disputed", "escalated"}


def normalize_row(row: dict) -> dict:
    warnings = []
    inv = {
        "invoice_id": (row.get("invoice_id") or "").strip(),
        "customer": (row.get("customer") or "").strip(),
        "email": (row.get("email") or "").strip(),
        "phone": re.sub(r"[^\d+]", "", row.get("phone") or ""),
        "issued": (row.get("issued") or "").strip(),
        "due": (row.get("due") or "").strip(),
        "status": (row.get("status") or "open").strip().lower(),
        "amount": 0.0,
        "warnings": warnings,
    }
    if not inv["invoice_id"]:
        warnings.append("missing invoice_id")
    try:
        inv["amount"] = float(row.get("amount") or 0)
    except ValueError:
        warnings.append(f"unparseable amount {row.get('amount')!r}")
    if inv["email"] and not EMAIL_RE.match(inv["email"]):
        warnings.append(f"odd email {inv['email']!r}")
    for k in ("issued", "due"):
        if inv[k] and not re.match(r"^\d{4}-\d{2}-\d{2}$", inv[k]):
            warnings.append(f"odd {k} date {inv[k]!r}")
    if inv["status"] == "open":
        inv["status"] = "aging"
    elif inv["status"] not in VALID_STATES:
        warnings.append(f"unknown status {inv['status']!r} -> aging")
        inv["status"] = "aging"
    if not inv["email"] and not inv["phone"]:
        warnings.append("no contact channel")
    inv["warnings"] = warnings
    return inv


def main() -> None:
    text = open(sys.argv[1]).read()
    rows = [normalize_row(r) for r in csv.DictReader(io.StringIO(text))
            if any((v or "").strip() for v in r.values())]
    print(MARKER + json.dumps(rows))


if __name__ == "__main__":
    main()
