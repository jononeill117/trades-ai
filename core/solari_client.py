"""The one Solari wrapper every use case shares.

Hides the fiddly bits of the Solari SDKs behind three verbs — `browser()`,
`sandbox()`, `desktop()` — so a use-case package reads like the workflow it
automates. Gotchas encoded here (learned the hard way, documented in
docs/architecture.md):

- Recording is OPT-IN PER SESSION at create time. `recording=True` is the
  default here — every session is auditable unless you explicitly say no.
- `browser.close()` releases the cloud session. Always call it (try/finally)
  or the slot stays held until the plan deadline.
- `sandbox.kill()` destroys the VM — `close()` only drops the local channel.
- A desktop needs `desktop.close()` AND `client.destroy(id)`.
- Replays upload asynchronously AFTER release — poll before giving up.
- A browser profile is not auto-applied: attach `profile_id`, then hand
  `browser.session.storage_state` to `new_context()`. And it is not
  auto-saved: call `save_profile_state()` when you're done.
- `proxy`/`captcha` require `stealth=True`.
- Sandbox commands are NOT shell-interpreted — argv goes in `args`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .config import env

DEFAULT_BASE_URL = "https://api.getsolari.com"
REPLAY_POLL_ATTEMPTS = 10
REPLAY_POLL_SECONDS = 3


class SolariCore:
    """Thin async wrapper over solari-browser / solari-sandbox / solari-desktop.

    Construct once per run, then `await core.aclose()` at the end.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or env("SOLARI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "SOLARI_API_KEY is not set. Live mode needs a key from "
                "https://console.getsolari.com — or run demo.py --mock instead."
            )
        self.base_url = env("SOLARI_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
        self._browser_client: Any = None
        self._sandbox_client: Any = None
        self._desktop_client: Any = None

    # -- browsers -------------------------------------------------------------

    async def _browser(self):
        if self._browser_client is None:
            from solari_browser import Solari

            region = env("SOLARI_REGION") or None
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if region:
                kwargs["region"] = region
            # base_url wins over region gateway-side; only pass it when the
            # deployer pointed at a non-default gateway.
            if env("SOLARI_BASE_URL") and self.base_url != DEFAULT_BASE_URL:
                kwargs["base_url"] = self.base_url
            self._browser_client = Solari(**kwargs)
        return self._browser_client

    async def get_or_create_profile(self, name: str):
        """Profiles hold cookies+localStorage server-side — log in once, then
        every later session starts already signed in."""
        solari = await self._browser()
        for p in await solari.profiles.list():
            if p.name == name:
                return p
        return await solari.profiles.create(name=name)

    async def browser(
        self,
        *,
        profile_name: str | None = None,
        recording: bool = True,
        stealth: bool = False,
        proxy: Any = None,
        captcha: bool = False,
    ):
        """Launch a cloud browser. Returns (session, page).

        The page comes from a context seeded with the profile's storage state
        when a profile is attached — that seeding is NOT automatic.
        Caller owns `await session.close()`.
        """
        solari = await self._browser()
        profile_id = None
        if profile_name:
            profile_id = (await self.get_or_create_profile(profile_name)).id
        # The TS SDK accepts shorthand strings ("us", "smart"); the Python SDK
        # sends proxy verbatim unless it's a ProxyRequest, so wrap 2-letter
        # country codes here — keeps callers (and the layering rule) SDK-free.
        if isinstance(proxy, str) and len(proxy) == 2:
            from solari_browser import ProxyRequest

            proxy = ProxyRequest(country=proxy)
        session = await solari.launch(
            profile_id=profile_id,
            recording=recording,
            stealth=stealth,
            proxy=proxy,
            captcha=captcha,
        )
        storage_state = getattr(session.session, "storage_state", None)
        ctx_kwargs: dict[str, Any] = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        # A hand-built context doesn't inherit the pool's timezone pin; pass it
        # through when a managed proxy set one so Intl/Date match the egress IP.
        tz = getattr(getattr(session, "proxy", None), "timezone_id", None)
        if tz:
            ctx_kwargs["timezone_id"] = tz
        context = await session.new_context(**ctx_kwargs)
        page = await context.new_page()
        return session, page

    async def save_profile_state(self, session: Any, profile_name: str) -> None:
        """Persist whatever the session accumulated (logins, cookies)."""
        solari = await self._browser()
        profile = await self.get_or_create_profile(profile_name)
        # contexts is a method on the real BrowserSession, a plain list on
        # MockSession — handle both.
        ctxs = getattr(session, "contexts", None)
        ctxs = ctxs() if callable(ctxs) else ctxs
        context = ctxs[0] if ctxs else None
        if context is None:
            return
        state = await context.storage_state()
        await solari.profiles.save(profile.id, state)

    async def login_handoff_url(self, session_id: str, reason: str) -> dict:
        """Mint a human-takeover link for a live browser session.

        No SDK method exists for this yet — it's plain HTTP:
        POST /sessions/:id/handoff  (see cookbook browser-login-handoff).
        """
        import urllib.request
        import json as _json

        req = urllib.request.Request(
            f"{self.base_url}/sessions/{session_id}/handoff",
            method="POST",
            data=_json.dumps({"reason": reason}).encode(),
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            return _json.loads(res.read())

    async def save_profile_from_session(self, session_id: str, profile_name: str) -> None:
        """Save the live session's state into a profile — captures whatever a
        human did during a login handoff."""
        import urllib.request
        import json as _json

        profile = await self.get_or_create_profile(profile_name)
        req = urllib.request.Request(
            f"{self.base_url}/sessions/{session_id}/save-profile",
            method="POST",
            data=_json.dumps({"profileId": profile.id}).encode(),
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            res.read()

    # -- replays ---------------------------------------------------------------

    async def replay_url(self, session_id: str) -> Optional[str]:
        solari = await self._browser()
        try:
            return (await solari.sessions.get_replay_url(session_id)).url
        except Exception:
            return None

    async def download_replay(self, session_id: str, dest_dir=None) -> Optional[Any]:
        """Fetch the rrweb replay for a released session; saves it under
        docs/demo/ when dest_dir is given. Polls — the upload lands a few
        seconds after the session is released."""
        from solari_browser.errors import SolariError

        solari = await self._browser()
        for _ in range(REPLAY_POLL_ATTEMPTS):
            await asyncio.sleep(REPLAY_POLL_SECONDS)
            try:
                blob = await solari.sessions.download_replay(session_id)
            except SolariError as err:
                if getattr(err, "status", None) == 404:
                    continue
                raise
            if dest_dir is not None:
                from pathlib import Path

                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                path = Path(dest_dir) / f"{session_id}.replay.ndjson"
                path.write_bytes(blob)
                return path
            return blob
        return None

    # -- sandboxes --------------------------------------------------------------

    async def _sandbox(self):
        if self._sandbox_client is None:
            from solari_sandbox import SandboxClient

            self._sandbox_client = SandboxClient(api_key=self.api_key, base_url=self.base_url)
        return self._sandbox_client

    async def sandbox(self, template: str = "base", timeout_ms: int = 5 * 60_000):
        """Create a sandbox and connect its control channel.
        Caller owns `await sandbox.kill()` — that is what destroys the VM."""
        client = await self._sandbox()
        sbx = await client.create(template=template, timeout_ms=timeout_ms)
        await sbx.connect()
        return sbx

    async def run_python_in_sandbox(
        self, script_path: str, script_arg_paths: dict[str, str] | None = None, timeout_ms: int = 60_000
    ) -> str:
        """Copy a stdlib-only Python script (+ input files) into a fresh
        sandbox, run it, return stdout. This is the untrusted-code boundary:
        whatever the script reads (untrusted email, scraped HTML) is processed
        inside a microVM, not on this machine.

        NOTE: `commands.run` is not shell-interpreted — args go in argv.
        """
        from pathlib import Path

        sbx = await self.sandbox()
        try:
            await sbx.files.write("/tmp/worker.py", Path(script_path).read_text())
            argv = ["/tmp/worker.py"]
            for local, remote in (script_arg_paths or {}).items():
                await sbx.files.write(remote, Path(local).read_bytes())
                argv.append(remote)
            out = await sbx.commands.run("python3", args=argv, timeout_ms=timeout_ms)
            if out.exitCode != 0:
                raise RuntimeError(f"sandbox worker exited {out.exitCode}: {out.stderr.strip()}")
            return out.stdout
        finally:
            await sbx.kill()

    # -- desktops ----------------------------------------------------------------

    async def _desktop(self):
        if self._desktop_client is None:
            from solari_desktop import DesktopClient

            self._desktop_client = DesktopClient(api_key=self.api_key, base_url=self.base_url)
        return self._desktop_client

    async def desktop(self, resolution: str = "1280x720", record: bool = True, timeout_ms: int = 10 * 60_000):
        """Create a desktop (a sandbox with a screen) and connect.
        Caller owns cleanup: `await desktop.close()` then
        `await client.destroy(desktop.sessionId)` — both, not either."""
        client = await self._desktop()
        desk = await client.create(
            template="default", resolution=resolution, record=record, timeout_ms=timeout_ms
        )
        await desk.connect()
        return desk

    async def destroy_desktop(self, desktop) -> None:
        client = await self._desktop()
        await desktop.close()
        await client.destroy(desktop.sessionId)

    # -- lifecycle ------------------------------------------------------------

    async def aclose(self) -> None:
        for client in (self._browser_client, self._sandbox_client, self._desktop_client):
            if client is None:
                continue
            for method in ("aclose", "close"):
                fn = getattr(client, method, None)
                if fn:
                    res = fn()
                    if asyncio.iscoroutine(res):
                        await res
                    break
