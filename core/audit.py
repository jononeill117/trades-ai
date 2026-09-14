"""The run log — one JSONL file per run, under runs/.

Every use case writes the same event shape so a run can be replayed, grepped,
or pasted into a bug report. Each event is one line:

    {"ts": "...", "run": "...", "use_case": "dispatch", "step": "book",
     "status": "ok", "detail": {...}, "session_id": "...", "replay_url": "..."}

Session-recording replay URLs are first-class fields: the recording is both
the demo video and the audit trail, so it lives in the run log next to the
step that produced it.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .config import repo_root
from .cost import CostTracker


class RunLog:
    """Append-only JSONL log for one run of one use case."""

    def __init__(self, use_case: str, mode: str, runs_dir: Path | None = None):
        self.use_case = use_case
        self.mode = mode
        self.run_id = f"{use_case}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.runs_dir = runs_dir or repo_root() / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.runs_dir / f"{self.run_id}.jsonl"
        self.cost = CostTracker()
        self._emit("run_start", "ok", {"mode": mode})

    def step(self, name: str, status: str = "ok", **detail: Any) -> None:
        """Record a pipeline step. Prints a plain-English line too."""
        self._emit(name, status, detail)

    def usage(self, kind: str, session_id: str, seconds: float) -> None:
        """Record measured Solari usage: one session of `kind` lived for
        `seconds`. Feeds the run's cost summary and runs/cost-report.py."""
        rec = self.cost.record(kind, session_id, seconds)
        self._emit("usage", "ok", rec.to_dict())

    def time_session(self, kind: str, session_id: str):
        """Context manager: emits a `usage` event with elapsed seconds when
        the block exits — the easy way to meter a session's lifetime.

            session, page = await core.browser()
            with run_log.time_session("browser", session.id):
                ...drive it...
        """
        log = self

        class _Timer:
            def __enter__(self):
                self._start = time.monotonic()
                return self

            def __exit__(self, *exc):
                log.usage(kind, session_id, time.monotonic() - self._start)
                return False

        return _Timer()

    def session(
        self,
        kind: str,
        session_id: str,
        replay_url: str | None = None,
        stream_url: str | None = None,
    ) -> None:
        """Record a Solari session (browser/sandbox/desktop) + its replay."""
        self._emit(
            "session",
            "ok",
            {"kind": kind, "stream_url": stream_url},
            session_id=session_id,
            replay_url=replay_url,
        )

    def finish(self, status: str = "ok", **detail: Any) -> Path:
        summary = self.cost.summary()
        self._emit("cost_summary", "ok", summary)
        detail.setdefault("cost", summary)
        self._emit("run_finish", status, detail)
        return self.path

    def _emit(
        self,
        step: str,
        status: str,
        detail: dict[str, Any],
        session_id: str | None = None,
        replay_url: str | None = None,
    ) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run": self.run_id,
            "use_case": self.use_case,
            "step": step,
            "status": status,
            "detail": detail,
        }
        if session_id:
            event["session_id"] = session_id
        if replay_url:
            event["replay_url"] = replay_url
        with self.path.open("a") as fh:
            fh.write(json.dumps(event, default=str) + "\n")

        # Human-readable echo so `demo.py` narrates itself.
        tag = {"ok": "  ok", "skipped": "  --", "error": " ERR", "would": "plan"}.get(status, "    ")
        summary = _summarize(detail)
        print(f"[{tag}] {step:<16} {summary}", file=sys.stderr)
        if session_id:
            print(f"       session {session_id}")
        if replay_url:
            print(f"       replay  {replay_url}")


def _summarize(detail: dict[str, Any]) -> str:
    if not detail:
        return ""
    parts = []
    for key, value in detail.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, default=str)
            if len(value) > 120:
                value = value[:117] + "..."
        parts.append(f"{key}={value}")
    return " ".join(parts)
