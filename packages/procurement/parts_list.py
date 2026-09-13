"""Load a parts list from CSV or JSON.

CSV columns: sku, description, qty   (a header row is expected)
JSON: a list of {"sku": ..., "description": ..., "qty": ...}
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Part


def load_parts(path: Path | str) -> list[Part]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        return [Part(sku=r["sku"], description=r["description"],
                     qty=int(r.get("qty", 1)), requirements=r.get("requirements", {}))
                for r in data]
    with path.open(newline="") as fh:
        return [
            Part(sku=row["sku"].strip(), description=row["description"].strip(),
                 qty=int(row.get("qty") or 1))
            for row in csv.DictReader(fh)
            if row.get("sku")
        ]
