"""Aggregate step — run the pricing math inside the sandbox boundary."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .aggregate_worker import MARKER
from .models import Offer, Part, QuoteLine

WORKER = Path(__file__).parent / "aggregate_worker.py"


def _payload(parts: list[Part], offers: list[Offer], baseline_supplier: str) -> dict:
    from dataclasses import asdict

    return {
        "parts": [asdict(p) for p in parts],
        "offers": [o.to_dict() for o in offers],
        "baseline_supplier": baseline_supplier,
    }


async def aggregate_in_sandbox(core, parts: list[Part], offers: list[Offer],
                               baseline_supplier: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(_payload(parts, offers, baseline_supplier), tmp)
        tmp_path = tmp.name
    stdout = await core.run_python_in_sandbox(str(WORKER), {tmp_path: "/tmp/payload.json"})
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"aggregate worker produced no marker; stdout={stdout!r}")
    return json.loads(stdout[idx + len(MARKER):])


def aggregate_locally(parts: list[Part], offers: list[Offer], baseline_supplier: str) -> dict:
    """In-process aggregation — used by unit tests."""
    from .aggregate_worker import aggregate

    p = _payload(parts, offers, baseline_supplier)
    return aggregate(p["parts"], p["offers"], p["baseline_supplier"])


def to_quote_lines(quote: dict) -> list[QuoteLine]:
    out = []
    for line in quote["lines"]:
        out.append(QuoteLine(
            part=Part(**{k: v for k, v in line["part"].items() if k in Part.__dataclass_fields__}),
            winner=Offer(**line["winner"]) if line["winner"] else None,
            offers=[Offer(**o) for o in line["offers"]],
            line_total=line["line_total"],
            baseline_total=line["baseline_total"],
            savings=line["savings"],
            note=line["note"],
        ))
    return out
