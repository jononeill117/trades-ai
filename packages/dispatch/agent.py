"""Dispatch agent — work-order email in, booked job out.

Pipeline:
    ingest -> parse (sandbox) -> schedule -> book (cloud browser)
           -> confirm (browser, or desktop fallback) -> notify -> audit

`core` is SolariCore in --live mode or MockSolari in --mock mode; the
pipeline is identical, only where the machines live changes.
"""

from __future__ import annotations

import tempfile
import time
from datetime import datetime
from pathlib import Path

from core.audit import RunLog
from core.config import env, load_yaml, repo_root
from core.drivers import as_driver
from core.notify import GmailNotifier, QuoNotifier, SlackNotifier

from . import desktop_fallback, ingest, mock_portal_server
from .models import NeedsDesktopError, WorkOrder
from .parse import parse_email_in_sandbox
from .schedule import load_availability, propose_slots
from .portals import get_adapter


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "dispatch.yaml")


def _inbox(cfg: dict):
    if cfg.get("inbox", "fixture") == "gmail":
        return ingest.GmailInbox(
            env("GMAIL_USER", "") or "",
            env("GMAIL_APP_PASSWORD", "") or "",
            subject_filter=cfg.get("gmail_subject_filter", "Work Order"),
        )
    return ingest.FixtureInbox(repo_root() / cfg.get("fixture_dir", "fixtures/work_orders"))


async def _portal_url(core, cfg: dict, run_log: RunLog) -> tuple[str, object | None]:
    """Where the portal lives. Returns (base_url, keepalive).

    mock portal: mock mode serves it on localhost; live mode runs it inside a
    Solari sandbox and returns the public preview URL (a real cloud browser
    can't reach your localhost, so the portal goes to the cloud instead).
    """
    if cfg.get("portal", "mock") == "mock":
        seed = repo_root() / cfg.get("portal_seed", "fixtures/mock_portal/seed.json")
        if cfg.get("mode") == "live":
            sbx = await core.sandbox()
            run_log.session("sandbox", sbx.sandboxId)
            url = await mock_portal_server.serve_in_sandbox(sbx, seed)
            run_log.step("portal_up", url=url)
            return url, (sbx, time.monotonic())
        server, url = mock_portal_server.serve(seed)
        run_log.step("portal_up", url=url)
        return url, server
    return cfg.get("portal_base_url", ""), None


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    results: dict = {"orders": [], "booked": 0, "skipped": 0}
    keepalive = None
    try:
        # -- ingest -------------------------------------------------------------
        inbox = _inbox(cfg)
        raw_emails = inbox.fetch()
        run_log.step("ingest", count=len(raw_emails), source=type(inbox).__name__)

        availability = load_availability(load_yaml(repo_root() / cfg["availability"]))

        # -- parse (sandbox boundary) --------------------------------------
        # Parse ALL emails before starting the portal: each parse spins up
        # its own sandbox (killed afterwards), and the portal itself holds a
        # sandbox open. Solari accounts with a concurrency limit of 1 cannot
        # have both alive at once, so parsing is a separate phase up front.
        parsed: list[tuple] = []
        for raw in raw_emails:
            entry: dict = {"message_id": raw.message_id}
            results["orders"].append(entry)

            if raw.path:
                eml_path = Path(raw.path)
            else:
                tmp = tempfile.NamedTemporaryFile("w", suffix=".eml", delete=False)
                tmp.write(raw.to_eml())
                tmp.close()
                eml_path = Path(tmp.name)

            t0 = time.monotonic()
            order: WorkOrder = await parse_email_in_sandbox(core, eml_path)
            run_log.usage("sandbox", f"parse:{order.source_id}", time.monotonic() - t0)
            entry["order"] = order.to_dict()
            run_log.step("parse", source_id=order.source_id, trade=order.trade,
                         priority=order.priority, warnings=order.warnings)
            parsed.append((raw, order, entry))

        portal_url, keepalive = await _portal_url(core, cfg, run_log)
        adapter = get_adapter(cfg.get("portal", "mock"), portal_url)

        for raw, order, entry in parsed:
            if order.needs_review:
                run_log.step("route", status="skipped",
                             reason="needs human review", warnings=order.warnings)
                results["skipped"] += 1
                entry["status"] = "needs_review"
                continue

            # -- schedule --------------------------------------------------------
            slots = propose_slots(order, availability, now=datetime.now())
            if not slots:
                run_log.step("schedule", status="skipped", reason="no open slot in horizon")
                results["skipped"] += 1
                entry["status"] = "no_slot"
                continue
            slot = slots[0]
            entry["slot"] = {"tech": slot.tech, "start": slot.start, "end": slot.end}
            run_log.step("schedule", tech=slot.tech, start=slot.start, end=slot.end,
                         proposals=len(slots))

            # -- book (cloud browser) ---------------------------------------------
            # The browser session is closed BEFORE any desktop fallback: Solari
            # accounts with a low concurrency limit cannot hold the portal
            # sandbox + browser + desktop open at once.
            session, page = await core.browser(
                profile_name=cfg.get("portal_profile"), recording=True
            )
            run_log.session("browser", session.id)
            needs_desktop = False
            browser_t0 = time.monotonic()
            try:
                driver = as_driver(page)
                signed_in = await adapter.login(driver)
                run_log.step("portal_login", signed_in=signed_in)
                job_ref = await adapter.create_job(driver, order, slot)
                entry["job_ref"] = job_ref
                run_log.step("create_job", ref=job_ref)
                try:
                    status = await adapter.confirm_booking(driver, job_ref)
                except NeedsDesktopError:
                    # GUI-only step -> deferred to desktop fallback below,
                    # after the browser session is closed.
                    needs_desktop = True
                    status = None
                entry["status"] = status
            finally:
                await session.close()
                run_log.usage("browser", session.id, time.monotonic() - browser_t0)
                replay = await core.replay_url(session.id)
                run_log.session("browser", session.id, replay_url=replay)
                entry["replay_url"] = replay
            if needs_desktop:
                # GUI-only step -> Solari desktop computer-use fallback.
                run_log.step("confirm", status="would", via="desktop-fallback")
                board_url = job_ref.rstrip("/") + "/board"
                status = await desktop_fallback.confirm_on_dispatch_board(
                    core, run_log, board_url, portal_url
                )
                entry["status"] = status
            run_log.step("confirm", status="ok" if str(entry["status"]).startswith("booked") else "error",
                         job_status=entry["status"])
            if str(entry["status"]).startswith("booked"):
                results["booked"] += 1

            # -- notify -------------------------------------------------------------
            summary = (
                f"Booked {order.source_id}: {order.trade} for {order.customer_name} "
                f"@ {order.site_address} — {slot.label()}. Run: {run_log.run_id}"
            )
            slack = SlackNotifier()
            res = await slack.send(":calendar: " + summary)
            run_log.step("notify_slack", status=res.status, detail=res.detail)

            gmail = GmailNotifier()
            to = order.tenant_email or cfg.get("customer_fallback_email", "")
            res = await gmail.send(
                to or "customer@example.com",
                f"Your {order.trade} appointment is booked",
                f"Hi {order.tenant_name or 'there'},\n\nYou're booked for "
                f"{slot.label()} at {order.site_address}.\n\n— Your service team",
                sender=env("DISPATCH_REPLY_FROM") or env("GMAIL_USER"),
            )
            run_log.step("notify_email", status=res.status, detail=res.detail)

            quo = QuoNotifier()
            res = await quo.send(
                order.tenant_phone or "+15555550100",
                f"Your {order.trade} visit is booked: {slot.label()}",
            )
            run_log.step("notify_sms", status=res.status, detail=res.detail)

        run_log.finish("ok", booked=results["booked"], skipped=results["skipped"])
        return results
    finally:
        if keepalive is not None:
            # live mode: (sandbox, started_at) hosting the mock portal —
            # kill() destroys the VM; mock mode: a local HTTP server.
            if isinstance(keepalive, tuple):
                sbx, t0 = keepalive
                await sbx.kill()
                run_log.usage("sandbox", sbx.sandboxId, time.monotonic() - t0)
            else:
                keepalive.shutdown()
