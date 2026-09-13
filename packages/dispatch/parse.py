"""Parse step — send the untrusted email through the sandbox boundary.

`core` is a SolariCore (live: a real microVM) or MockSolari (mock: a local
subprocess running the identical worker). Either way, raw email content is
never parsed by the orchestrator itself — only the normalized JSON the worker
prints after @@RESULT@@ comes back.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import RawEmail, WorkOrder
from .parse_worker import MARKER

WORKER = Path(__file__).parent / "parse_worker.py"


async def parse_email_in_sandbox(core, eml_path: Path) -> WorkOrder:
    """Run parse_worker inside a sandbox on the raw .eml file."""
    stdout = await core.run_python_in_sandbox(str(WORKER), {str(eml_path): "/tmp/order.eml"})
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"parse worker produced no result marker; stdout={stdout!r}")
    return WorkOrder.from_dict(json.loads(stdout[idx + len(MARKER):]))


def parse_email_locally(raw: RawEmail) -> WorkOrder:
    """Direct, in-process parse — used by unit tests."""
    from .parse_worker import parse_work_order

    eml = f"From: {raw.sender}\nSubject: {raw.subject}\nMessage-ID: {raw.message_id}\n\n{raw.body}"
    return WorkOrder.from_dict(parse_work_order(eml))
