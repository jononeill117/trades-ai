"""Desktop fallback — when the portal needs a GUI, not a browser.

Some field-service steps can't be done in a cloud browser: a native app, an
OS-level dialog, or (in our demo portal) the "dispatch board" that only
accepts confirmation from its own screen. The orchestrator hands these to a
Solari desktop — a real Linux machine with a screen, driven by screenshot +
mouse/keyboard (computer-use).

Recorded: desktops are created with `record=True`; the mp4 playback URL lands
in the run log.

Live mode opens the board page in the desktop's Chrome and presses Enter on
the focused CONFIRM button — our fixture board auto-focuses it. A production
computer-use loop would screenshot -> locate -> click instead; the handoff
pattern (and the audit trail) is the part that matters here.
"""

from __future__ import annotations

import asyncio
import time

from core.drivers import HttpDriver


async def confirm_on_dispatch_board(core, run_log, board_url: str, portal_base_url: str = "") -> str:
    """Confirm a pending_board job via a desktop session. Returns final status."""
    try:
        desk = await core.desktop(record=True)
    except Exception as e:
        # Solari accounts with a concurrency limit of 1 cannot hold the
        # portal sandbox and a desktop open at once. The job was already
        # created in the portal via the browser; the desktop confirmation
        # is a demo flourish, not a correctness requirement.
        if "concurrent" in str(e).lower() or "ConcurrencyLimit" in type(e).__name__:
            run_log.step("desktop_fallback", status="skipped",
                         reason="concurrency limit: portal sandbox already holds the single session")
            return "booked (desktop confirmation skipped — concurrency limit)"
        raise
    run_log.session("desktop", desk.sessionId,
                    replay_url=getattr(desk, "recordingUrl", None),
                    stream_url=getattr(desk, "streamUrl", None))
    desk_t0 = time.monotonic()
    try:
        # Mock desktop (no real screen): log the computer-use steps, then make
        # the state change real by driving the board over plain HTTP.
        if hasattr(desk, "actions"):
            await desk.open("chrome")
            await desk.mouse.click(640, 300, humanize=True)
            await desk.keyboard.press("Enter")
            await desk.screenshot()
            run_log.step("desktop_actions", actions=desk.actions)
            return await _confirm_over_http(board_url, portal_base_url)

        # Live: a real desktop. Wait for X11, open a browser on the board
        # page, press Enter on the auto-focused CONFIRM button, screenshot.
        for _ in range(30):
            health = await desk.health()
            if getattr(health, "ready", False):
                break
            await asyncio.sleep(1)
        found = await desk.exec(
            "sh", args=["-c", "command -v google-chrome || command -v chromium || command -v firefox"]
        )
        browser_bin = found.stdout.strip().splitlines()[0] if found.stdout.strip() else "google-chrome"
        await desk.exec("sh", args=["-c", f"nohup {browser_bin} '{board_url}' >/dev/null 2>&1 &"])
        await asyncio.sleep(6)
        await desk.keyboard.press("Enter")
        await asyncio.sleep(3)
        shot = await desk.screenshot(format="png")
        run_log.step("desktop_screenshot", bytes=len(shot))
        return "booked (unverified — check the recording)"
    finally:
        await core.destroy_desktop(desk)
        run_log.usage("desktop", desk.sessionId, time.monotonic() - desk_t0)


async def _confirm_over_http(board_url: str, portal_base_url: str) -> str:
    """Mock-mode confirmation: same clicks a desktop would make, over HTTP."""
    from urllib.parse import urlparse

    base = portal_base_url or f"{urlparse(board_url).scheme}://{urlparse(board_url).netloc}"
    driver = HttpDriver(base)
    await driver.goto("/login")
    await driver.fill("#email", "dispatcher@yourshop.example")
    await driver.fill("#password", "demo-password")
    await driver.click("#login-submit")
    await driver.goto(board_url)
    await driver.click("#board-confirm")
    return await driver.text("#job-status")
