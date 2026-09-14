"""MockSolari — the same shape as SolariCore, but everything is local.

`demo.py --mock` uses this so the whole pipeline runs end to end with no API
keys: "browsers" are real HTTP fetches against the bundled mock portal (or
cached fixture pages), "sandboxes" are real local subprocesses running the
same worker scripts, and "desktops" record the actions a real computer-use
loop would take. Every step still lands in the run log, so you can see exactly
what a live run would do — the only difference is where the machines live.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .drivers import FixtureDriver, HttpDriver


class MockSession:
    """Stands in for a solari_browser BrowserSession."""

    def __init__(self, kind: str = "browser"):
        self.id = f"mock_{kind}_{uuid.uuid4().hex[:8]}"
        self.session = type("S", (), {"storage_state": None})()
        self.proxy = None
        self.contexts: list[Any] = []

    async def close(self) -> None:
        pass


class _MockCommands:
    """`sandbox.commands` backed by local subprocesses. argv, not shell."""

    def __init__(self, workdir: Path):
        self._workdir = workdir

    async def run(self, cmd: str, *, args: list[str] | None = None, timeout_ms: int | None = None, **_):
        argv = [cmd] + list(args or [])
        # Resolve "python3" to the interpreter running this process so the
        # local path runs the same stdlib-only workers the sandbox would.
        if cmd == "python3":
            argv[0] = sys.executable
        # Sandbox paths (/tmp/x) live under the mock workdir — remap argv
        # (but never the executable itself, which is a real host path).
        argv = argv[:1] + [
            str(self._workdir / a.lstrip("/")) if a.startswith("/") else a
            for a in argv[1:]
        ]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=(timeout_ms or 60_000) / 1000,
            cwd=self._workdir,
        )
        return type("CommandResult", (), {
            "exitCode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr
        })()


class _MockFiles:
    """`sandbox.files` backed by a temp directory."""

    def __init__(self, workdir: Path):
        self._workdir = workdir

    def _dest(self, path: str) -> Path:
        # /tmp/... lands inside the mock workdir, keeping runs hermetic.
        return self._workdir / path.lstrip("/")

    async def write(self, path: str, data: bytes | str, mode=None) -> None:
        dest = self._dest(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            dest.write_bytes(data)
        else:
            dest.write_text(data)


class MockSandbox:
    def __init__(self):
        self.id = f"mock_sandbox_{uuid.uuid4().hex[:8]}"
        self.sandboxId = self.id
        self._workdir = Path(tempfile.mkdtemp(prefix="trades-ai-mocksbx-"))
        self.commands = _MockCommands(self._workdir)
        self.files = _MockFiles(self._workdir)
        self.killed = False

    async def connect(self) -> None:
        pass

    async def kill(self) -> None:
        self.killed = True


class _MockMouse:
    def __init__(self, log: list[str]):
        self._log = log

    async def click(self, x: int, y: int, humanize: bool = False) -> None:
        self._log.append(f"mouse.click({x}, {y})")


class _MockKeyboard:
    def __init__(self, log: list[str]):
        self._log = log

    async def type(self, text: str) -> None:
        self._log.append(f"keyboard.type({text!r})")

    async def press(self, key: str) -> None:
        self._log.append(f"keyboard.press({key})")


class MockDesktop:
    """Records what a real computer-use loop would do."""

    def __init__(self):
        self.id = f"mock_desktop_{uuid.uuid4().hex[:8]}"
        self.sessionId = self.id
        self.streamUrl = f"mock://vnc/{self.id}"
        self.recordingUrl = f"mock://recording/{self.id}.mp4"
        self.actions: list[str] = []
        self.mouse = _MockMouse(self.actions)
        self.keyboard = _MockKeyboard(self.actions)

    async def connect(self) -> None:
        pass

    async def health(self):
        return type("Health", (), {"ready": True})()

    async def open(self, app: str) -> int:
        self.actions.append(f"open({app})")
        return 4242

    async def screenshot(self, format: str = "png") -> bytes:
        self.actions.append(f"screenshot({format})")
        return b"\x89PNG\r\n\x1a\n"  # PNG magic; real run returns a real frame

    async def close(self) -> None:
        self.actions.append("close()")


class MockSolari:
    """Drop-in for SolariCore in --mock mode.

    pages: {key: html} fixture map (procurement), or
    base_url: root of a locally served app (dispatch mock portal).
    """

    def __init__(self, pages: dict[str, str] | None = None, base_url: str = ""):
        self._pages = pages
        self._base_url = base_url
        self.sessions: list[str] = []

    def use_pages(self, pages: dict[str, str]) -> None:
        """Serve fixture HTML through browser() — the mock-only boundary.
        Keys are URL substrings; FixtureDriver matches 'key in url'."""
        self._pages = pages

    async def browser(self, *, profile_name=None, recording=True, stealth=False, proxy=None, captcha=False):
        session = MockSession("browser")
        self.sessions.append(session.id)
        if self._pages is not None:
            return session, FixtureDriver(self._pages)
        return session, HttpDriver(self._base_url)

    async def sandbox(self, template: str = "base", timeout_ms: int = 300_000) -> MockSandbox:
        sbx = MockSandbox()
        self.sessions.append(sbx.id)
        await sbx.connect()
        return sbx

    async def run_python_in_sandbox(self, script_path: str, script_arg_paths=None, timeout_ms=60_000) -> str:
        """Same contract as SolariCore.run_python_in_sandbox — but the
        'sandbox' is a local subprocess. Same worker, same argv, same stdout."""
        sbx = await self.sandbox()
        try:
            await sbx.files.write("/tmp/worker.py", Path(script_path).read_text())
            argv = ["/tmp/worker.py"]
            for local, remote in (script_arg_paths or {}).items():
                await sbx.files.write(remote, Path(local).read_bytes())
                argv.append(remote)
            out = await sbx.commands.run("python3", args=argv, timeout_ms=timeout_ms)
            if out.exitCode != 0:
                raise RuntimeError(f"mock sandbox worker exited {out.exitCode}: {out.stderr.strip()}")
            return out.stdout
        finally:
            await sbx.kill()

    async def desktop(self, resolution: str = "1280x720", record: bool = True, timeout_ms: int = 600_000) -> MockDesktop:
        desk = MockDesktop()
        self.sessions.append(desk.id)
        await desk.connect()
        return desk

    async def destroy_desktop(self, desktop: MockDesktop) -> None:
        await desktop.close()

    async def replay_url(self, session_id: str) -> str:
        return f"mock://replay/{session_id}"

    async def download_replay(self, session_id: str, dest_dir=None):
        """Write a canned replay artifact — in live mode this is real rrweb
        NDJSON downloaded from Solari."""
        blob = json.dumps({
            "type": 4,
            "data": {"href": "mock://session", "note": "mock replay — run --live for a real rrweb recording"},
            "timestamp": int(time.time() * 1000),
        }).encode() + b"\n"
        if dest_dir is not None:
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            path = Path(dest_dir) / f"{session_id}.replay.ndjson"
            path.write_bytes(blob)
            return path
        return blob

    async def aclose(self) -> None:
        pass
