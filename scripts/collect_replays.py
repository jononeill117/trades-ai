#!/usr/bin/env python3
"""Collect Solari session replays for evidence.

Reads the newest run log per package under runs/, extracts browser session
ids, polls `GET /sessions/{id}/replay-url`, and downloads whatever replay
artifacts actually exist into docs/demo/.

Honesty rules baked in:
- `ReplayPending`/`ReplayUnavailable`/errors are reported, never faked.
- Only artifacts that were actually captured are written to docs/demo/.
- A `docs/demo/replays.json` manifest records the true status of every
  session we asked about.

Requires SOLARI_API_KEY in the environment (set by the live launcher).
In mock mode there is nothing to collect — mock replays are already
written next to the run logs.

Usage:
    SOLARI_API_KEY=... python scripts/collect_replays.py [runs_dir]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.solari_client import SolariCore  # noqa: E402


def latest_run_per_package(runs_dir: Path) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    import re
    for path in sorted(runs_dir.glob("*.jsonl")):
        try:
            first = json.loads(path.read_text().splitlines()[0])
        except (IndexError, json.JSONDecodeError):
            continue
        if (first.get("detail") or {}).get("mode") != "live":
            continue  # only live runs produce real session ids
        pkg = re.split(r"-\d{8}-", path.name)[0]
        latest[pkg] = path
    return latest


def browser_session_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        detail = event.get("detail") or {}
        if event.get("step") == "session" and detail.get("kind") == "browser":
            sid = event.get("session_id")
            if sid and sid not in ids:
                ids.append(sid)
    return ids


async def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    runs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "runs"
    demo_dir = repo / "docs" / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)

    client = SolariCore()
    manifest: dict[str, dict] = {}
    try:
        for pkg, log_path in latest_run_per_package(runs_dir).items():
            sids = browser_session_ids(log_path)
            if not sids:
                manifest[pkg] = {"run_log": str(log_path.name),
                                 "browser_sessions": 0, "replays": []}
                continue
            entries = []
            solari = await client._browser()
            for sid in sids:
                try:
                    url = (await solari.sessions.get_replay_url(sid)).url
                    url_err = None
                except Exception as err:  # noqa: BLE001 — record honestly
                    url, url_err = None, f"{type(err).__name__}: {err}"
                status = {"session_id": sid,
                          "replay_url": url,
                          "artifact": None}
                if url_err:
                    status["replay_url_error"] = url_err
                try:
                    artifact = await client.download_replay(sid, demo_dir)
                except Exception as err:  # noqa: BLE001 — record honestly
                    status["error"] = f"{type(err).__name__}: {err}"
                    artifact = None
                if artifact:
                    status["artifact"] = Path(artifact).name
                    status["status"] = "captured"
                else:
                    status["status"] = (
                        "url_only" if url else "unavailable_or_pending")
                entries.append(status)
                print(f"{pkg}: {sid[:24]}… -> {status['status']} "
                      f"{url or ''}")
            manifest[pkg] = {"run_log": log_path.name,
                             "browser_sessions": len(sids),
                             "replays": entries}
    finally:
        await client.aclose()

    manifest_path = demo_dir / "replays.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    captured = sum(
        1 for m in manifest.values() for r in m.get("replays", [])
        if r.get("status") == "captured")
    print(f"\nmanifest: {manifest_path}")
    print(f"captured replay artifacts: {captured}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
