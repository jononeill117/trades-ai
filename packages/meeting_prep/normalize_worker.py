"""Sandbox worker — normalize untrusted job-export records.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Accepts a
JSON job list, coerces fields, keeps every source field needed for
traceability, prints normalized JSON after @@RESULT@@.
"""

from __future__ import annotations

import json
import re
import sys

MARKER = "@@RESULT@@"
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def norm(d: dict) -> dict:
    clean = {k: CONTROL_RE.sub(" ", str(v)).strip() if isinstance(v, str) else v
             for k, v in d.items()}
    try:
        clean["amount"] = float(clean.get("amount") or 0)
    except (ValueError, TypeError):
        clean["amount"] = 0.0
    for k in ("job_id", "tech", "trade", "date", "customer", "summary",
              "notes", "status"):
        clean.setdefault(k, "")
    return clean


def main() -> None:
    data = json.loads(open(sys.argv[1]).read())
    print(MARKER + json.dumps([norm(d) for d in data]))


if __name__ == "__main__":
    main()
