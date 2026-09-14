"""Idempotency — a tiny processed-events state file.

Re-running the same missed-call event must not send a second text or book a
second job. We persist processed call ids under out/; a retry sees the id and
skips. This is deliberately a file, not a DB — same contract as the rest of
the toolkit.
"""

from __future__ import annotations

import json
from pathlib import Path


class ProcessedState:
    def __init__(self, path: Path):
        self.path = path
        self.ids: set[str] = set()
        if path.exists():
            try:
                self.ids = set(json.loads(path.read_text()).get("processed", []))
            except json.JSONDecodeError:
                self.ids = set()

    def seen(self, event_id: str) -> bool:
        return event_id in self.ids

    def mark(self, event_id: str) -> None:
        self.ids.add(event_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"processed": sorted(self.ids)}, indent=2))
